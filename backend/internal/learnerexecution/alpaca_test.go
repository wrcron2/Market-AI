package learnerexecution

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"strings"
	"testing"
	"time"
)

type paperTransportFunc func(*http.Request) (*http.Response, error)

func (f paperTransportFunc) RoundTrip(r *http.Request) (*http.Response, error) { return f(r) }
func brokerResponse(code int, body string) *http.Response {
	return &http.Response{StatusCode: code, Header: make(http.Header), Body: io.NopCloser(strings.NewReader(body))}
}
func paperConfig() PaperBrokerConfig {
	return PaperBrokerConfig{BaseURL: "https://paper-api.alpaca.markets", APIKey: "synthetic-key", SecretKey: "synthetic-secret", AccountID: "paper-account-1", Authority: "KIMI", OperatingMode: "paper", Paper: true}
}

const accountBody = `{"id":"paper-account-1","status":"ACTIVE","trading_blocked":false,"account_blocked":false,"buying_power":"100","cash":"100"}`
const orderBody = `{"id":"broker-order-1","client_order_id":"kl-3b93c90d59d0ae76a7d2cdaaee70cd0837122290","symbol":"AAPL","side":"buy","qty":"0.01","type":"limit","limit_price":"100.00","time_in_force":"day","status":"new","filled_qty":"0","filled_avg_price":null,"updated_at":"2026-09-23T14:00:02.000Z","order_class":"simple","extended_hours":false}`

func TestPaperAdapterExactSubmitAndLookup(t *testing.T) {
	for _, side := range []string{"buy", "sell"} {
		t.Run(side, func(t *testing.T) {
			var methods, targets []string
			transport := paperTransportFunc(func(r *http.Request) (*http.Response, error) {
				methods = append(methods, r.Method)
				targets = append(targets, r.URL.String())
				if r.Header.Get("APCA-API-KEY-ID") != "synthetic-key" || r.Header.Get("APCA-API-SECRET-KEY") != "synthetic-secret" {
					t.Fatal("missing broker authentication")
				}
				if r.URL.Path == "/v2/account" {
					return brokerResponse(200, accountBody), nil
				}
				if r.Method == "POST" {
					var request map[string]any
					if err := json.NewDecoder(r.Body).Decode(&request); err != nil {
						t.Fatal(err)
					}
					for key, want := range map[string]string{"symbol": "AAPL", "side": side, "qty": "0.01", "type": "limit", "limit_price": "100.00", "time_in_force": "day", "client_order_id": "kl-3b93c90d59d0ae76a7d2cdaaee70cd0837122290", "order_class": "simple"} {
						if request[key] != want {
							t.Errorf("%s changed: %v", key, request[key])
						}
					}
					if request["extended_hours"] != false || len(request) != 9 {
						t.Errorf("unexpected broker mechanics %+v", request)
					}
				}
				return brokerResponse(200, strings.Replace(orderBody, `"side":"buy"`, `"side":"`+side+`"`, 1)), nil
			})
			broker, err := newPaperBroker(paperConfig(), transport)
			if err != nil {
				t.Fatal(err)
			}
			broker.now = fixtureTime
			i, _ := DecodeIntent(fixtureBytes(t))
			if side == "sell" {
				i.Action = "SELL"
				i.Order.Side = "sell"
			}
			o, err := broker.Submit(context.Background(), i, "kl-3b93c90d59d0ae76a7d2cdaaee70cd0837122290")
			if err != nil || o.State != "accepted" || o.Side != side || o.AccountID != "paper-account-1" || o.FilledAveragePrice != nil {
				t.Fatalf("submit %+v %v", o, err)
			}
			found, err := broker.LookupByClientID(context.Background(), o.ClientOrderID)
			if err != nil || found == nil || found.OrderID != "broker-order-1" {
				t.Fatalf("lookup %+v %v", found, err)
			}
			if strings.Join(methods, ",") != "GET,POST,GET,GET" || targets[1] != "https://paper-api.alpaca.markets/v2/orders" || targets[3] != "https://paper-api.alpaca.markets/v2/orders:by_client_order_id?client_order_id=kl-3b93c90d59d0ae76a7d2cdaaee70cd0837122290" {
				t.Fatalf("unexpected transport %v %v", methods, targets)
			}
		})
	}
}

func TestPaperAdapterRejectsUnsafeConfiguration(t *testing.T) {
	for _, mutate := range []func(*PaperBrokerConfig){
		func(c *PaperBrokerConfig) { c.BaseURL = "https://api.alpaca.markets" }, func(c *PaperBrokerConfig) { c.BaseURL = "https://paper-api.alpaca.markets.evil.test" }, func(c *PaperBrokerConfig) { c.BaseURL = "http://paper-api.alpaca.markets" }, func(c *PaperBrokerConfig) { c.BaseURL = "https://paper-api.alpaca.markets/" }, func(c *PaperBrokerConfig) { c.Paper = false }, func(c *PaperBrokerConfig) { c.Authority = "LEGACY" }, func(c *PaperBrokerConfig) { c.OperatingMode = "learning" }, func(c *PaperBrokerConfig) { c.APIKey = "" }, func(c *PaperBrokerConfig) { c.AccountID = "" },
	} {
		c := paperConfig()
		mutate(&c)
		if _, err := newPaperBroker(c, paperTransportFunc(func(*http.Request) (*http.Response, error) {
			t.Fatal("invalid configuration reached network")
			return nil, nil
		})); err == nil {
			t.Fatal("unsafe broker configuration accepted")
		}
	}
}

func TestPaperAdapterAccountMismatchOrRedirectNeverMutates(t *testing.T) {
	for _, redirect := range []bool{false, true} {
		calls := 0
		broker, err := newPaperBroker(paperConfig(), paperTransportFunc(func(r *http.Request) (*http.Response, error) {
			calls++
			if r.Method != "GET" || r.URL.Path != "/v2/account" {
				t.Errorf("mutation or redirect followed: %s %s", r.Method, r.URL)
			}
			if redirect {
				resp := brokerResponse(302, "")
				resp.Header.Set("Location", "https://api.alpaca.markets/v2/orders")
				return resp, nil
			}
			return brokerResponse(200, strings.Replace(accountBody, "paper-account-1", "other", 1)), nil
		}))
		if err != nil {
			t.Fatal(err)
		}
		broker.now = fixtureTime
		i, _ := DecodeIntent(fixtureBytes(t))
		if _, err = broker.Submit(context.Background(), i, clientOrderID(i.AccountID, i.DecisionID)); err == nil {
			t.Fatal("unsafe account accepted")
		}
		if calls != 1 {
			t.Fatalf("unexpected network calls %d", calls)
		}
	}
}

func TestPaperAdapterMissingFieldsAndMalformedResponsesFailClosed(t *testing.T) {
	for _, body := range []string{`{}`, `null`, strings.Replace(orderBody, `"qty":"0.01",`, "", 1), strings.Replace(orderBody, `"status":"new"`, `"status":"new","status":"filled"`, 1), orderBody + ` {}`, strings.Repeat("x", 1048577)} {
		broker, err := newPaperBroker(paperConfig(), paperTransportFunc(func(r *http.Request) (*http.Response, error) {
			if r.URL.Path == "/v2/account" {
				return brokerResponse(200, accountBody), nil
			}
			return brokerResponse(200, body), nil
		}))
		if err != nil {
			t.Fatal(err)
		}
		broker.now = fixtureTime
		i, _ := DecodeIntent(fixtureBytes(t))
		if _, err = broker.Submit(context.Background(), i, clientOrderID(i.AccountID, i.DecisionID)); err == nil {
			t.Fatal("malformed order acknowledged")
		}
	}
}

func TestPaperAdapterErrorDoesNotExposeProviderBodyOrCredentials(t *testing.T) {
	broker, err := newPaperBroker(paperConfig(), paperTransportFunc(func(r *http.Request) (*http.Response, error) {
		return brokerResponse(500, "synthetic-secret provider internal details"), nil
	}))
	if err != nil {
		t.Fatal(err)
	}
	broker.now = fixtureTime
	i, _ := DecodeIntent(fixtureBytes(t))
	_, err = broker.Submit(context.Background(), i, clientOrderID(i.AccountID, i.DecisionID))
	if err == nil || strings.Contains(err.Error(), "synthetic-secret") || strings.Contains(err.Error(), "provider internal") {
		t.Fatalf("unsafe error %v", err)
	}
}

func TestPaperAdapterBlockedAccountAllowsReconciliationButNotSubmission(t *testing.T) {
	posts := 0
	lookups := 0
	broker, err := newPaperBroker(paperConfig(), paperTransportFunc(func(r *http.Request) (*http.Response, error) {
		if r.URL.Path == "/v2/account" {
			return brokerResponse(200, strings.Replace(accountBody, `"trading_blocked":false`, `"trading_blocked":true`, 1)), nil
		}
		if r.Method == "POST" {
			posts++
		} else {
			lookups++
		}
		return brokerResponse(200, orderBody), nil
	}))
	if err != nil {
		t.Fatal(err)
	}
	broker.now = fixtureTime
	i, _ := DecodeIntent(fixtureBytes(t))
	id := clientOrderID(i.AccountID, i.DecisionID)
	if _, err = broker.Submit(context.Background(), i, id); err == nil {
		t.Fatal("blocked account submitted")
	}
	o, err := broker.LookupByClientID(context.Background(), id)
	if err != nil || o == nil || o.OrderID != "broker-order-1" || posts != 0 || lookups != 1 {
		t.Fatalf("blocked account lost read-only recovery: %v posts=%d lookups=%d", err, posts, lookups)
	}
}

func TestPaperAdapterRechecksExpiryAfterSlowAccountRead(t *testing.T) {
	now := fixtureTime()
	posts := 0
	broker, err := newPaperBroker(paperConfig(), paperTransportFunc(func(r *http.Request) (*http.Response, error) {
		if r.URL.Path == "/v2/account" {
			now = now.Add(time.Hour)
			return brokerResponse(200, accountBody), nil
		}
		posts++
		return brokerResponse(200, orderBody), nil
	}))
	if err != nil {
		t.Fatal(err)
	}
	broker.now = func() time.Time { return now }
	i, _ := DecodeIntent(fixtureBytes(t))
	if _, err = broker.Submit(context.Background(), i, clientOrderID(i.AccountID, i.DecisionID)); err == nil || posts != 0 {
		t.Fatalf("expired intent reached broker order endpoint: %v posts=%d", err, posts)
	}
}
