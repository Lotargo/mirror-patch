package main

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"os"
	"path/filepath"
	"strings"
	"time"
)

const googleSafetyEnv = "AGY_GOOGLE_SAFETY_SETTINGS_FILE"

var googleSafetyCategories = []string{"HARM_CATEGORY_HARASSMENT", "HARM_CATEGORY_HATE_SPEECH", "HARM_CATEGORY_SEXUALLY_EXPLICIT", "HARM_CATEGORY_DANGEROUS_CONTENT"}

type googleSafetyPolicy struct {
	Settings map[string]string `json:"google_safety_settings"`
	// Server-owned bridge options are not upstream provider settings.
	Tools     json.RawMessage `json:"tool_calling,omitempty"`
	Reasoning json.RawMessage `json:"reasoning,omitempty"`
}

func readGoogleSafetyPolicy(path string) (googleSafetyPolicy, error) {
	file, err := os.Open(path)
	if err != nil {
		return googleSafetyPolicy{}, errors.New("cannot read Google safety settings file")
	}
	defer file.Close()
	b, err := io.ReadAll(io.LimitReader(file, 8193))
	if err != nil || len(b) > 8192 {
		return googleSafetyPolicy{}, errors.New("Google safety settings file exceeds 8 KiB or cannot be read")
	}
	var policy googleSafetyPolicy
	trimmed := bytes.TrimSpace(b)
	if len(trimmed) == 0 || trimmed[0] != '{' {
		return policy, errors.New("expected Google safety settings object")
	}
	var root map[string]json.RawMessage
	if json.Unmarshal(b, &root) != nil {
		return policy, errors.New("invalid Google safety settings JSON")
	}
	if raw, ok := root["google_safety_settings"]; ok {
		raw = bytes.TrimSpace(raw)
		if len(raw) == 0 || raw[0] != '{' {
			return policy, errors.New("google_safety_settings must be an object")
		}
	}
	decoder := json.NewDecoder(bytes.NewReader(b))
	decoder.DisallowUnknownFields()
	if decoder.Decode(&policy) != nil || decoder.Decode(new(any)) != io.EOF {
		return policy, errors.New("invalid Google safety settings JSON")
	}
	for category, threshold := range policy.Settings {
		known := false
		for _, allowed := range googleSafetyCategories {
			known = known || category == allowed
		}
		if !known {
			return policy, errors.New("unsupported Google safety category")
		}
		switch threshold {
		case "DEFAULT", "HARM_BLOCK_THRESHOLD_UNSPECIFIED", "BLOCK_LOW_AND_ABOVE", "BLOCK_MEDIUM_AND_ABOVE", "BLOCK_ONLY_HIGH", "BLOCK_NONE", "OFF":
		default:
			return policy, errors.New("unsupported Google safety threshold")
		}
	}
	normalized := map[string]string{}
	for _, category := range googleSafetyCategories {
		normalized[category] = "DEFAULT"
	}
	for category, threshold := range policy.Settings {
		normalized[category] = threshold
	}
	policy.Settings = normalized
	return policy, nil
}

func (p googleSafetyPolicy) fingerprint() string {
	b, _ := json.Marshal(p.Settings)
	sum := sha256.Sum256(b)
	return hex.EncodeToString(sum[:])
}

func (c config) googleExperience(selected string) bool {
	for _, e := range c.Experiences {
		if e.Name != selected || e.Model == nil {
			continue
		}
		if strings.HasPrefix(strings.ToLower(e.Name), "chat-gemini-") {
			return true
		}
		for _, name := range []string{e.Model.ID, e.Model.Name} {
			name = strings.ToLower(name)
			if at := strings.LastIndex(name, "/"); at >= 0 {
				name = name[at+1:]
			}
			if strings.HasPrefix(name, "gemini-") {
				return true
			}
		}
	}
	return false
}

// The policy is process opt-in, applies only to Google experiences, and is read
// for each new upstream request. It never alters monitor/permission settings.
func applyGoogleSafety(body []byte, c config, selected, path string) ([]byte, *googleSafetyPolicy, error) {
	if path == "" || !c.googleExperience(selected) {
		return body, nil, nil
	}
	policy, err := readGoogleSafetyPolicy(path)
	if err != nil {
		return nil, nil, err
	}
	explicit := map[string]string{}
	for category, threshold := range policy.Settings {
		if threshold != "DEFAULT" {
			explicit[category] = threshold
		}
	}
	if len(explicit) == 0 {
		return body, &policy, nil
	}
	var request map[string]json.RawMessage
	if json.Unmarshal(body, &request) != nil {
		return nil, nil, errors.New("invalid Google generation request")
	}
	var settings []map[string]json.RawMessage
	if raw, ok := request["safetySettings"]; ok && json.Unmarshal(raw, &settings) != nil {
		return nil, nil, errors.New("invalid native safetySettings array")
	}
	seen := map[string]bool{}
	for _, setting := range settings {
		var category string
		_ = json.Unmarshal(setting["category"], &category)
		if threshold, ok := explicit[category]; ok {
			setting["threshold"], _ = json.Marshal(threshold)
			seen[category] = true
		}
	}
	for _, category := range googleSafetyCategories {
		if threshold, ok := explicit[category]; ok && !seen[category] {
			cat, _ := json.Marshal(category)
			value, _ := json.Marshal(threshold)
			settings = append(settings, map[string]json.RawMessage{"category": cat, "threshold": value})
		}
	}
	request["safetySettings"], _ = json.Marshal(settings)
	result, err := json.Marshal(request)
	return result, &policy, err
}

// A forwarding receipt records configuration and HTTP status, not model text
// or a claim that the upstream service applied a particular filtering behavior.
func writeGoogleSafetyReceipt(path, selected string, policy *googleSafetyPolicy, status int, body []byte) {
	if policy == nil || path == "" {
		return
	}
	value := map[string]any{"google_safety_settings": policy.Settings, "settings_sha256": policy.fingerprint(), "experience": selected, "upstream_http_status": status, "forwarded_at": time.Now().UTC().Format(time.RFC3339Nano)}
	var request map[string]json.RawMessage
	_ = json.Unmarshal(body, &request)
	var raw []map[string]any
	_ = json.Unmarshal(request["safetySettings"], &raw)
	settings := []any{}
	for _, entry := range raw {
		out := map[string]string{}
		for _, key := range []string{"category", "threshold", "method"} {
			if v, ok := entry[key].(string); ok && len(v) <= 100 {
				out[key] = v
			}
		}
		settings = append(settings, out)
	}
	value["request_safety_settings"] = settings
	b, _ := json.Marshal(value)
	target := path + ".receipt.json"
	file, err := os.CreateTemp(filepath.Dir(target), ".google-safety-receipt-*")
	if err != nil {
		return
	}
	name := file.Name()
	defer os.Remove(name)
	if _, err = file.Write(b); err != nil {
		file.Close()
		return
	}
	if file.Close() != nil {
		return
	}
	_ = os.Rename(name, target)
}
