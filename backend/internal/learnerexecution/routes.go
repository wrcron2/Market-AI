package learnerexecution

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/url"
	"strings"
	"time"
)

type ExecutionView struct {
	Intent    Intent           `json:"intent"`
	Execution ExecutionRecord  `json:"execution"`
	Events    []ExecutionEvent `json:"events"`
}

func NewHandler(auth *Authenticator, service *Service) (http.Handler, error) {
	if auth == nil || service == nil || auth.ownerID != service.store.ownerID || auth.accountID != service.store.accountID {
		return nil, errors.New("learner_handler_scope_mismatch")
	}
	return auth.Middleware(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Cache-Control", "no-store")
		raw, err := io.ReadAll(r.Body)
		if err != nil {
			http.Error(w, "invalid_body", 400)
			return
		}
		respond := func(code int, value any) {
			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(code)
			_ = json.NewEncoder(w).Encode(value)
		}
		failure := func(err error) {
			switch {
			case errors.Is(err, ErrConflict):
				http.Error(w, "decision_id_body_conflict", 409)
			case errors.Is(err, ErrNotFound):
				http.Error(w, "intent_not_found", 404)
			case errors.Is(err, ErrScope):
				http.Error(w, "intent_scope_mismatch", 403)
			default:
				http.Error(w, "execution_service_unavailable", 503)
			}
		}
		if r.Method == http.MethodGet && r.URL.Path == "/api/learner/observations" {
			query, err := url.ParseQuery(r.URL.RawQuery)
			if err != nil || len(raw) != 0 || len(query) != 1 || len(query["symbol"]) != 1 || !symbolPattern.MatchString(query.Get("symbol")) {
				http.Error(w, "invalid_observation_query", 400)
				return
			}
			o, err := service.Observe(r.Context(), query.Get("symbol"))
			if err != nil {
				failure(err)
				return
			}
			respond(200, o)
			return
		}
		var record ExecutionRecord
		code := http.StatusOK
		switch {
		case r.Method == http.MethodPost && r.URL.Path == "/api/learner/intents" && r.URL.RawQuery == "":
			i, e := DecodeIntent(raw)
			if e != nil {
				http.Error(w, "invalid_intent", 400)
				return
			}
			if !auth.Owns(i) {
				http.Error(w, "intent_scope_mismatch", 403)
				return
			}
			record, err = service.Submit(r.Context(), i, raw)
			code = http.StatusAccepted
		case (r.Method == http.MethodGet || r.Method == http.MethodDelete) && strings.HasPrefix(r.URL.Path, "/api/learner/intents/") && r.URL.RawQuery == "" && len(raw) == 0:
			id := strings.TrimPrefix(r.URL.Path, "/api/learner/intents/")
			if !uuidPattern.MatchString(id) {
				http.Error(w, "invalid_decision_id", 400)
				return
			}
			if r.Method == http.MethodDelete {
				record, err = service.Cancel(r.Context(), id)
				code = http.StatusAccepted
			} else {
				record, err = service.Reconcile(r.Context(), id)
			}
		default:
			http.Error(w, "unsupported_learner_route", 404)
			return
		}
		if err != nil {
			failure(err)
			return
		}
		view, err := service.store.View(r.Context(), record.DecisionID)
		if err != nil {
			failure(err)
			return
		}
		respond(code, view)
	})), nil
}

func (s *Service) Cancel(ctx context.Context, id string) (ExecutionRecord, error) {
	r, err := s.Reconcile(ctx, id)
	if err != nil {
		return r, err
	}
	if r.State != "accepted" && r.State != "partially_filled" {
		return r, nil
	}
	broker, ok := s.broker.(Broker)
	if !ok {
		return ExecutionRecord{}, errors.New("cancellation_not_configured")
	}
	requested := false
	err = s.store.immediate(ctx, func(c *sql.Conn) error {
		latest, err := scanRecord(c.QueryRowContext(ctx, "SELECT "+recordColumns+" FROM learner_intents WHERE decision_id=? AND owner_id=? AND account_id=?", id, s.store.ownerID, s.store.accountID))
		if err != nil {
			return err
		}
		if latest.State != "accepted" && latest.State != "partially_filled" {
			return nil
		}
		if err = appendEvent(ctx, c, id, "cancel_requested", "", s.now()); err != nil {
			return err
		}
		requested = true
		return nil
	})
	if err != nil {
		return ExecutionRecord{}, err
	}
	if !requested {
		return s.store.Get(ctx, id)
	}
	cancelErr := broker.Cancel(ctx, r.ClientOrderID, r.BrokerOrderID)
	writeCtx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	state := "cancel_request_accepted"
	if cancelErr != nil {
		state = "cancel_outcome_unknown"
	}
	err = s.store.immediate(writeCtx, func(c *sql.Conn) error { return appendEvent(writeCtx, c, id, state, "", s.now()) })
	if err != nil {
		return ExecutionRecord{}, err
	}
	return s.Reconcile(writeCtx, id)
}
