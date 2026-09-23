package alpaca

import (
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"

	"github.com/marketflow/backend/internal/operatingmode"
)

// countingTransport records every outbound request and serves canned replies.
type countingTransport struct {
	calls   atomic.Int32
	handler func(*http.Request) *http.Response
}

func (c *countingTransport) RoundTrip(r *http.Request) (*http.Response, error) {
	c.calls.Add(1)
	if c.handler != nil {
		return c.handler(r), nil
	}
	return jsonResponse(http.StatusOK, `{}`), nil
}

func jsonResponse(status int, body string) *http.Response {
	h := make(http.Header)
	h.Set("Content-Type", "application/json")
	return &http.Response{
		StatusCode: status,
		Status:     http.StatusText(status),
		Body:       io.NopCloser(strings.NewReader(body)),
		Header:     h,
	}
}

// newTestHandler builds a Handler with env exactly as a production process
// would see it, then swaps in the fake transport (same shape as the real
// client: bounded timeout, env-captured mode).
func newTestHandler(t *testing.T, mode, paperTrading, baseURL string) (*Handler, *countingTransport) {
	t.Helper()
	t.Setenv("MARKET_AI_OPERATING_MODE", mode)
	t.Setenv("DECISION_AUTHORITY", "LEGACY")
	t.Setenv("PAPER_TRADING", paperTrading)
	t.Setenv("ALPACA_BASE_URL", baseURL)
	t.Setenv("ALPACA_API_KEY", "test-key")
	t.Setenv("ALPACA_SECRET_KEY", "test-secret")
	h := NewHandler()
	ct := &countingTransport{}
	h.client = &http.Client{Transport: ct, Timeout: brokerTimeout}
	return h, ct
}

// In learning mode PlaceOrder/ClosePosition must fail before any network
// request — including ClosePosition's preliminary position GET.
func TestLearningModeMutationsProduceZeroOutboundCalls(t *testing.T) {
	for _, mode := range []string{"", "learning", "bogus", "Paper", "live"} {
		h, ct := newTestHandler(t, mode, "true", operatingmode.PaperBrokerBaseURL)

		if _, err := h.PlaceOrder("AAPL", "BUY", 1); err == nil {
			t.Errorf("mode=%q: PlaceOrder succeeded, want learning_only rejection", mode)
		}
		if _, err := h.ClosePosition("AAPL"); err == nil {
			t.Errorf("mode=%q: ClosePosition succeeded, want learning_only rejection", mode)
		}
		if got := ct.calls.Load(); got != 0 {
			t.Errorf("mode=%q: %d outbound calls escaped the broker boundary, want 0", mode, got)
		}
	}
}

// Paper mode still fails closed when PAPER_TRADING is not explicitly true.
func TestPaperModeMutationsRequireExplicitPaperTrading(t *testing.T) {
	for _, pt := range []string{"", "false", "0"} {
		h, ct := newTestHandler(t, "paper", pt, operatingmode.PaperBrokerBaseURL)
		if _, err := h.PlaceOrder("AAPL", "BUY", 1); err == nil {
			t.Errorf("PAPER_TRADING=%q: PlaceOrder succeeded, want rejection", pt)
		}
		if _, err := h.ClosePosition("AAPL"); err == nil {
			t.Errorf("PAPER_TRADING=%q: ClosePosition succeeded, want rejection", pt)
		}
		if got := ct.calls.Load(); got != 0 {
			t.Errorf("PAPER_TRADING=%q: %d outbound calls, want 0", pt, got)
		}
	}
}

// Paper mode fails closed on any broker URL that is not exactly the paper
// endpoint: live host, insecure scheme, trailing slash, lookalike host,
// userinfo, path suffix.
func TestPaperModeMutationsRequireExactPaperURL(t *testing.T) {
	badURLs := []string{
		"https://api.alpaca.markets",
		"http://paper-api.alpaca.markets",
		"https://paper-api.alpaca.markets/",
		"https://paper-api.alpaca.markets.evil.example",
		"https://user:secret@paper-api.alpaca.markets",
		"https://paper-api.alpaca.markets/v2",
	}
	for _, u := range badURLs {
		h, ct := newTestHandler(t, "paper", "true", u)
		if _, err := h.PlaceOrder("AAPL", "BUY", 1); err == nil {
			t.Errorf("baseURL=%q: PlaceOrder succeeded, want fail-closed rejection", u)
		}
		if _, err := h.ClosePosition("AAPL"); err == nil {
			t.Errorf("baseURL=%q: ClosePosition succeeded, want fail-closed rejection", u)
		}
		if got := ct.calls.Load(); got != 0 {
			t.Errorf("baseURL=%q: %d outbound calls, want 0", u, got)
		}
	}
}

// Existing paper behavior must still be possible under explicit, valid paper
// configuration: PlaceOrder POSTs once, ClosePosition GETs then DELETEs.
func TestPaperModeMutationsWorkWithValidConfig(t *testing.T) {
	h, ct := newTestHandler(t, "paper", "true", operatingmode.PaperBrokerBaseURL)
	ct.handler = func(r *http.Request) *http.Response {
		switch {
		case r.Method == http.MethodPost && strings.HasSuffix(r.URL.Path, "/v2/orders"):
			return jsonResponse(http.StatusOK, `{"id":"order-1","status":"accepted","symbol":"AAPL","qty":"5"}`)
		case r.Method == http.MethodGet && strings.Contains(r.URL.Path, "/v2/positions/"):
			return jsonResponse(http.StatusOK, `{"symbol":"AAPL","current_price":"123.45"}`)
		case r.Method == http.MethodDelete:
			return jsonResponse(http.StatusNoContent, ``)
		default:
			return jsonResponse(http.StatusBadRequest, `{"message":"unexpected"}`)
		}
	}

	res, err := h.PlaceOrder("AAPL", "BUY", 5)
	if err != nil {
		t.Fatalf("valid paper PlaceOrder rejected: %v", err)
	}
	if res.ID != "order-1" {
		t.Errorf("PlaceOrder result ID = %q, want order-1", res.ID)
	}

	closeRes, err := h.ClosePosition("AAPL")
	if err != nil {
		t.Fatalf("valid paper ClosePosition rejected: %v", err)
	}
	if closeRes.FillPrice != 123.45 {
		t.Errorf("ClosePosition fill price = %v, want 123.45", closeRes.FillPrice)
	}
	if got := ct.calls.Load(); got != 3 {
		t.Errorf("outbound calls = %d, want 3 (POST order, GET position, DELETE position)", got)
	}
}

// Read-only account/data access stays usable in learning mode: the boundary
// gates mutations only.
func TestLearningModeReadsRemainUsable(t *testing.T) {
	h, ct := newTestHandler(t, "learning", "false", operatingmode.PaperBrokerBaseURL)
	ct.handler = func(r *http.Request) *http.Response {
		if r.Method != http.MethodGet {
			return jsonResponse(http.StatusBadRequest, `{"message":"mutation leaked"}`)
		}
		return jsonResponse(http.StatusOK, `{"cash":"10000.00","status":"ACTIVE"}`)
	}

	cash, err := h.SettledCash()
	if err != nil {
		t.Fatalf("SettledCash (read) blocked in learning mode: %v", err)
	}
	if cash != 10000.00 {
		t.Errorf("SettledCash = %v, want 10000", cash)
	}

	rec := httptest.NewRecorder()
	h.Account(rec, httptest.NewRequest(http.MethodGet, "/api/alpaca/account", nil))
	if rec.Code != http.StatusOK {
		t.Errorf("Account proxy (read) status = %d, want 200", rec.Code)
	}
	if got := ct.calls.Load(); got != 2 {
		t.Errorf("read calls = %d, want 2 (account via SettledCash + Account)", got)
	}
}

// The Go client must carry a bounded timeout so a hung broker cannot stall
// handlers forever.
func TestHTTPClientHasBoundedTimeout(t *testing.T) {
	t.Setenv("MARKET_AI_OPERATING_MODE", "")
	t.Setenv("ALPACA_BASE_URL", operatingmode.PaperBrokerBaseURL)
	h := NewHandler()
	if h.client.Timeout <= 0 {
		t.Error("Alpaca HTTP client has no timeout")
	}
	if h.client.Timeout > 60*1e9 {
		t.Errorf("Alpaca HTTP client timeout %v exceeds a sane bound", h.client.Timeout)
	}
}

func TestLegacyClientCannotExecuteForKimiOrMissingAuthority(t *testing.T) {
	for _, authority := range []string{"KIMI", "", "invalid"} {
		_, ct := newTestHandler(t, "paper", "true", operatingmode.PaperBrokerBaseURL)
		t.Setenv("DECISION_AUTHORITY", authority)
		h := NewHandler()
		h.client = &http.Client{Transport: ct}
		t.Setenv("DECISION_AUTHORITY", "LEGACY")
		if _, err := h.PlaceOrder("AAPL", "SELL", 1); err == nil {
			t.Fatal("legacy client placed order")
		}
		if _, err := h.ClosePosition("AAPL"); err == nil {
			t.Fatal("legacy client closed position")
		}
		if ct.calls.Load() != 0 {
			t.Fatal("blocked client issued network requests")
		}
	}
}
