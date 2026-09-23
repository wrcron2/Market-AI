package learnerexecution

import (
	"context"
	"database/sql"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"testing"
)

func TestSignedRoutesObservationSubmitStatusAndCancel(t *testing.T) {
	s := executionStore(t, filepath.Join(t.TempDir(), "routes.db"))
	b := &policyBroker{journalBroker: newJournalBroker(), snapshot: freshSnapshot()}
	service := policyService(t, s, b, pilotPolicy())
	nonces, err := NewNonceStore(s.db)
	if err != nil {
		t.Fatal(err)
	}
	auth, err := NewAuthenticator(AuthConfig{Key: []byte(testKey), OwnerID: "1", AccountID: "paper-account-1"}, nonces)
	if err != nil {
		t.Fatal(err)
	}
	auth.now = fixtureTime
	handler, err := NewHandler(auth, service)
	if err != nil {
		t.Fatal(err)
	}
	n := 0
	request := func(method, target, body string) *httptest.ResponseRecorder {
		n++
		req := signedRequest(method, target, body, testKey, fmt.Sprintf("12345678-1234-4234-8234-%012d", n), fixtureTime().Unix())
		w := httptest.NewRecorder()
		handler.ServeHTTP(w, req)
		return w
	}
	w := request("GET", "/api/learner/observations?symbol=AAPL", "")
	if w.Code != 200 {
		t.Fatalf("observe %d %s", w.Code, w.Body)
	}
	var observation BrokerSnapshot
	if err = json.Unmarshal(w.Body.Bytes(), &observation); err != nil {
		t.Fatal(err)
	}
	i, _ := DecodeIntent(fixtureBytes(t))
	i.ObservationID = observation.ID
	raw, _ := json.Marshal(i)
	w = request("POST", "/api/learner/intents", string(raw))
	if w.Code != 202 {
		t.Fatalf("submit %d %s", w.Code, w.Body)
	}
	var view ExecutionView
	if err = json.Unmarshal(w.Body.Bytes(), &view); err != nil {
		t.Fatal(err)
	}
	if view.Execution.State != "accepted" || view.Intent.DecisionID != i.DecisionID || len(view.Events) < 3 || b.submits != 1 {
		t.Fatalf("view %+v submits=%d", view.Execution, b.submits)
	}
	w = request("GET", "/api/learner/intents/"+i.DecisionID, "")
	if w.Code != 200 {
		t.Fatalf("status %d %s", w.Code, w.Body)
	}
	w = request("DELETE", "/api/learner/intents/"+i.DecisionID, "")
	if w.Code != 202 {
		t.Fatalf("cancel %d %s", w.Code, w.Body)
	}
	if err = json.Unmarshal(w.Body.Bytes(), &view); err != nil {
		t.Fatal(err)
	}
	if view.Execution.State != "accepted" || b.submits != 1 {
		t.Fatalf("cancel acknowledgement invented terminal state: %s", view.Execution.State)
	}
	found := false
	for _, event := range view.Events {
		if event.State == "cancel_requested" {
			found = true
		}
	}
	if !found {
		t.Fatal("cancellation not durably recorded")
	}
	// Long-lived reconciliation must not pin the UI to its first 100 events.
	if err = s.immediate(context.Background(), func(c *sql.Conn) error {
		for n := 0; n < 110; n++ {
			if e := appendEvent(context.Background(), c, i.DecisionID, "cancel_requested", "", fixtureTime()); e != nil {
				return e
			}
		}
		return appendEvent(context.Background(), c, i.DecisionID, "cancel_outcome_unknown", "", fixtureTime())
	}); err != nil {
		t.Fatal(err)
	}
	w = request("GET", "/api/learner/intents/"+i.DecisionID, "")
	if err = json.Unmarshal(w.Body.Bytes(), &view); err != nil {
		t.Fatal(err)
	}
	if len(view.Events) != 100 || view.Events[len(view.Events)-1].State != "cancel_outcome_unknown" {
		t.Fatal("response did not include the latest bounded event window")
	}
	w = request("POST", "/api/learner/intents", string(append(raw, ' ')))
	if w.Code != 409 || b.submits != 1 {
		t.Fatalf("conflicting retry %d", w.Code)
	}
	unauthorized := httptest.NewRecorder()
	handler.ServeHTTP(unauthorized, httptest.NewRequest(http.MethodGet, "/api/learner/intents/"+i.DecisionID, nil))
	if unauthorized.Code != 401 {
		t.Fatal("unsigned read accepted")
	}
}

func TestRoutesFailClosedForMismatchedAuthScope(t *testing.T) {
	s := executionStore(t, filepath.Join(t.TempDir(), "routes.db"))
	b := &policyBroker{journalBroker: newJournalBroker(), snapshot: freshSnapshot()}
	service := policyService(t, s, b, pilotPolicy())
	nonces, err := NewNonceStore(s.db)
	if err != nil {
		t.Fatal(err)
	}
	auth, err := NewAuthenticator(AuthConfig{Key: []byte(testKey), OwnerID: "2", AccountID: "paper-account-1"}, nonces)
	if err != nil {
		t.Fatal(err)
	}
	if _, err = NewHandler(auth, service); err == nil {
		t.Fatal("GET routes would expose another owner's store")
	}
}

func TestCancellationCannotTargetUnknownOrUnsubmittedDecision(t *testing.T) {
	s := executionStore(t, filepath.Join(t.TempDir(), "cancel.db"))
	b := &policyBroker{journalBroker: newJournalBroker(), snapshot: freshSnapshot()}
	service := policyService(t, s, b, pilotPolicy())
	if _, err := service.Cancel(context.Background(), testNonce); err == nil {
		t.Fatal("unknown cancellation accepted")
	}
	i, _ := DecodeIntent(fixtureBytes(t))
	i.Action = "HOLD"
	i.Order = nil
	raw, _ := json.Marshal(i)
	r, err := service.Submit(context.Background(), i, raw)
	if err != nil {
		t.Fatal(err)
	}
	r, err = service.Cancel(context.Background(), r.DecisionID)
	if err != nil || r.State != "hold_recorded" || b.lookups != 0 || b.submits != 0 {
		t.Fatalf("HOLD mutated broker %v %s", err, r.State)
	}
}
