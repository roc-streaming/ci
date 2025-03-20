package main

import (
	"bytes"
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
)

const enableEncryption = true

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
	if enableEncryption && !ok {
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
	ghSecret := os.Getenv("GH_SECRET")
	ghToken := os.Getenv("GH_TOKEN")

	if enableEncryption {
		ghSecret, err = decryptAesCbc(ghSecret, ghKey)
		if err != nil {
			return makeErr(http.StatusForbidden, "can't decrypt GH_SECRET")
		}
		ghToken, err = decryptAesCbc(ghToken, ghKey)
		if err != nil {
			return makeErr(http.StatusForbidden, "can't decrypt GH_TOKEN")
		}
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
	if enableEncryption && !verifyHmac(body, ghSignature, ghSecret) {
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

	// pull request or issue number (depending on event type)
	var number float64

	if strings.HasPrefix(ghEvent, "pull_request") {
		pullreq, ok := payload["pull_request"].(map[string]any)
		if !ok {
			return makeErr(http.StatusBadRequest, "bad request: missing body.pull_request")
		}
		number, ok = pullreq["number"].(float64)
		if !ok {
			return makeErr(http.StatusBadRequest, "bad request: missing body.pull_request.number")
		}
		if number <= 0 {
			return makeErr(http.StatusBadRequest, "bad request: invalid body.pull_request.number")
		}
	} else {
		issue, ok := payload["issue"].(map[string]any)
		if !ok {
			return makeErr(http.StatusBadRequest, "bad request: missing body.issue")
		}
		number, ok = issue["number"].(float64)
		if !ok {
			return makeErr(http.StatusBadRequest, "bad request: missing body.issue.number")
		}
		if number <= 0 {
			return makeErr(http.StatusBadRequest, "bad request: invalid body.issue.number")
		}
	}

	// filter out unneeded events to avoid spamming
	dispEvent := ""

	switch ghEvent {
	case "pull_request":
		switch ghAction {
		case "opened", "reopened", "closed", "synchronize",
			"ready_for_review", "converted_to_draft",
			"review_requested", "review_request_removed":
			dispEvent = "pull_request_" + ghAction
		}
	case "pull_request_review":
		switch ghAction {
		case "submitted", "edited", "dismissed":
			dispEvent = "pull_request_review_" + ghAction
		}
	case "issue":
		switch ghAction {
		case "opened", "reopened", "closed",
			"labeled", "unlabeled":
			dispEvent = "issue_" + ghAction
		}
	}

	if dispEvent == "" {
		return makeErr(http.StatusAccepted,
			"ignoring request: unsupported event %s/%s", ghEvent, ghAction)
	}

	// build repository_dispatch request
	dispReqURL := fmt.Sprintf("https://api.github.com/repos/%s/dispatches", repoName)

	dispReqBody, _ := json.Marshal(map[string]any{
		"event_type": dispEvent,
		"client_payload": map[string]any{
			"number": number,
		},
	})

	dispReq, _ := http.NewRequest("POST", dispReqURL, bytes.NewReader(dispReqBody))
	dispReq.Header.Set("Authorization", "Bearer "+ghToken)
	dispReq.Header.Set("Accept", "application/vnd.github.v3+json")
	dispReq.Header.Set("Content-Type", "application/json")

	// send request
	dispResp, err := http.DefaultClient.Do(dispReq)
	if err != nil {
		return makeErr(http.StatusBadGateway, "dispatch request failed: "+err.Error())
	}
	defer dispResp.Body.Close()

	dispRespBody, _ := io.ReadAll(dispResp.Body)

	statusCode := dispResp.StatusCode
	if statusCode == http.StatusNoContent {
		statusCode = http.StatusOK
	}

	// forward response and some info to caller
	return map[string]any{
		"statusCode": statusCode,
		"body": map[string]any{
			"event":  ghEvent,
			"action": ghAction,
			"repo":   repoName,
			"number": number,
			"dispatch": map[string]any{
				"url":             dispReqURL,
				"request":         dispReqBody,
				"response_status": dispResp.Status,
				"response_body":   string(dispRespBody),
			},
		},
	}
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
