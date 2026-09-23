package learnerexecution

import (
	"context"
	"net/http"
	"strings"
	"testing"
	"time"
)

func observationResponses() map[string]string {
	return map[string]string{
		"/v2/account":              accountBody,
		"/v2/clock":                `{"timestamp":"2026-09-23T14:00:01.000Z","is_open":true}`,
		"/v2/assets/AAPL":          `{"symbol":"AAPL","class":"us_equity","status":"active","tradable":true,"fractionable":true}`,
		"/v2/positions":            `[]`,
		"/v2/orders":               `[]`,
		"/v2/stocks/quotes/latest": `{"quotes":{"AAPL":{"ap":100.125,"bp":100.12,"t":"2026-09-23T14:00:00.123456789Z"}}}`,
	}
}

func TestExternalShortObservationReservesCoverCash(t *testing.T) {
	responses := observationResponses()
	responses["/v2/positions"] = `[{"symbol":"XLE","qty":"-112","market_value":"-50"}]`
	broker, err := newPaperBroker(paperConfig(), paperTransportFunc(func(r *http.Request) (*http.Response, error) { return brokerResponse(200, responses[r.URL.Path]), nil }))
	if err != nil {
		t.Fatal(err)
	}
	broker.now = fixtureTime
	o, err := broker.Observe(context.Background(), "AAPL")
	if err != nil || o.BuyingPower != "50" || o.Positions[0].Qty != "-112" {
		t.Fatalf("short position or cover cash lost: %+v %v", o, err)
	}
}

func TestPaperObservationCannotOfferMarginAsSpendableCash(t *testing.T) {
	for _, tc := range []struct{ cash, power, want string }{{"2", "100", "2"}, {"100", "2", "2"}, {"0", "100", "0"}} {
		responses := observationResponses()
		responses["/v2/account"] = strings.Replace(strings.Replace(accountBody, `"cash":"100"`, `"cash":"`+tc.cash+`"`, 1), `"buying_power":"100"`, `"buying_power":"`+tc.power+`"`, 1)
		broker, err := newPaperBroker(paperConfig(), paperTransportFunc(func(r *http.Request) (*http.Response, error) { return brokerResponse(200, responses[r.URL.Path]), nil }))
		if err != nil {
			t.Fatal(err)
		}
		broker.now = fixtureTime
		o, err := broker.Observe(context.Background(), "AAPL")
		if err != nil || o.BuyingPower != tc.want {
			t.Fatalf("cash=%s power=%s: spendable=%s err=%v", tc.cash, tc.power, o.BuyingPower, err)
		}
	}
}
func TestPaperObservationPreservesSourceTimeAndUsesIEXOnly(t *testing.T) {
	responses := observationResponses()
	calls := 0
	broker, err := newPaperBroker(paperConfig(), paperTransportFunc(func(r *http.Request) (*http.Response, error) {
		calls++
		if r.Method != "GET" {
			t.Fatal("observation mutated broker")
		}
		if r.URL.Path == "/v2/stocks/quotes/latest" {
			if r.URL.Host != "data.alpaca.markets" || r.URL.Query().Get("feed") != "iex" || r.URL.Query().Get("symbols") != "AAPL" {
				t.Fatalf("wrong market feed %s", r.URL)
			}
		} else if r.URL.Host != "paper-api.alpaca.markets" {
			t.Fatalf("wrong account host %s", r.URL)
		}
		if r.URL.Path == "/v2/orders" && (r.URL.Query().Get("status") != "open" || r.URL.Query().Get("limit") != "500") {
			t.Fatal("incomplete order query")
		}
		body, ok := responses[r.URL.Path]
		if !ok {
			t.Fatalf("unexpected request %s", r.URL)
		}
		return brokerResponse(200, body), nil
	}))
	if err != nil {
		t.Fatal(err)
	}
	broker.now = fixtureTime
	o, err := broker.Observe(context.Background(), "AAPL")
	if err != nil || !o.Complete || !o.Paper || o.AccountID != "paper-account-1" || o.QuotePrice != "100.125" || o.Source != "alpaca/iex" || o.SourceQuoteAt != "2026-09-23T14:00:00.123456789Z" || o.QuoteAt.Format(timestampLayout) != "2026-09-23T14:00:00.123Z" || !o.RetrievedAt.Equal(fixtureTime()) || len(o.Positions) != 0 || len(o.OpenOrders) != 0 || calls != 6 {
		t.Fatalf("observation %+v err=%v calls=%d", o, err, calls)
	}
}

func TestPaperObservationFailsClosedOnIncompleteSources(t *testing.T) {
	for _, tc := range []struct{ path, body string }{
		{"/v2/account", `{"id":"paper-account-1"}`},
		{"/v2/account", strings.Replace(accountBody, `,"cash":"100"`, "", 1)},
		{"/v2/clock", `{"is_open":true}`},
		{"/v2/assets/AAPL", `{"symbol":"AAPL","class":"us_equity","tradable":true}`},
		{"/v2/positions", `null`},
		{"/v2/positions", `[{"symbol":"AAPL","qty":"1"}]`},
		{"/v2/orders", `null`},
		{"/v2/orders", "[" + strings.TrimSuffix(strings.Repeat(orderBody+",", 500), ",") + "]"},
		{"/v2/stocks/quotes/latest", `{"quotes":{"AAPL":{"ap":100,"bp":99}}}`},
		{"/v2/stocks/quotes/latest", `{"quotes":{"AAPL":{"ap":0,"bp":0,"t":"2026-09-23T14:00:00Z"}}}`},
		{"/v2/stocks/quotes/latest", `{"quotes":{"AAPL":{"ap":99,"bp":100,"t":"2026-09-23T14:00:00Z"}}}`},
	} {
		t.Run(tc.path+tc.body[:1], func(t *testing.T) {
			responses := observationResponses()
			responses[tc.path] = tc.body
			broker, err := newPaperBroker(paperConfig(), paperTransportFunc(func(r *http.Request) (*http.Response, error) { return brokerResponse(200, responses[r.URL.Path]), nil }))
			if err != nil {
				t.Fatal(err)
			}
			broker.now = fixtureTime
			o, err := broker.Observe(context.Background(), "AAPL")
			if err == nil || o.Complete {
				t.Fatal("incomplete snapshot accepted")
			}
		})
	}
}

func TestPaperObservationKeepsOldQuoteOld(t *testing.T) {
	responses := observationResponses()
	responses["/v2/stocks/quotes/latest"] = `{"quotes":{"AAPL":{"ap":100,"bp":99,"t":"2026-09-23T13:00:00Z"}}}`
	broker, err := newPaperBroker(paperConfig(), paperTransportFunc(func(r *http.Request) (*http.Response, error) { return brokerResponse(200, responses[r.URL.Path]), nil }))
	if err != nil {
		t.Fatal(err)
	}
	broker.now = fixtureTime
	o, err := broker.Observe(context.Background(), "AAPL")
	if err != nil {
		t.Fatal(err)
	}
	if fixtureTime().Sub(o.QuoteAt) < time.Hour || !o.RetrievedAt.Equal(fixtureTime()) {
		t.Fatal("stale quote relabeled as fresh")
	}
}

func TestPaperCancelTargetsVerifiedLearnerOrderOnly(t *testing.T) {
	for _, mismatch := range []bool{false, true} {
		deletes := 0
		broker, err := newPaperBroker(paperConfig(), paperTransportFunc(func(r *http.Request) (*http.Response, error) {
			if r.URL.Path == "/v2/account" {
				return brokerResponse(200, accountBody), nil
			}
			if r.Method == "GET" {
				return brokerResponse(200, orderBody), nil
			}
			if r.Method != "DELETE" || r.URL.Path != "/v2/orders/broker-order-1" {
				t.Fatalf("unsafe cancel path %s %s", r.Method, r.URL)
			}
			deletes++
			return brokerResponse(204, ""), nil
		}))
		if err != nil {
			t.Fatal(err)
		}
		id := "broker-order-1"
		if mismatch {
			id = "different-order"
		}
		err = broker.Cancel(context.Background(), "kl-3b93c90d59d0ae76a7d2cdaaee70cd0837122290", id)
		if mismatch {
			if err == nil || deletes != 0 {
				t.Fatal("canceled mismatched order")
			}
		} else if err != nil || deletes != 1 {
			t.Fatalf("cancel %v count=%d", err, deletes)
		}
	}
}
