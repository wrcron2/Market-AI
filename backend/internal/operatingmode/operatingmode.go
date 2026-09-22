// Package operatingmode defines the process-wide operating mode policy for
// Market AI (Block 1: learning-only operation).
//
// The mode is read once from MARKET_AI_OPERATING_MODE at process start and is
// immutable for the process lifetime — no API endpoint can change it. Only
// the exact value "paper" permits the legacy paper-trading decision/execution
// behavior. Missing, empty, or unknown values fail closed to "learning",
// where the process produces no trading decisions and cannot mutate the
// broker. There is deliberately no live mode.
package operatingmode

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"os"
	"strings"

	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
)

// EnvVar is the environment variable that selects the operating mode.
const EnvVar = "MARKET_AI_OPERATING_MODE"

// PaperBrokerBaseURL is the only broker base URL from which order mutations
// are accepted. Anything else — a live host, an added suffix or path,
// userinfo, or an insecure scheme — fails closed.
const PaperBrokerBaseURL = "https://paper-api.alpaca.markets"

// Mode is the operating mode of a Market AI process.
type Mode string

const (
	// Learning disables all decision-producing and broker-mutating behavior.
	Learning Mode = "learning"
	// Paper permits the legacy paper-trading behavior.
	Paper Mode = "paper"
)

// Parse maps a raw MARKET_AI_OPERATING_MODE value to a Mode. Only the exact
// string "paper" selects paper; every other value (including empty) fails
// closed to learning.
func Parse(raw string) Mode {
	if raw == string(Paper) {
		return Paper
	}
	return Learning
}

// FromEnv reads the operating mode from the environment. Call once at process
// start; the returned Mode is the immutable policy for the process lifetime.
func FromEnv() Mode {
	return Parse(os.Getenv(EnvVar))
}

// DecisionsEnabled reports whether decision-producing behavior (signal
// scanning, the market-hours auto-execute watcher, scout/research runs) may
// run in this mode.
func (m Mode) DecisionsEnabled() bool { return m == Paper }

// ExecutionEnabled reports whether broker mutations (order submit/cancel,
// position close) may run in this mode.
func (m Mode) ExecutionEnabled() bool { return m == Paper }

// statusResponse is the JSON body of GET /api/operating-mode.
type statusResponse struct {
	Mode             Mode `json:"mode"`
	DecisionsEnabled bool `json:"decisionsEnabled"`
	ExecutionEnabled bool `json:"executionEnabled"`
}

// StatusHandler serves GET /api/operating-mode. The reported flag is
// server-owned and immutable for the process lifetime — there is no setter
// endpoint and no way to toggle the mode over the API.
func (m Mode) StatusHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(statusResponse{
		Mode:             m,
		DecisionsEnabled: m.DecisionsEnabled(),
		ExecutionEnabled: m.ExecutionEnabled(),
	})
}

// Middleware rejects every non-read request with 423 Locked unless the
// process runs in paper mode, before any business handler is invoked. GET,
// HEAD, and OPTIONS requests (reads and health) always pass through. The
// comparison is m != Paper (not m == Learning) so a zero-value or otherwise
// invalid Mode also denies.
func (m Mode) Middleware(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if m != Paper && r.Method != http.MethodGet &&
			r.Method != http.MethodHead && r.Method != http.MethodOptions {
			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(http.StatusLocked)
			_ = json.NewEncoder(w).Encode(map[string]string{
				"error": "learning_only",
				"message": "Market AI is in learning-only mode: trading decisions and " +
					"broker mutations are disabled; read-only access remains available",
			})
			return
		}
		next.ServeHTTP(w, r)
	})
}

// UnaryServerInterceptor rejects every gRPC service call with
// FailedPrecondition unless the process runs in paper mode, before any service
// handler is invoked. The comparison is m != Paper (not m == Learning) so a
// zero-value or otherwise invalid Mode also denies.
func (m Mode) UnaryServerInterceptor() grpc.UnaryServerInterceptor {
	return func(ctx context.Context, req any, info *grpc.UnaryServerInfo, handler grpc.UnaryHandler) (any, error) {
		if m != Paper {
			return nil, status.Error(codes.FailedPrecondition,
				"learning_only: Market AI is in learning-only mode; gRPC service calls are disabled")
		}
		return handler(ctx, req)
	}
}

// CheckBrokerMutation enforces the broker boundary for order submit/cancel
// and position close. It must be called BEFORE any network request, including
// preliminary GETs a mutation flow might otherwise issue. Read-only broker
// calls are never gated by this function.
//
// A mutation requires ALL of the following, failing closed on any doubt:
//   - operating mode exactly "paper";
//   - PAPER_TRADING explicitly "true" (case-insensitive);
//   - broker base URL exactly PaperBrokerBaseURL — a live host, any suffix or
//     path, userinfo, or an insecure scheme all fail closed.
func (m Mode) CheckBrokerMutation(paperTrading, baseURL string) error {
	if m != Paper {
		return errors.New("learning_only: broker mutations are disabled unless MARKET_AI_OPERATING_MODE=paper")
	}
	if !strings.EqualFold(strings.TrimSpace(paperTrading), "true") {
		return errors.New("PAPER_TRADING=true is required for broker mutations")
	}
	if baseURL != PaperBrokerBaseURL {
		// Deliberately does not echo baseURL: a rejected URL may carry userinfo.
		return fmt.Errorf("broker base URL is not exactly %s — fail closed", PaperBrokerBaseURL)
	}
	return nil
}
