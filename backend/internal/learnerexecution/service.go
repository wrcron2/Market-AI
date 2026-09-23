package learnerexecution

import (
	"bytes"
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"reflect"
	"time"
)

// SubmissionBroker has no decision, strategy or model API. The concrete paper
// adapter will additionally provide account observations and cancellation.
type SubmissionBroker interface {
	Submit(context.Context, Intent, string) (BrokerOrder, error)
	LookupByClientID(context.Context, string) (*BrokerOrder, error)
}

type SafetyDecision struct {
	Allowed          bool              `json:"allowed"`
	ReasonCode       string            `json:"reasonCode"`
	PolicyVersion    string            `json:"policyVersion"`
	DailyLimit       string            `json:"dailyLimit"`
	ValidUntil       *time.Time        `json:"validUntil,omitempty"`
	Measurements     map[string]string `json:"measurements,omitempty"`
	checkReservation func(context.Context, *sql.Conn) (SafetyDecision, error)
}

func (d *SafetyDecision) measure(key, value string) {
	if d.Measurements == nil {
		d.Measurements = map[string]string{}
	}
	d.Measurements[key] = value
}

// Package-private until the concrete mechanical policy is connected. There is
// deliberately no exported constructor accepting an arbitrary approval callback.
type mechanicalGate interface {
	assess(context.Context, Intent) (SafetyDecision, error)
}

type Service struct {
	store  *Store
	broker SubmissionBroker
	gate   mechanicalGate
	now    func() time.Time
}

func newService(store *Store, broker SubmissionBroker, gate mechanicalGate) (*Service, error) {
	if store == nil || broker == nil || gate == nil {
		return nil, errors.New("execution_not_configured")
	}
	return &Service{store: store, broker: broker, gate: gate, now: time.Now}, nil
}

func (s *Service) Submit(ctx context.Context, intent Intent, rawBody []byte) (ExecutionRecord, error) {
	// Validate both representations before any external call. Thereafter use only
	// the decoded immutable copy, not caller-owned maps, slices or order pointers.
	raw := bytes.Clone(rawBody)
	i, err := DecodeIntent(raw)
	if err != nil {
		return ExecutionRecord{}, err
	}
	if !reflect.DeepEqual(i, intent) {
		return ExecutionRecord{}, errors.New("intent_body_mismatch")
	}
	if i.OwnerID != s.store.ownerID || i.AccountID != s.store.accountID {
		return ExecutionRecord{}, ErrScope
	}
	r, err := s.store.Get(ctx, i.DecisionID)
	if err == nil {
		if !bytes.Equal(r.RawBody, raw) {
			return ExecutionRecord{}, ErrConflict
		}
		// A submitting claim is never replayed, even if no order can be found yet.
		if r.State != "reserved" {
			return r, nil
		}
	} else if !errors.Is(err, ErrNotFound) {
		return ExecutionRecord{}, err
	}
	decision := SafetyDecision{Allowed: true, PolicyVersion: i.PolicyVersion}
	if i.Action != "HOLD" && i.ValidateFresh(s.now()) == nil {
		decision, err = s.gate.assess(ctx, i)
		if err != nil {
			decision = SafetyDecision{Allowed: false, ReasonCode: "safety_observation_unavailable", PolicyVersion: i.PolicyVersion}
		}
		if decision.PolicyVersion != i.PolicyVersion {
			decision.Allowed = false
			decision.ReasonCode = "policy_version_mismatch"
		}
		if !decision.Allowed && decision.ReasonCode == "" {
			decision.ReasonCode = "mechanical_safety_rejected"
		}
	}
	denial := ""
	if !decision.Allowed {
		denial = decision.ReasonCode
	}
	r, _, err = s.store.reserve(ctx, raw, s.now(), decision.DailyLimit, denial, decision)
	if err != nil || r.State != "reserved" {
		return r, err
	}
	// A retry of a pre-claim reservation must not bypass a newly failed gate.
	if !decision.Allowed {
		if err = s.store.rejectUnsent(ctx, i.DecisionID, denial, s.now()); err != nil {
			return ExecutionRecord{}, err
		}
		return s.store.Get(ctx, i.DecisionID)
	}
	won, err := s.store.claim(ctx, i.DecisionID, s.now(), decision)
	if err != nil {
		return ExecutionRecord{}, err
	}
	if !won {
		return s.store.Get(ctx, i.DecisionID)
	}
	r, err = s.store.Get(ctx, i.DecisionID)
	if err != nil {
		return ExecutionRecord{}, err
	}
	var safety SafetyDecision
	if len(r.Safety) > 0 {
		if err = json.Unmarshal(r.Safety, &safety); err != nil {
			return ExecutionRecord{}, err
		}
	}
	submitCtx := ctx
	if safety.ValidUntil != nil {
		var cancel context.CancelFunc
		submitCtx, cancel = context.WithTimeout(ctx, safety.ValidUntil.Sub(s.now()))
		defer cancel()
	}
	order, brokerErr := s.broker.Submit(submitCtx, i, r.ClientOrderID)
	// The request may have timed out. Use a bounded independent context to save
	// the outcome; a failed write still leaves the durable submitting claim.
	writeCtx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	if brokerErr != nil {
		err = s.store.uncertain(writeCtx, i.DecisionID, s.now())
	} else {
		err = s.store.observe(writeCtx, i.DecisionID, order, s.now())
	}
	if err != nil {
		return ExecutionRecord{}, err
	}
	return s.store.Get(writeCtx, i.DecisionID)
}

func (s *Service) Reconcile(ctx context.Context, decisionID string) (ExecutionRecord, error) {
	r, err := s.store.Get(ctx, decisionID)
	if err != nil {
		return r, err
	}
	if r.State == "reserved" || r.State == "hold_recorded" || (r.State == "rejected" && r.BrokerOrderID == "") {
		return r, nil
	}
	o, lookupErr := s.broker.LookupByClientID(ctx, r.ClientOrderID)
	writeCtx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	if lookupErr != nil || o == nil {
		// A not-found response does NOT establish that a timed-out submission
		// never happened. Keep its identity and reservation; never auto-resubmit.
		err = s.store.uncertain(writeCtx, decisionID, s.now())
	} else {
		err = s.store.observe(writeCtx, decisionID, *o, s.now())
	}
	if err != nil {
		return ExecutionRecord{}, err
	}
	return s.store.Get(writeCtx, decisionID)
}
