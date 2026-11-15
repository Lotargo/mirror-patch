package main

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
)

const sample = `{"availableExperiences":[
 {"name":"future-high","model":{"name":"models/future","modelId":"future","displayName":"Future High"}},
 {"name":"future-medium","model":{"modelId":"future","displayName":"Future Medium"}},
 {"name":"other","model":{"modelId":"other-model"}},
 {"name":"hidden","model":{"modelId":"hidden"}}, {"name":"disabled","model":{"displayName":"Disabled"}}],
 "agySurfaceConfig":{"agentExperiences":{"experienceNames":["disabled","future-medium","future-high","other"]}}}`

func testConfig(t *testing.T) config {
	t.Helper()
	c, err := decodeConfig([]byte(sample))
	if err != nil {
		t.Fatal(err)
	}
	return c
}

func TestSelectionUsesServerOrderAndExplicitChoice(t *testing.T) {
	c := testConfig(t)
	for _, tc := range []struct {
		want     string
		explicit bool
		expected string
	}{
		{"chat-gemini-3.1-pro-low", false, "future-medium"}, {"future-high", true, "future-high"},
		{"models/future-medium", true, "future-medium"}, {"Future High", true, "future-high"}, {"other-model", true, "other"},
		{"hidden", true, "hidden"}, {"hidden", false, "hidden"},
	} {
		got, err := c.selectExperience(tc.want, tc.explicit)
		if err != nil || got != tc.expected {
			t.Fatalf("%+v: %q %v", tc, got, err)
		}
	}
	for _, want := range []string{"disabled", "unknown", "future"} {
		if _, err := c.selectExperience(want, true); err == nil {
			t.Fatalf("accepted unavailable or ambiguous model %q", want)
		}
	}
	if _, err := c.selectExperience("unknown-sdk-rpc-model", false); err == nil {
		t.Fatal("RPC model silently fell back without a CLI model flag")
	}
	if _, err := c.selectExperience("tab_missing", false); err == nil || !strings.Contains(err.Error(), "Tab model") {
		t.Fatal("unpublished Tab model did not report its unsupported route", err)
	}
	c.Surface.Agent.Names = []string{"future-high", "future-medium"}
	if got, _ := c.selectExperience("chat-gemini-3.1-pro-low", false); got != "future-high" {
		t.Fatal("server configuration change was ignored")
	}
}

func TestPredictionCatalogIncludesLiteAndImagesOutsideAgentPicker(t *testing.T) {
	c, err := decodeConfig([]byte(`{"availableExperiences":[
        {"name":"lite","model":{"modelId":"flash-lite"}},
        {"name":"image","model":{"name":"models/flash-image"}},
        {"name":"agent","model":{"modelId":"flash"}},
        {"name":"disabled","model":{"displayName":"Disabled"}}],
        "agySurfaceConfig":{"agentExperiences":{"experienceNames":["agent"]}}}`))
	if err != nil {
		t.Fatal(err)
	}
	for _, name := range []string{"lite", "image", "agent"} {
		if got, err := c.selectExperience(name, true); err != nil || got != name {
			t.Fatal(name, got, err)
		}
	}
	if got, _ := c.selectExperience("", false); got != "agent" {
		t.Fatal("default ignored UI order", got)
	}
	if _, err := c.selectExperience("disabled", true); err == nil {
		t.Fatal("disabled model accepted")
	}
	c.Surface.Agent.Names = nil
	if got, _ := c.selectExperience("", false); got != "lite" {
		t.Fatal("non-agent catalog rejected", got)
	}
}

func TestGenerationPreservesUnknownFieldsAndEntitlement(t *testing.T) {
	original := `{"aicode":{"experience":"future-high","futureOption":true},"entitlement":{"userTier":"free-tier"},"contents":[{"text":"private prompt"}],"futureField":{"n":3}}`
	out, selected, err := patchRequest([]byte(original), testConfig(t), true)
	if err != nil || selected != "future-high" {
		t.Fatal(selected, err)
	}
	var before, after map[string]any
	_ = json.Unmarshal([]byte(original), &before)
	_ = json.Unmarshal(out, &after)
	for _, key := range []string{"entitlement", "contents", "futureField"} {
		a, _ := json.Marshal(before[key])
		b, _ := json.Marshal(after[key])
		if string(a) != string(b) {
			t.Fatalf("changed %s", key)
		}
	}
	if after["aicode"].(map[string]any)["futureOption"] != true {
		t.Fatal("unknown aicode setting was lost")
	}
	for _, invalid := range []string{`null`, `[]`, `{}`, `{"aicode":false}`} {
		if _, _, err := patchRequest([]byte(invalid), testConfig(t), false); err == nil {
			t.Fatalf("accepted %s", invalid)
		}
	}
}

type roundTrip func(*http.Request) (*http.Response, error)

func (f roundTrip) RoundTrip(r *http.Request) (*http.Response, error) { return f(r) }
func response(status int, body string) *http.Response {
	return &http.Response{StatusCode: status, Header: http.Header{"Content-Type": []string{"application/json"}}, Body: io.NopCloser(strings.NewReader(body))}
}

func TestAdapterAuthenticatedConfigAndStreaming(t *testing.T) {
	a := newAdapter("secret", true)
	var mu sync.Mutex
	configCalls := 0
	a.client.Transport = roundTrip(func(r *http.Request) (*http.Response, error) {
		if r.URL.Scheme != "https" || r.URL.Host != "aicode.googleapis.com" || r.Header.Get("Authorization") != "Bearer test" {
			t.Errorf("wrong upstream or authentication")
		}
		if r.URL.Path == "/v1alpha:fetchConfig" {
			mu.Lock()
			configCalls++
			mu.Unlock()
			return response(200, sample), nil
		}
		b, _ := io.ReadAll(r.Body)
		if !strings.Contains(string(b), `"experience":"future-high"`) {
			t.Errorf("wrong experience: %s", b)
		}
		resp := response(200, "data: {\"answer\":\"ok\"}\n\n")
		resp.Header.Set("Content-Type", "text/event-stream")
		return resp, nil
	})
	server := httptest.NewServer(a)
	defer server.Close()
	for i := 0; i < 2; i++ {
		req, _ := http.NewRequest("POST", server.URL+"/secret/v1alpha:streamGenerateContent?alt=sse", strings.NewReader(`{"aicode":{"experience":"future-high"}}`))
		req.Header.Set("Authorization", "Bearer test")
		resp, err := http.DefaultClient.Do(req)
		if err != nil {
			t.Fatal(err)
		}
		b, _ := io.ReadAll(resp.Body)
		resp.Body.Close()
		if resp.StatusCode != 200 || string(b) != "data: {\"answer\":\"ok\"}\n\n" || resp.Header.Get("Content-Type") != "text/event-stream" {
			t.Fatal(resp.StatusCode, string(b))
		}
	}
	mu.Lock()
	defer mu.Unlock()
	if configCalls != 1 {
		t.Fatal("config not cached per session", configCalls)
	}
}

func TestAdapterFailsClosedAndPreservesLicenses(t *testing.T) {
	a := newAdapter("secret", true)
	calls := 0
	a.client.Transport = roundTrip(func(r *http.Request) (*http.Response, error) {
		calls++
		if strings.HasSuffix(r.URL.Path, "fetchConfig") {
			return response(403, `{"error":"denied"}`), nil
		}
		return response(403, `{"error":{"message":"license required"}}`), nil
	})
	for _, path := range []string{"/wrong/v1alpha:fetchLicenses", "/secret/https://evil.test", "/secret/v1alpha:fetchLicenses?redirect=evil"} {
		req := httptest.NewRequest("GET", path, nil)
		req.Header.Set("Authorization", "Bearer test")
		w := httptest.NewRecorder()
		a.ServeHTTP(w, req)
		if w.Code != 404 {
			t.Fatal(w.Code)
		}
	}
	if calls != 0 {
		t.Fatal("untrusted path reached upstream")
	}
	for _, path := range []string{"/secret/v1alpha:fetchLicenses", "/secret/v1alpha:streamGenerateContent"} {
		method := "GET"
		if strings.Contains(path, "streamGenerate") {
			method = "POST"
		}
		req := httptest.NewRequest(method, path, strings.NewReader(`{"aicode":{"experience":"future-high"}}`))
		req.Header.Set("Authorization", "Bearer test")
		w := httptest.NewRecorder()
		a.ServeHTTP(w, req)
		if method == "GET" && (w.Code != 403 || w.Body.String() != `{"error":{"message":"license required"}}`) {
			t.Fatal("license response changed")
		}
		if method == "POST" && (w.Code != 502 || !strings.Contains(w.Body.String(), "HTTP 403")) {
			t.Fatal("generation attempted without config", w.Body.String())
		}
	}
	if calls != 2 {
		t.Fatal(calls)
	}
}

func TestConfigCacheSeparatesAccounts(t *testing.T) {
	a := newAdapter("secret", false)
	calls := 0
	a.client.Transport = roundTrip(func(r *http.Request) (*http.Response, error) { calls++; return response(200, sample), nil })
	for _, auth := range []string{"Bearer A", "Bearer A", "Bearer B"} {
		h := http.Header{}
		h.Set("Authorization", auth)
		if _, err := a.getConfig(context.Background(), h); err != nil {
			t.Fatal(err)
		}
	}
	if calls != 2 {
		t.Fatal(calls)
	}
}

func TestEnvironmentAndExplicitModel(t *testing.T) {
	env := overrideEnv([]string{"use_aicode=false", "Path=preserved", "NO_PROXY=old"}, map[string]string{"USE_AICODE": "true", "NO_PROXY": "new"})
	joined := strings.Join(env, "\n")
	if strings.Contains(joined, "false") || strings.Contains(joined, "old") || !strings.Contains(joined, "Path=preserved") {
		t.Fatal(env)
	}
	for _, args := range [][]string{{"--model", "abc"}, {"--model=abc"}, {"-m", "abc"}} {
		if !explicitModel(args) {
			t.Fatal(args)
		}
	}
	if explicitModel([]string{"--print", "prompt mentions --model"}) || explicitModel([]string{"--", "--model"}) {
		t.Fatal("prompt mistaken for flag")
	}
}
