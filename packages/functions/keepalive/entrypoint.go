package main

import (
	"crypto/aes"
	"crypto/cipher"
	"crypto/hmac"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"strings"

	"golang.org/x/crypto/pbkdf2"
	"gopkg.in/yaml.v3"
)

const enableVerification = true

func Main(args map[string]any) map[string]any {
	httpArg, ok := args["http"].(map[string]any)
	if !ok {
		return makeErr(http.StatusBadRequest, "bad request: missing http")
	}

	// parse headers
	headers, ok := httpArg["headers"].(map[string]any)
	if !ok {
		return makeErr(http.StatusBadRequest, "bad request: missing http.headers")
	}

	ghSignature, ok := headers["x-hub-signature-256"].(string)
	if enableVerification && !ok {
		return makeErr(http.StatusBadRequest,
			"bad request: missing http.headers.x-hub-signature-256")
	}

	ghEvent, ok := headers["x-github-event"].(string)
	if !ok {
		return makeErr(http.StatusBadRequest,
			"bad request: missing http.headers.x-github-event")
	}

	// parse query
	queryStr, ok := httpArg["queryString"].(string)
	if !ok {
		return makeErr(http.StatusBadRequest, "bad request: missing http.queryString")
	}

	query, err := url.ParseQuery(queryStr)
	if err != nil {
		return makeErr(http.StatusBadRequest,
			"bad request: can't decode http.queryString: %s", err)
	}

	ghKey := query.Get("key")
	if ghKey == "" {
		return makeErr(http.StatusBadRequest, "bad request: missing http.queryString.key")
	}

	// decrypt .env using ?key=... from github
	ghSecret, err := decryptAesCbc(os.Getenv("GH_SECRET"), ghKey)
	if err != nil {
		return makeErr(http.StatusForbidden, "can't decrypt GH_SECRET")
	}
	ghToken, err := decryptAesCbc(os.Getenv("GH_TOKEN"), ghKey)
	if err != nil {
		return makeErr(http.StatusForbidden, "can't decrypt GH_TOKEN")
	}

	// get body
	body, ok := httpArg["body"].(string)
	if !ok {
		return makeErr(http.StatusBadRequest, "bad request: missing http.body")
	}

	isBase64, _ := httpArg["isBase64Encoded"].(bool)
	if isBase64 {
		b, err := base64.StdEncoding.DecodeString(body)
		if err != nil {
			return makeErr(http.StatusBadRequest,
				"bad request: can't decode http.body: %s", err)
		}
		body = string(b)
	}

	// verify body signature with decrypted github secret
	if enableVerification && !verifyHmac(body, ghSignature, ghSecret) {
		return makeErr(http.StatusForbidden, "can't validate http.body")
	}

	// parse body
	var payload map[string]any
	if err := json.Unmarshal([]byte(body), &payload); err != nil {
		return makeErr(http.StatusBadRequest, "bad request: can't parse http.body: %s", err)
	}

	ghAction, ok := payload["action"].(string)
	if !ok {
		return makeErr(http.StatusBadRequest, "bad request: missing body.action")
	}

	repo, ok := payload["repository"].(map[string]any)
	if !ok {
		return makeErr(http.StatusBadRequest, "bad request: missing body.repository")
	}

	repoName, ok := repo["full_name"].(string)
	if !ok {
		return makeErr(http.StatusBadRequest, "bad request: missing body.repository.full_name")
	}

	// safety check
	if !strings.HasPrefix(repoName, "roc-streaming/") {
		return makeErr(http.StatusBadRequest, "bad request: unexpected repository")
	}

	// filter events
	if ghEvent != "workflow_run" || ghAction != "completed" {
		return makeErr(http.StatusAccepted,
			"ignoring request: unsupported event %s/%s", ghEvent, ghAction)
	}

	enabledWorkflows, err := keepaliveWorkflows(repoName, ghToken)
	if err != nil {
		return makeErr(http.StatusBadGateway, "workflow keepalive failed: %s", err)
	}

	// forward response and some info to caller
	return map[string]any{
		"body": map[string]any{
			"event":             ghEvent,
			"action":            ghAction,
			"repo":              repoName,
			"enabled_workflows": enabledWorkflows,
		},
	}
}

// github REST API
type workflow struct {
	ID    int    `json:"id"`
	Name  string `json:"name"`
	State string `json:"state"`
	Path  string `json:"path"`
}

type workflowList struct {
	Workflows []workflow `json:"workflows"`
}

// Iterate each workflow in repo.
// If workflow is enabled or auto-disabled, enable it.
// Skip workflows in other states (e.g. disabled manually).
// Enabling workflow restarts 60-day inactivity timer, even if workflow is already enabled.
// Given that workflow is scheduled more frequently than 60 days, and this function is
// invoked via a webhook after each run, it will effectively prevent workflow from being
// auto-disabled even if there were no new commits.
// This especially makes sense for repos that are updated rarely (like bindings), but
// which we want to keep green.
func keepaliveWorkflows(repo, token string) ([]string, error) {
	enabledWorkflows := []string{}

	workflows, err := readWorkflowList(repo, token)
	if err != nil {
		return nil, fmt.Errorf("can't get workflow list: %w", err)
	}

	for _, workflow := range workflows {
		if workflow.State != "active" && workflow.State != "disabled_inactivity" {
			continue
		}

		if !strings.HasPrefix(workflow.Path, ".github/workflows/") {
			continue
		}

		scheduled, err := isWorkflowScheduled(repo, token, workflow.ID)
		if err != nil {
			return nil, fmt.Errorf("can't check workflow: %w", err)
		}
		if !scheduled {
			continue
		}

		if err := enableWorkflow(repo, token, workflow.ID); err != nil {
			return nil, fmt.Errorf("can't enable workflow: %w", err)
		}
		enabledWorkflows = append(enabledWorkflows, workflow.Name)
	}

	return enabledWorkflows, nil
}

func readWorkflowList(repo, token string) ([]workflow, error) {
	url := fmt.Sprintf("https://api.github.com/repos/%s/actions/workflows", repo)

	respBody, err := githubRequest("GET", url, token, nil)
	if err != nil {
		return nil, fmt.Errorf("failed to retrieve workflow list: %w", err)
	}

	var response workflowList
	err = json.Unmarshal(respBody, &response)
	if err != nil {
		return nil, fmt.Errorf("failed to parse workflow list: %w", err)
	}

	return response.Workflows, nil
}

func isWorkflowScheduled(repo, token string, workflowID int) (bool, error) {
	url := fmt.Sprintf("https://api.github.com/repos/%s/actions/workflows/%d",
		repo, workflowID)

	workflowResp, err := githubRequest("GET", url, token, nil)
	if err != nil {
		return false, fmt.Errorf("failed to get workflow: %w", err)
	}

	var workflow workflow
	if err := json.Unmarshal(workflowResp, &workflow); err != nil {
		return false, fmt.Errorf("failed to parse workflow: %w", err)
	}

	content, err := readWorkflowContent(repo, token, workflow.Path)
	if err != nil {
		return false, err
	}

	on, exists := content["on"]
	if !exists {
		return false, nil
	}
	if str, ok := on.(string); ok { // "on: schedule"
		hasSchedule := str == "schedule"
		return hasSchedule, nil
	}
	if onMap, ok := on.(map[string]any); ok { // "on: {schedule: ...}"
		_, hasSchedule := onMap["schedule"]
		return hasSchedule, nil
	}

	return false, nil
}

func readWorkflowContent(repo, token, path string) (map[string]any, error) {
	url := fmt.Sprintf("https://api.github.com/repos/%s/contents/%s", repo, path)

	contentResp, err := githubRequest("GET", url, token, nil)
	if err != nil {
		return nil, fmt.Errorf("failed to get file content: %w", err)
	}

	var content map[string]any
	if err := yaml.Unmarshal(contentResp, &content); err != nil {
		return nil, fmt.Errorf("failed to parse content response: %w", err)
	}

	return content, nil
}

func enableWorkflow(repo, token string, workflowID int) error {
	url := fmt.Sprintf("https://api.github.com/repos/%s/actions/workflows/%d/enable",
		repo, workflowID)

	_, err := githubRequest("PUT", url, token, nil)
	return err
}

func githubRequest(method, url, token string, body io.Reader) ([]byte, error) {
	req, err := http.NewRequest(method, url, body)
	if err != nil {
		return nil, fmt.Errorf("http request failed: %w", err)
	}

	req.Header.Add("Authorization", "Bearer "+token)
	req.Header.Add("Accept", "application/vnd.github.v3+json")
	req.Header.Add("Accept", "application/vnd.github.v3.raw")

	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		return nil, fmt.Errorf("http request failed: %w", err)
	}

	defer resp.Body.Close()

	respBody, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, fmt.Errorf("http request failed: %w", err)
	}

	if resp.StatusCode != http.StatusOK && resp.StatusCode != http.StatusNoContent {
		return nil, fmt.Errorf("http request failed with code %s: %s",
			resp.Status, string(respBody))
	}

	return respBody, nil
}

func verifyHmac(ghBody, ghBodySignature, ghSecret string) bool {
	mac := hmac.New(sha256.New, []byte(ghSecret))

	mac.Write([]byte(ghBody))
	expectedBodySignature := "sha256=" + hex.EncodeToString(mac.Sum(nil))

	return hmac.Equal([]byte(ghBodySignature), []byte(expectedBodySignature))
}

func decryptAesCbc(encryptedData, passphrase string) (string, error) {
	if encryptedData == "" {
		return "", fmt.Errorf("empty data")
	}

	if passphrase == "" {
		return "", fmt.Errorf("empty key")
	}

	ciphertext, err := base64.StdEncoding.DecodeString(encryptedData)
	if err != nil {
		return "", fmt.Errorf("can't decode base64: %w", err)
	}

	if len(ciphertext) < 16 || string(ciphertext[0:8]) != "Salted__" {
		return "", fmt.Errorf("can't parse cipher")
	}
	salt := ciphertext[8:16]
	ciphertext = ciphertext[16:]

	// 48 = 32 bytes for key + 16 bytes for IV
	keyiv := pbkdf2.Key([]byte(passphrase), salt, 10000, 48, sha256.New)
	key, iv := keyiv[:32], keyiv[32:]

	block, err := aes.NewCipher(key)
	if err != nil {
		return "", fmt.Errorf("can't create cipher: %w", err)
	}

	mode := cipher.NewCBCDecrypter(block, iv)
	plaintext := make([]byte, len(ciphertext))
	mode.CryptBlocks(plaintext, ciphertext)

	padLen := int(plaintext[len(plaintext)-1])
	if padLen > len(plaintext) {
		return "", fmt.Errorf("invalid padding")
	}

	result := string(plaintext[:len(plaintext)-padLen])
	result = strings.TrimSpace(result)

	return result, nil
}

func makeErr(status int, message string, args ...any) map[string]any {
	formattedMessage := message
	if len(args) > 0 {
		formattedMessage = fmt.Sprintf(message, args...)
	}
	return map[string]any{
		"statusCode": status,
		"body": map[string]any{
			"error": formattedMessage,
		},
	}
}
