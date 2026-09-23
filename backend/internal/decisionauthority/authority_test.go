package decisionauthority

import (
	"encoding/json"
	"github.com/marketflow/backend/internal/operatingmode"
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestHandlerPresenceDoesNotClaimSubmissionEnabled(t *testing.T) {
	for _, enabled := range []bool{false, true} {
		mux := NewMux(Kimi, operatingmode.Paper, http.HandlerFunc(func(http.ResponseWriter, *http.Request) {}), enabled)
		w := httptest.NewRecorder()
		mux.ServeHTTP(w, httptest.NewRequest("GET", "/api/operating-mode", nil))
		var status map[string]any
		if err := json.Unmarshal(w.Body.Bytes(), &status); err != nil {
			t.Fatal(err)
		}
		if status["executionEnabled"] != enabled || status["learnerConfigured"] != true || status["decisionsEnabled"] != false {
			t.Fatalf("incorrect status %+v", status)
		}
	}
}

func TestAuthorityFailsClosed(t *testing.T) {
	for _, raw := range []string{"", "kimi", "legacy", " KIMI ", "LIVE", "unknown"} {
		a := Parse(raw)
		if a.KimiEnabled() || a.LegacyEnabled() {
			t.Fatalf("enabled %q", raw)
		}
	}
	if !Parse("KIMI").KimiEnabled() || Parse("KIMI").LegacyEnabled() {
		t.Fatal("KIMI isolation")
	}
	if !Parse("LEGACY").LegacyEnabled() || Parse("LEGACY").KimiEnabled() {
		t.Fatal("LEGACY isolation")
	}
}

func TestDedicatedRoutesNeverReachLegacy(t *testing.T) {
	for _, a := range []Authority{Parse("KIMI"), Parse(""), Parse("invalid")} {
		called := 0
		mux := NewMux(a, operatingmode.Paper, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { called++; w.WriteHeader(202) }))
		for _, method := range []string{"GET", "POST", "DELETE"} {
			for _, path := range []string{"/api/orders/approve", "/api/orders/retry", "/api/orders/auto-execute", "/api/alpaca/positions/AAPL/close", "/api/signals", "/api/mode"} {
				rec := httptest.NewRecorder()
				mux.ServeHTTP(rec, httptest.NewRequest(method, path, nil))
				if rec.Code != 404 {
					t.Fatalf("legacy route reachable: %s %s = %d", method, path, rec.Code)
				}
			}
		}
		if called != 0 {
			t.Fatal("legacy route dispatched to execution")
		}
		rec := httptest.NewRecorder()
		mux.ServeHTTP(rec, httptest.NewRequest("POST", "/api/learner/intents", nil))
		if a.KimiEnabled() && (rec.Code != 202 || called != 1) {
			t.Fatal("KIMI ingress unavailable")
		}
		if !a.KimiEnabled() && called != 0 {
			t.Fatal("disabled authority reached executor")
		}
	}
}
