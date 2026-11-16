package main

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestPublicationReceiptIncludesHiddenAliasesAndNoSecrets(t *testing.T) {
	path := filepath.Join(t.TempDir(), "catalog.json")
	payload := []byte(`{"secret":"DO_NOT_PERSIST","availableExperiences":[
	{"name":"gemini-3.1-flash-lite","model":{"modelId":"gemini-3.5-flash-lite","supportedFeatures":["FUNCTION_CALLING","THINKING_LEVEL"],"generationConfig":{"thinkingConfig":{"thinkingLevel":"LOW","unknown":"DO_NOT_PERSIST"}}}},
	{"name":"disabled","model":{}},{"name":"image","model":{"modelId":"image","supportedFeatures":["IMAGE_OUTPUT"]}}]}`)
	if err := writeModelCatalog(path, payload); err != nil {
		t.Fatal(err)
	}
	b, _ := os.ReadFile(path)
	if strings.Contains(string(b), "DO_NOT_PERSIST") {
		t.Fatal("receipt leaked unselected fields")
	}
	var value struct {
		Experiences []map[string]any `json:"experiences"`
	}
	if json.Unmarshal(b, &value) != nil || len(value.Experiences) != 2 {
		t.Fatal(string(b))
	}
	if value.Experiences[0]["id"] != "gemini-3.1-flash-lite" {
		t.Fatal(value)
	}
	if err := writeModelCatalog(path, []byte(`{"availableExperiences":[]}`)); err != nil {
		t.Fatal(err)
	}
	if err := writeModelCatalog(path, []byte(`{}`)); err == nil {
		t.Fatal("missing publication accepted")
	}
}
