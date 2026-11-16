package main

import (
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"time"
)

const modelCatalogEnv = "AGY_MODEL_CATALOG_FILE"

// A metadata-only receipt for the child SDK process. Never persist OAuth,
// request headers, prompts, experiment payloads or the entire configuration.
func writeModelCatalog(path string, payload []byte) error {
	if path == "" {
		return nil
	}
	var source struct {
		Experiences []struct {
			Name     string `json:"name"`
			Disabled bool   `json:"disabled"`
			Model    *struct {
				ID             string   `json:"modelId"`
				Name           string   `json:"name"`
				DisplayName    string   `json:"displayName"`
				Features       []string `json:"supportedFeatures"`
				ThinkingLevels []string `json:"supportedThinkingLevels"`
				Generation     struct {
					Thinking struct {
						Level  string `json:"thinkingLevel,omitempty"`
						Budget int    `json:"thinkingBudget,omitempty"`
					} `json:"thinkingConfig"`
				} `json:"generationConfig"`
			} `json:"model"`
		} `json:"availableExperiences"`
	}
	if json.Unmarshal(payload, &source) != nil || source.Experiences == nil {
		return errors.New("invalid model publication metadata")
	}
	entries := []any{}
	for _, experience := range source.Experiences {
		m := experience.Model
		if experience.Disabled || experience.Name == "" || m == nil || (m.ID == "" && m.Name == "") {
			continue
		}
		entries = append(entries, map[string]any{"id": experience.Name, "model_id": m.ID, "model_name": m.Name, "display_name": m.DisplayName, "features": m.Features, "thinking_levels": m.ThinkingLevels, "thinking": m.Generation.Thinking})
	}
	data, err := json.Marshal(map[string]any{"schema_version": 1, "checked_at": time.Now().UTC(), "experiences": entries})
	if err != nil {
		return err
	}
	f, err := os.CreateTemp(filepath.Dir(path), ".agy-catalog-*.tmp")
	if err != nil {
		return err
	}
	defer os.Remove(f.Name())
	_, writeErr := f.Write(data)
	closeErr := f.Close()
	if writeErr != nil {
		return writeErr
	}
	if closeErr != nil {
		return closeErr
	}
	return os.Rename(f.Name(), path)
}
