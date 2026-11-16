package main

import (
	"encoding/json"
	"testing"
)

func TestCorrectQuotaSummaryUsesModelBuckets(t *testing.T) {
	raw := []byte(`{"buckets":[{"model":{"modelId":"gemini-a"},"remainingFraction":0.8},{"model":{"modelId":"gemini-b"},"remainingFraction":0.4683456,"resetTime":"2026-11-22T10:00:00Z"},{"model":{"modelId":"claude-a"},"remainingFraction":0},{"model":{"modelId":"gpt-a"}}],"quotaSummary":{"description":"keep","groups":[{"displayName":"Gemini Models","futureField":1,"buckets":[{"bucketId":"gemini-weekly","remainingFraction":1,"resetTime":"2026-11-23T00:00:00Z"}]},{"buckets":[{"bucketId":"3p-weekly","remainingFraction":1}]},{"buckets":[{"bucketId":"unknown","remainingFraction":0.7}]}]},"futureRoot":{"keep":true}}`)
	corrected, err := correctQuotaSummary(raw)
	if err != nil {
		t.Fatal(err)
	}
	var q map[string]any
	_ = json.Unmarshal(corrected, &q)
	groups := q["quotaSummary"].(map[string]any)["groups"].([]any)
	bucket := func(i int) map[string]any { return groups[i].(map[string]any)["buckets"].([]any)[0].(map[string]any) }
	if bucket(0)["remainingFraction"] != 0.4683456 || bucket(0)["resetTime"] != "2026-11-22T10:00:00Z" || bucket(1)["remainingFraction"] != float64(0) || bucket(2)["remainingFraction"] != 0.7 {
		t.Fatal("bad corrected fractions", string(corrected))
	}
	if groups[0].(map[string]any)["futureField"] != float64(1) || q["futureRoot"] == nil {
		t.Fatal("unknown fields dropped")
	}
	if q["buckets"].([]any)[0].(map[string]any)["remainingFraction"] != 0.8 {
		t.Fatal("original model quota modified")
	}
}
func TestCorrectQuotaSummaryDoesNotInventUnknown(t *testing.T) {
	raw := []byte(`{"buckets":[{"model":{"modelId":"gemini-a"}}],"quotaSummary":{"groups":[{"buckets":[{"bucketId":"gemini-weekly","remainingFraction":1}]}]}}`)
	out, err := correctQuotaSummary(raw)
	if err != nil || string(out) != string(raw) {
		t.Fatal("unknown input changed")
	}
}
