// Package decisionauthority keeps economic ownership separate from operating mode.
package decisionauthority

import (
	"encoding/json"
	"github.com/marketflow/backend/internal/operatingmode"
	"net/http"
	"os"
)

type Authority string

const (
	Disabled Authority = "DISABLED"
	Kimi     Authority = "KIMI"
	Legacy   Authority = "LEGACY"
)

func Parse(raw string) Authority {
	switch raw {
	case "KIMI":
		return Kimi
	case "LEGACY":
		return Legacy
	default:
		return Disabled
	}
}
func FromEnv() Authority                { return Parse(os.Getenv("DECISION_AUTHORITY")) }
func (a Authority) KimiEnabled() bool   { return a == Kimi }
func (a Authority) LegacyEnabled() bool { return a == Legacy }

// NewMux deliberately has no legacy handlers, fallback or strategy dependencies.
// The authenticated learner handler must independently enforce paper/risk policy.
func NewMux(a Authority, mode operatingmode.Mode, learner http.Handler, submissionEnabled ...bool) *http.ServeMux {
	enabled := len(submissionEnabled) == 1 && submissionEnabled[0]
	mux := http.NewServeMux()
	mux.HandleFunc("GET /healthz", func(w http.ResponseWriter, r *http.Request) { w.WriteHeader(http.StatusOK) })
	mux.HandleFunc("GET /api/operating-mode", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(map[string]any{
			"mode": mode, "decisionAuthority": a, "decisionsEnabled": false,
			"learnerConfigured": a.KimiEnabled() && learner != nil,
			"executionEnabled":  a.KimiEnabled() && mode == operatingmode.Paper && learner != nil && enabled,
		})
	})
	if a.KimiEnabled() && learner != nil {
		mux.Handle("/api/learner/", learner)
	}
	return mux
}
