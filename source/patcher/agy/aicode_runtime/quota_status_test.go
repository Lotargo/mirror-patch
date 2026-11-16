package main

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
)

func TestQuotaReceiptPreservesSeparateSourcesAndUnknown(t *testing.T) {
	path := filepath.Join(t.TempDir(), "quota.json")
	raw := []byte(`{"buckets":[{"remainingFraction":0.4683456,"model":{"modelId":"gemini-3.8-flash","displayName":"Flash"}},{"remainingFraction":0,"model":{"modelId":"zero"}},{"model":{"modelId":"unknown"}}],"quotaSummary":{"groups":[{"displayName":"Gemini","buckets":[{"remainingFraction":1}]}]},"secret":"DO_NOT_STORE"}`)
	if err := writeQuotaStatus(path, raw); err != nil {
		t.Fatal(err)
	}
	data, _ := os.ReadFile(path)
	var d map[string]any
	_ = json.Unmarshal(data, &d)
	if _, ok := d["secret"]; ok {
		t.Fatal("unknown source fields leaked")
	}
	models := d["models"].([]any)
	if models[0].(map[string]any)["remaining_fraction"] != 0.4683456 || models[1].(map[string]any)["remaining_fraction"] != float64(0) || models[2].(map[string]any)["remaining_fraction"] != nil {
		t.Fatal("missing/zero/real fraction changed")
	}
	if string(data) == string(raw) {
		t.Fatal("raw response persisted")
	}
}
func TestQuotaReceiptRejectsInvalidMetadata(t *testing.T) {
	for _, raw := range []string{"null", "[]", "{}", "not JSON"} {
		if err := writeQuotaStatus(filepath.Join(t.TempDir(), "q.json"), []byte(raw)); err == nil {
			t.Fatal("invalid quota accepted", raw)
		}
	}
}
