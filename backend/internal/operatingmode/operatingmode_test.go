package operatingmode

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
)

// Mode resolution: missing/empty/unknown values must fail closed to learning;
// only the exact string "paper" may permit legacy behavior.
func TestParseFailsClosed(t *testing.T) {
	cases := map[string]Mode{
		"":         Learning,
		"learning": Learning,
		"live":     Learning, // no live mode exists
		"yahoo":    Learning,
		"Paper":    Learning, // case-sensitive on purpose
		"PAPER":    Learning,
		"paper ":   Learning, // trailing space is not exact
		" paper":   Learning,
		"0":        Learning,
		"true":     Learning,
		"paper":    Paper, // the ONLY permissive value
	}
	for raw, want := range cases {
		if got := Parse(raw); got != want {
			t.Errorf("Parse(%q) = %q, want %q", raw, got, want)
		}
	}
}

func TestFromEnvDefaultsClosed(t *testing.T) {
	t.Setenv(EnvVar, "")
	if got := FromEnv(); got != Learning {
		t.Errorf("FromEnv with empty %s = %q, want learning", EnvVar, got)
	}
	t.Setenv(EnvVar, "paper")
	if got := FromEnv(); got != Paper {
		t.Errorf("FromEnv with paper = %q, want paper", got)
	}
}

func TestDecisionExecutionFlags(t *testing.T) {
	if Learning.DecisionsEnabled() || Learning.ExecutionEnabled() {
		t.Error("learning mode must have decisionsEnabled=false and executionEnabled=false")
	}
	if !Paper.DecisionsEnabled() || !Paper.ExecutionEnabled() {
		t.Error("paper mode must have decisionsEnabled=true and executionEnabled=true")
	}
}

type spyHandler struct{ called bool }

func (s *spyHandler) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	s.called = true
	w.WriteHeader(http.StatusOK)
}

// In learning mode a rejected request must never reach the business handler;
// read methods must pass through untouched.
func TestMiddlewareLearning(t *testing.T) {
	for _, method := range []string{http.MethodPost, http.MethodPut, http.MethodPatch, http.MethodDelete} {
		spy := &spyHandler{}
		rec := httptest.NewRecorder()
		req := httptest.NewRequest(method, "/api/orders/approve", nil)
		Learning.Middleware(spy).ServeHTTP(rec, req)

		if spy.called {
			t.Errorf("%s: business handler was invoked in learning mode", method)
		}
		if rec.Code != http.StatusLocked {
			t.Errorf("%s: status = %d, want 423", method, rec.Code)
		}
		var body map[string]string
		if err := json.Unmarshal(rec.Body.Bytes(), &body); err != nil {
			t.Fatalf("%s: response is not JSON: %v", method, err)
		}
		if body["error"] != "learning_only" {
			t.Errorf("%s: error field = %q, want learning_only", method, body["error"])
		}
		if body["message"] == "" {
			t.Errorf("%s: message field must be present", method)
		}
	}

	for _, method := range []string{http.MethodGet, http.MethodHead, http.MethodOptions} {
		spy := &spyHandler{}
		rec := httptest.NewRecorder()
		req := httptest.NewRequest(method, "/api/positions", nil)
		Learning.Middleware(spy).ServeHTTP(rec, req)
		if !spy.called {
			t.Errorf("%s: read request must reach the handler in learning mode", method)
		}
	}
}

func TestMiddlewarePaperPassesThrough(t *testing.T) {
	spy := &spyHandler{}
	rec := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodPost, "/api/orders/approve", nil)
	Paper.Middleware(spy).ServeHTTP(rec, req)
	if !spy.called {
		t.Error("paper mode must pass POST requests through to the handler")
	}
}

// Regression: the middleware must deny on m != Paper — a zero-value or
// otherwise invalid Mode must behave exactly like learning, never permitting
// a mutation through to the business handler.
func TestMiddlewareDeniesNonPaperModes(t *testing.T) {
	for _, m := range []Mode{"", "invalid", Learning} {
		spy := &spyHandler{}
		rec := httptest.NewRecorder()
		m.Middleware(spy).ServeHTTP(rec, httptest.NewRequest(http.MethodPost, "/api/orders/approve", nil))
		if spy.called {
			t.Errorf("Mode(%q): business handler was invoked for a mutating request", m)
		}
		if rec.Code != http.StatusLocked {
			t.Errorf("Mode(%q): POST status = %d, want 423", m, rec.Code)
		}

		spy = &spyHandler{}
		rec = httptest.NewRecorder()
		m.Middleware(spy).ServeHTTP(rec, httptest.NewRequest(http.MethodGet, "/api/positions", nil))
		if !spy.called {
			t.Errorf("Mode(%q): GET must still reach the handler", m)
		}
	}
}

func TestStatusHandler(t *testing.T) {
	rec := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodGet, "/api/operating-mode", nil)
	Learning.StatusHandler(rec, req)

	var body statusResponse
	if err := json.Unmarshal(rec.Body.Bytes(), &body); err != nil {
		t.Fatalf("status response is not JSON: %v", err)
	}
	if body.Mode != Learning || body.DecisionsEnabled || body.ExecutionEnabled {
		t.Errorf("learning status = %+v, want mode=learning decisionsEnabled=false executionEnabled=false", body)
	}

	rec = httptest.NewRecorder()
	Paper.StatusHandler(rec, req)
	if err := json.Unmarshal(rec.Body.Bytes(), &body); err != nil {
		t.Fatalf("paper status response is not JSON: %v", err)
	}
	if body.Mode != Paper || !body.DecisionsEnabled || !body.ExecutionEnabled {
		t.Errorf("paper status = %+v, want mode=paper decisionsEnabled=true executionEnabled=true", body)
	}

	// The flag is server-owned: no method other than GET is served.
	rec = httptest.NewRecorder()
	Paper.StatusHandler(rec, httptest.NewRequest(http.MethodPost, "/api/operating-mode", nil))
	if rec.Code != http.StatusMethodNotAllowed {
		t.Errorf("POST /api/operating-mode = %d, want 405", rec.Code)
	}
}

// The interceptor must deny the call before the service handler runs.
func TestUnaryInterceptorLearningDeniesBeforeHandler(t *testing.T) {
	called := false
	handler := func(ctx context.Context, req any) (any, error) {
		called = true
		return "ok", nil
	}
	interceptor := Learning.UnaryServerInterceptor()
	_, err := interceptor(context.Background(), "req",
		&grpc.UnaryServerInfo{FullMethod: "/proto.SignalService/SubmitSignal"}, handler)

	if called {
		t.Error("gRPC service handler was invoked in learning mode")
	}
	if status.Code(err) != codes.FailedPrecondition {
		t.Errorf("gRPC denial code = %v, want FailedPrecondition (err=%v)", status.Code(err), err)
	}
}

func TestUnaryInterceptorPaperPassesThrough(t *testing.T) {
	called := false
	handler := func(ctx context.Context, req any) (any, error) {
		called = true
		return "ok", nil
	}
	resp, err := Paper.UnaryServerInterceptor()(context.Background(), "req",
		&grpc.UnaryServerInfo{FullMethod: "/proto.SignalService/SubmitSignal"}, handler)
	if err != nil || !called || resp != "ok" {
		t.Errorf("paper interceptor = resp %v, err %v, called %v; want pass-through", resp, err, called)
	}
}

// Regression: the interceptor must deny on m != Paper — a zero-value or
// otherwise invalid Mode must fail closed before the service handler runs.
func TestUnaryInterceptorDeniesNonPaperModes(t *testing.T) {
	for _, m := range []Mode{"", "invalid", Learning} {
		called := false
		handler := func(ctx context.Context, req any) (any, error) {
			called = true
			return "ok", nil
		}
		_, err := m.UnaryServerInterceptor()(context.Background(), "req",
			&grpc.UnaryServerInfo{FullMethod: "/proto.SignalService/SubmitSignal"}, handler)
		if called {
			t.Errorf("Mode(%q): gRPC service handler was invoked", m)
		}
		if status.Code(err) != codes.FailedPrecondition {
			t.Errorf("Mode(%q): gRPC denial code = %v, want FailedPrecondition", m, status.Code(err))
		}
	}
}

// Broker mutation policy: everything except explicit paper + PAPER_TRADING=true
// + the exact paper URL must fail closed.
func TestCheckBrokerMutation(t *testing.T) {
	if err := Paper.CheckBrokerMutation("true", PaperBrokerBaseURL); err != nil {
		t.Errorf("valid paper config rejected: %v", err)
	}
	if err := Paper.CheckBrokerMutation("TRUE", PaperBrokerBaseURL); err != nil {
		t.Errorf("PAPER_TRADING=TRUE should be accepted (case-insensitive): %v", err)
	}

	// Learning mode blocks regardless of broker env.
	for _, pt := range []string{"", "false", "true"} {
		if err := Learning.CheckBrokerMutation(pt, PaperBrokerBaseURL); err == nil {
			t.Errorf("learning mode allowed mutation with PAPER_TRADING=%q", pt)
		} else if !strings.Contains(err.Error(), "learning_only") {
			t.Errorf("learning denial should be identifiable, got: %v", err)
		}
	}

	// Paper mode without explicit PAPER_TRADING=true blocks.
	for _, pt := range []string{"", "false", "0", "yes", "on"} {
		if err := Paper.CheckBrokerMutation(pt, PaperBrokerBaseURL); err == nil {
			t.Errorf("paper mode allowed mutation with PAPER_TRADING=%q", pt)
		}
	}

	// Any deviation from the exact paper URL blocks: live host, insecure
	// scheme, trailing slash, lookalike host, userinfo, path suffix.
	badURLs := []string{
		"",
		"https://api.alpaca.markets",
		"http://paper-api.alpaca.markets",
		"https://paper-api.alpaca.markets/",
		"https://paper-api.alpaca.markets.evil.example",
		"https://user:secret@paper-api.alpaca.markets",
		"https://paper-api.alpaca.markets/v2",
	}
	for _, u := range badURLs {
		if err := Paper.CheckBrokerMutation("true", u); err == nil {
			t.Errorf("paper mode allowed mutation with base URL %q", u)
		}
	}
}
