package main

import (
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
)

func safetyFixture(t *testing.T, settings string) (config, string) {
	t.Helper()
	c := config{Experiences: []experience{{Name: "google", Model: &struct {
		Name        string `json:"name"`
		ID          string `json:"modelId"`
		DisplayName string `json:"displayName"`
	}{ID: "gemini-3.8-flash"}}, {Name: "claude", Model: &struct {
		Name        string `json:"name"`
		ID          string `json:"modelId"`
		DisplayName string `json:"displayName"`
	}{ID: "claude-sonnet-4-6"}}}}
	path := filepath.Join(t.TempDir(), "settings.json")
	if err := os.WriteFile(path, []byte(settings), 0600); err != nil {
		t.Fatal(err)
	}
	return c, path
}

func TestGoogleSafetyDefaultsAndOtherModelsPreserveBytes(t *testing.T) {
	body := []byte(`{"contents":[{"text":"private input"}],"safetySettings":[{"category":"HARM_CATEGORY_HARASSMENT","threshold":"BLOCK_ONLY_HIGH"}],"entitlement":{"tier":"free"}}`)
	c, path := safetyFixture(t, `{"google_safety_settings":{},"tool_calling":{"parallel":true},"reasoning":{"forward_summary":true}}`)
	for _, item := range []struct{ selected, path string }{{"google", path}, {"google", ""}, {"claude", path}, {"unknown", path}} {
		out, _, err := applyGoogleSafety(body, c, item.selected, item.path)
		if err != nil || string(out) != string(body) {
			t.Fatal(string(out), err)
		}
	}
	_ = os.WriteFile(path, []byte(`{"google_safety_settings":{"HARM_CATEGORY_HARASSMENT":"OFF"}}`), 0600)
	out, policy, err := applyGoogleSafety(body, c, "claude", path)
	if err != nil || policy != nil || string(out) != string(body) {
		t.Fatal("Google settings applied to Claude", err)
	}
}

func TestGoogleSafetyMergesCategoriesAndReloadsFile(t *testing.T) {
	body := []byte(`{"contents":[{"text":"private input"}],"safetySettings":[{"category":"HARM_CATEGORY_HARASSMENT","threshold":"BLOCK_ONLY_HIGH","method":"PROBABILITY"},{"category":"FUTURE_CATEGORY","threshold":"OFF"}],"entitlement":{"tier":"free"},"generationConfig":{"maxOutputTokens":512}}`)
	c, path := safetyFixture(t, `{"google_safety_settings":{"HARM_CATEGORY_HARASSMENT":"BLOCK_NONE","HARM_CATEGORY_HATE_SPEECH":"OFF"}}`)
	out, policy, err := applyGoogleSafety(body, c, "google", path)
	if err != nil {
		t.Fatal(err)
	}
	var before, after map[string]any
	_ = json.Unmarshal(body, &before)
	_ = json.Unmarshal(out, &after)
	for _, key := range []string{"contents", "entitlement", "generationConfig"} {
		if !reflect.DeepEqual(before[key], after[key]) {
			t.Fatal("unrelated request changed", key)
		}
	}
	entries := after["safetySettings"].([]any)
	if len(entries) != 3 || entries[0].(map[string]any)["threshold"] != "BLOCK_NONE" || entries[0].(map[string]any)["method"] != "PROBABILITY" || entries[1].(map[string]any)["category"] != "FUTURE_CATEGORY" {
		t.Fatal(entries)
	}
	writeGoogleSafetyReceipt(path, "google", policy, 200, out)
	var receipt map[string]any
	b, _ := os.ReadFile(path + ".receipt.json")
	_ = json.Unmarshal(b, &receipt)
	if receipt["settings_sha256"] != policy.fingerprint() || len(receipt["request_safety_settings"].([]any)) != 3 {
		t.Fatal(receipt)
	}
	if _, found := receipt["contents"]; found {
		t.Fatal("receipt retained model input")
	}
	_ = os.WriteFile(path, []byte(`{"google_safety_settings":{"HARM_CATEGORY_HARASSMENT":"BLOCK_LOW_AND_ABOVE"}}`), 0600)
	out, _, err = applyGoogleSafety(body, c, "google", path)
	_ = json.Unmarshal(out, &after)
	if err != nil || after["safetySettings"].([]any)[0].(map[string]any)["threshold"] != "BLOCK_LOW_AND_ABOVE" {
		t.Fatal("policy did not reload", err)
	}
}

func TestGoogleSafetyRejectsUnsupportedSettings(t *testing.T) {
	for _, settings := range []string{`null`, `{"google_safety_settings":null}`, `{"google_safety_settings":{"HARM_CATEGORY_UNKNOWN":"OFF"}}`, `{"google_safety_settings":{"HARM_CATEGORY_HARASSMENT":"none"}}`, `{"typo":{}}`, `{"google_safety_settings":{"HARM_CATEGORY_HARASSMENT":false}}`, `{} {}`, `{"google_safety_settings":[]}`} {
		c, path := safetyFixture(t, settings)
		if _, _, err := applyGoogleSafety([]byte(`{}`), c, "google", path); err == nil {
			t.Fatal("accepted invalid policy", settings)
		}
	}
}

func TestAdapterForwardsPolicyWithoutChangingAuthAndResponse(t *testing.T) {
	c, path := safetyFixture(t, `{"google_safety_settings":{"HARM_CATEGORY_HARASSMENT":"BLOCK_NONE","HARM_CATEGORY_HATE_SPEECH":"BLOCK_NONE","HARM_CATEGORY_SEXUALLY_EXPLICIT":"BLOCK_NONE","HARM_CATEGORY_DANGEROUS_CONTENT":"BLOCK_NONE"}}`)
	a := newAdapter("secret", true)
	a.googleSafetyFile = path
	a.client.Transport = roundTrip(func(r *http.Request) (*http.Response, error) {
		if r.Header.Get("Authorization") != "Bearer test" || r.URL.Host != "aicode.googleapis.com" {
			t.Fatal("authentication changed")
		}
		if r.URL.Path == "/v1alpha:fetchConfig" {
			b, _ := json.Marshal(c)
			return response(200, string(b)), nil
		}
		b, _ := io.ReadAll(r.Body)
		var request map[string]any
		_ = json.Unmarshal(b, &request)
		if len(request["safetySettings"].([]any)) != 4 {
			t.Fatal(request)
		}
		for _, item := range request["safetySettings"].([]any) {
			if item.(map[string]any)["threshold"] != "BLOCK_NONE" {
				t.Fatal(item)
			}
		}
		resp := response(200, "data: {\"text\":\"unchanged\"}\n\n")
		resp.Header.Set("Content-Type", "text/event-stream")
		return resp, nil
	})
	r := httptest.NewRequest("POST", "http://localhost/secret/v1alpha:streamGenerateContent?alt=sse", strings.NewReader(`{"aicode":{"experience":"google"},"contents":[{"text":"neutral"}]}`))
	r.Header.Set("Authorization", "Bearer test")
	w := httptest.NewRecorder()
	a.ServeHTTP(w, r)
	if w.Code != 200 || w.Body.String() != "data: {\"text\":\"unchanged\"}\n\n" {
		t.Fatal(w.Code, w.Body.String())
	}
	if _, err := os.Stat(path + ".receipt.json"); err != nil {
		t.Fatal("no forwarding receipt", err)
	}
}
