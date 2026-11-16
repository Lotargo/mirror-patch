package main

import (
	"encoding/json"
	"math"
	"strings"
	"time"
)

// Recognized weekly groups use their least remaining valid model bucket. Do not
// average distinct quotas or treat missing data as zero/full. Unknown groups and
// fields are retained. A tied minimum uses the latest known reset among ties.
func correctQuotaSummary(payload []byte) ([]byte, error) {
	var root map[string]json.RawMessage
	if err := json.Unmarshal(payload, &root); err != nil {
		return nil, err
	}
	var models []quotaSourceBucket
	if json.Unmarshal(root["buckets"], &models) != nil {
		return payload, nil
	}
	var summary map[string]json.RawMessage
	if json.Unmarshal(root["quotaSummary"], &summary) != nil {
		return payload, nil
	}
	var groups []map[string]json.RawMessage
	if json.Unmarshal(summary["groups"], &groups) != nil {
		return payload, nil
	}
	changed := false
	for _, g := range groups {
		var buckets []map[string]json.RawMessage
		if json.Unmarshal(g["buckets"], &buckets) != nil {
			continue
		}
		for _, b := range buckets {
			var id string
			_ = json.Unmarshal(b["bucketId"], &id)
			if id != "gemini-weekly" && id != "3p-weekly" {
				continue
			}
			var selected *quotaSourceBucket
			for i := range models {
				m := &models[i]
				name := strings.TrimPrefix(strings.ToLower(m.Model.ID), "models/")
				matches := id == "gemini-weekly" && strings.HasPrefix(name, "gemini-") || id == "3p-weekly" && (strings.HasPrefix(name, "claude-") || strings.HasPrefix(name, "gpt-"))
				if !matches || m.Remaining == nil || math.IsNaN(*m.Remaining) || math.IsInf(*m.Remaining, 0) || *m.Remaining < 0 || *m.Remaining > 1 {
					continue
				}
				if selected == nil || *m.Remaining < *selected.Remaining {
					selected = m
				} else if *m.Remaining == *selected.Remaining {
					next, ne := time.Parse(time.RFC3339Nano, m.ResetTime)
					old, oe := time.Parse(time.RFC3339Nano, selected.ResetTime)
					if ne == nil && (oe != nil || next.After(old)) {
						selected = m
					}
				}
			}
			if selected == nil {
				continue
			}
			b["remainingFraction"], _ = json.Marshal(*selected.Remaining)
			if _, err := time.Parse(time.RFC3339Nano, selected.ResetTime); err == nil {
				b["resetTime"], _ = json.Marshal(selected.ResetTime)
			} else {
				delete(b, "resetTime")
			}
			changed = true
		}
		g["buckets"], _ = json.Marshal(buckets)
	}
	if !changed {
		return payload, nil
	}
	summary["groups"], _ = json.Marshal(groups)
	root["quotaSummary"], _ = json.Marshal(summary)
	return json.Marshal(root)
}
