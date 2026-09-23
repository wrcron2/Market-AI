package learnerexecution

import (
	"io"
	"net/http"
)

// NewIntentHandler verifies request identity and wire provenance before handing
// exact bytes to the durable execution service. That service checks existing
// decision IDs BEFORE freshness, so an expired retry can return its old result.
// It must validate freshness/safety before submitting any new intent.
func NewIntentHandler(auth *Authenticator, submit func(http.ResponseWriter, *http.Request, Intent, []byte)) http.Handler {
	if auth == nil || submit == nil {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			http.Error(w, "execution_not_configured", http.StatusServiceUnavailable)
		})
	}
	return auth.Middleware(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != "POST" || r.URL.Path != "/api/learner/intents" || r.URL.RawQuery != "" {
			http.Error(w, "unsupported_intent_route", http.StatusNotFound)
			return
		}
		raw, e := io.ReadAll(r.Body)
		if e != nil {
			http.Error(w, "invalid_intent_body", http.StatusBadRequest)
			return
		}
		intent, e := DecodeIntent(raw)
		if e != nil {
			http.Error(w, "invalid_intent", http.StatusBadRequest)
			return
		}
		if !auth.Owns(intent) {
			http.Error(w, "intent_scope_mismatch", http.StatusForbidden)
			return
		}
		submit(w, r, intent, raw)
	}))
}
