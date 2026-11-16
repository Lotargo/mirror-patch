package main

import (
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"time"
)

type quotaModel struct {
	ID   string `json:"modelId"`
	Name string `json:"displayName"`
}
type quotaSourceBucket struct {
	Model     quotaModel `json:"model"`
	Remaining *float64   `json:"remainingFraction"`
	ResetTime string     `json:"resetTime"`
	ID        string     `json:"bucketId"`
	Name      string     `json:"displayName"`
	Window    string     `json:"refreshWindowLabel"`
}

// Capture only quota metadata. Do not replace the original response or infer
// that group and per-model buckets describe the same limiting window.
func writeQuotaStatus(path string, payload []byte) error {
	if path == "" {
		return nil
	}
	var source struct {
		Buckets []quotaSourceBucket `json:"buckets"`
		Summary struct {
			Description string `json:"description"`
			Groups      []struct {
				Name        string              `json:"displayName"`
				Description string              `json:"description"`
				Buckets     []quotaSourceBucket `json:"buckets"`
			} `json:"groups"`
		} `json:"quotaSummary"`
	}
	if json.Unmarshal(payload, &source) != nil || (source.Buckets == nil && source.Summary.Groups == nil) {
		return errors.New("invalid quota metadata")
	}
	bucket := func(b quotaSourceBucket) map[string]any {
		return map[string]any{"model_id": b.Model.ID, "name": b.Model.Name, "remaining_fraction": b.Remaining, "reset_at": b.ResetTime, "bucket_id": b.ID, "label": b.Name, "window": b.Window}
	}
	models := []any{}
	for _, b := range source.Buckets {
		models = append(models, bucket(b))
	}
	groups := []any{}
	for _, g := range source.Summary.Groups {
		buckets := []any{}
		for _, b := range g.Buckets {
			buckets = append(buckets, bucket(b))
		}
		groups = append(groups, map[string]any{"name": g.Name, "description": g.Description, "buckets": buckets})
	}
	data, err := json.Marshal(map[string]any{"schema_version": 1, "checked_at": time.Now().UTC(), "source": "aicode.googleapis.com/v1alpha:fetchQuotaStatus", "models": models, "groups": groups})
	if err != nil {
		return err
	}
	f, err := os.CreateTemp(filepath.Dir(path), ".agy-quota-*.tmp")
	if err != nil {
		return err
	}
	defer os.Remove(f.Name())
	writeErr := f.Chmod(0600)
	if writeErr == nil {
		_, writeErr = f.Write(data)
	}
	closeErr := f.Close()
	if writeErr != nil {
		return writeErr
	}
	if closeErr != nil {
		return closeErr
	}
	return os.Rename(f.Name(), path)
}
