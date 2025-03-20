//go:build stub

package main

import (
	"encoding/json"
	"fmt"
	"os"
	"strings"
)

// stub for local testing
func main() {
	envBytes, err := os.ReadFile(".env")
	if err != nil {
		fmt.Fprintf(os.Stderr, "error reading .env: %s\n", err)
		os.Exit(1)
	}
	for _, line := range strings.Split(string(envBytes), "\n") {
		line = strings.TrimSpace(line)
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}
		parts := strings.SplitN(line, "=", 2)
		if len(parts) != 2 {
			continue
		}
		key := strings.TrimSpace(parts[0])
		value := strings.TrimSpace(parts[1])
		os.Setenv(key, value)
	}

	var input map[string]any
	decoder := json.NewDecoder(os.Stdin)
	if err := decoder.Decode(&input); err != nil {
		fmt.Fprintf(os.Stderr, "error decoding input: %s\n", err)
		os.Exit(1)
	}

	result := Main(input)

	encoder := json.NewEncoder(os.Stdout)
	if err := encoder.Encode(result); err != nil {
		fmt.Fprintf(os.Stderr, "error encoding output: %s\n", err)
		os.Exit(1)
	}
}
