package learnerexecution

import (
	"encoding/json"
	"os"
	"strings"
	"testing"
	"time"
)

func fixtureBytes(t *testing.T) []byte {
	t.Helper()
	b, e := os.ReadFile("testdata/trade-intent-v1.json")
	if e != nil {
		t.Fatal(e)
	}
	return b
}
func fixtureTime() time.Time { return time.Date(2026, 9, 23, 14, 0, 1, 0, time.UTC) }
func TestIntentPreservesEconomicDecision(t *testing.T) {
	i, e := DecodeIntent(fixtureBytes(t))
	if e != nil {
		t.Fatal(e)
	}
	if e = i.ValidateFresh(fixtureTime()); e != nil {
		t.Fatal(e)
	}
	if i.Action != "BUY" || i.Order.Side != "buy" || i.Order.Qty != "0.01" || i.Order.LimitPrice != "100.00" || i.PredictionID != 42 {
		t.Fatalf("modified: %+v", i)
	}
	for _, action := range []string{"SELL", "EXIT"} {
		i.Action = action
		i.Order.Side = "sell"
		b, _ := json.Marshal(i)
		if _, e := DecodeIntent(b); e != nil {
			t.Fatal(e)
		}
	}
	i.Action = "HOLD"
	i.Order = nil
	b, _ := json.Marshal(i)
	if _, e := DecodeIntent(b); e != nil {
		t.Fatal(e)
	}
}
func TestIntentRejectsMalformedWire(t *testing.T) {
	raw := string(fixtureBytes(t))
	cases := map[string]string{
		"duplicate":      strings.Replace(raw, `"action": "BUY"`, `"action":"SELL","action":"BUY"`, 1),
		"case alias":     strings.Replace(raw, `"symbol"`, `"Symbol"`, 1),
		"unknown":        strings.Replace(raw, `"action": "BUY"`, `"extra":true,"action":"BUY"`, 1),
		"missing":        strings.Replace(raw, `"action": "BUY",`, "", 1),
		"null":           strings.Replace(raw, `"rationale": "Synthetic fixture, not an investment recommendation."`, `"rationale":null`, 1),
		"live":           strings.Replace(raw, `"environment": "paper"`, `"environment":"live"`, 1),
		"side":           strings.Replace(raw, `"side": "buy"`, `"side":"sell"`, 1),
		"order":          strings.Replace(raw, `"type": "limit"`, `"type":"market"`, 1),
		"overflow":       strings.Replace(raw, `"qty": "0.01"`, `"qty":"1000000000"`, 1),
		"precision":      strings.Replace(raw, `"qty": "0.01"`, `"qty":"0.0000000001"`, 1),
		"zero":           strings.Replace(raw, `"qty": "0.01"`, `"qty":"0"`, 1),
		"exponent":       strings.Replace(raw, `"qty": "0.01"`, `"qty":"1e2"`, 1),
		"price":          strings.Replace(raw, `"limitPrice": "100.00"`, `"limitPrice":"0.00001"`, 1),
		"date":           strings.Replace(raw, "2026-09-23T14:00:00.000Z", "2026-02-30T14:00:00.000Z", 1),
		"ttl":            strings.Replace(raw, "2026-09-23T14:01:00.000Z", "2026-09-23T14:10:00.000Z", 1),
		"unsafe integer": strings.Replace(raw, `"predictionId": 42`, `"predictionId":9007199254740992`, 1),
		"trailing":       raw + `{}`, "invalid utf8": raw + string([]byte{0xff}),
		"oversize": strings.Repeat(" ", 65537) + raw,
	}
	for name, b := range cases {
		t.Run(name, func(t *testing.T) {
			if _, e := DecodeIntent([]byte(b)); e == nil {
				t.Fatal("accepted malformed intent")
			}
		})
	}
}
func TestFreshnessIsSeparateFromHistoricalDecoding(t *testing.T) {
	i, e := DecodeIntent(fixtureBytes(t))
	if e != nil {
		t.Fatal(e)
	}
	for _, now := range []time.Time{fixtureTime().Add(-2 * time.Second), fixtureTime().Add(time.Minute)} {
		if i.ValidateFresh(now) == nil {
			t.Fatal("accepted future/expired intent")
		}
	}
}

func TestNamespacedModelAndSemanticVersion(t *testing.T) {
	raw := strings.Replace(string(fixtureBytes(t)), "synthetic-test", "kimi-code/k3", 1)
	raw = strings.Replace(raw, "decision-v1", "1.0.0", 1)
	i, e := DecodeIntent([]byte(raw))
	if e != nil {
		t.Fatal(e)
	}
	if i.Producer.Model != "kimi-code/k3" || i.Producer.Version != "1.0.0" {
		t.Fatal("producer metadata changed")
	}
}
