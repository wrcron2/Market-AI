package learnerexecution

import (
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"
)

func TestRuntimeFailsBeforeCreatingDatabaseOnMissingCredentials(t *testing.T) {
	path := filepath.Join(t.TempDir(), "missing.db")
	_, err := OpenRuntime(RuntimeConfig{DatabasePath: path, OwnerID: "1", Broker: paperConfig(), Policy: pilotPolicy()})
	if err == nil {
		t.Fatal("missing signing key accepted")
	}
	if _, err = os.Stat(path); !os.IsNotExist(err) {
		t.Fatal("unconfigured runtime touched database")
	}
}

func TestRuntimeKeepsAuthenticatedReadPathButDisablesUnapprovedSubmission(t *testing.T) {
	for _, mode := range []string{"disabled", "kill", "missing limits", "enabled"} {
		t.Run(mode, func(t *testing.T) {
			p := pilotPolicy()
			switch mode {
			case "disabled":
				p.Enabled = false
			case "kill":
				p.KillSwitch = true
			case "missing limits":
				p.MaxOrder = ""
			}
			r, err := OpenRuntime(RuntimeConfig{DatabasePath: filepath.Join(t.TempDir(), "runtime.db"), OwnerID: "1", HMACKey: []byte(testKey), Broker: paperConfig(), Policy: p})
			if err != nil {
				t.Fatal(err)
			}
			defer r.Close()
			if r.SubmissionEnabled != (mode == "enabled") {
				t.Fatalf("wrong submission flag %v", r.SubmissionEnabled)
			}
			w := httptest.NewRecorder()
			r.Handler.ServeHTTP(w, httptest.NewRequest(http.MethodGet, "/api/learner/intents/"+testNonce, nil))
			if w.Code != http.StatusUnauthorized {
				t.Fatalf("unauthed runtime endpoint %d", w.Code)
			}
		})
	}
}

func TestRuntimeEnvironmentDefaultsFailClosed(t *testing.T) {
	for _, key := range []string{"KIMI_EXECUTION_ENABLED", "KIMI_KILL_SWITCH", "KIMI_OBSERVATION_MAX_AGE_SECONDS", "KIMI_MAX_ORDER_NOTIONAL", "KIMI_MAX_POSITION_NOTIONAL", "KIMI_MAX_PORTFOLIO_NOTIONAL", "KIMI_MAX_DAILY_NOTIONAL"} {
		t.Setenv(key, "")
	}
	c := RuntimeConfigFromEnv()
	if c.Policy.Enabled || !c.Policy.KillSwitch || c.Policy.MaxAge != 0 || c.Policy.configured() {
		t.Fatal("empty environment enabled execution")
	}
	t.Setenv("KIMI_EXECUTION_ENABLED", "TRUE")
	t.Setenv("KIMI_KILL_SWITCH", "False")
	c = RuntimeConfigFromEnv()
	if c.Policy.Enabled || !c.Policy.KillSwitch {
		t.Fatal("inexact enable switch accepted")
	}
}
