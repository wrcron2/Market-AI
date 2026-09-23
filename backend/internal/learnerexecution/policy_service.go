package learnerexecution

import (
	"context"
	"database/sql"
	"errors"
	"time"
)

// The public factory always binds a concrete mechanical policy to a paper-only
// adapter. Production callers cannot supply a strategy or approval callback.
func NewExecutionService(store *Store, broker *PaperBroker, policy SafetyPolicy) (*Service, error) {
	if broker == nil {
		return nil, errors.New("paper_broker_not_configured")
	}
	return newPolicyService(store, broker, policy)
}
func newPolicyService(store *Store, broker Broker, policy SafetyPolicy) (*Service, error) {
	if _, err := policy.externalPositions(); err != nil {
		return nil, err
	}
	if store == nil || broker == nil || policy.AccountID != store.accountID {
		return nil, ErrScope
	}
	gate := &policyGate{store: store, broker: broker, policy: policy}
	s, err := newService(store, broker, gate)
	if err != nil {
		return nil, err
	}
	gate.now = func() time.Time { return s.now() }
	return s, nil
}

type policyGate struct {
	store  *Store
	broker Broker
	policy SafetyPolicy
	now    func() time.Time
}

func (g *policyGate) assess(ctx context.Context, i Intent) (SafetyDecision, error) {
	d := SafetyDecision{PolicyVersion: g.policy.Version, DailyLimit: g.policy.MaxDaily}
	if !g.policy.Enabled {
		d.ReasonCode = "execution_disabled"
		return d, nil
	}
	if g.policy.KillSwitch {
		d.ReasonCode = "kill_switch"
		return d, nil
	}
	reference, err := g.store.observation(ctx, i.ObservationID)
	if errors.Is(err, ErrNotFound) {
		d.ReasonCode = "observation_reference_unavailable"
		return d, nil
	}
	if err != nil {
		return d, err
	}
	current, err := g.broker.Observe(ctx, i.Symbol)
	if err != nil {
		return d, err
	}
	// No broker work is done inside this function: it runs under the journal's
	// immediate transaction both when reserving and immediately before claiming.
	d.Allowed = true
	d.checkReservation = func(ctx context.Context, c *sql.Conn) (SafetyDecision, error) {
		exposure, err := g.store.exposure(ctx, c, i.DecisionID)
		if err != nil {
			return SafetyDecision{}, err
		}
		return g.policy.check(i, reference, current, exposure, g.now()), nil
	}
	return d, nil
}

func (s *Service) Observe(ctx context.Context, symbol string) (BrokerSnapshot, error) {
	broker, ok := s.broker.(Broker)
	if !ok {
		return BrokerSnapshot{}, errors.New("observations_not_configured")
	}
	o, err := broker.Observe(ctx, symbol)
	if err != nil {
		return BrokerSnapshot{}, err
	}
	err = s.store.immediate(ctx, func(c *sql.Conn) error {
		exposure, err := s.store.exposure(ctx, c, "")
		if err != nil {
			return err
		}
		value := func(values map[string]string) string {
			if text, ok := values[symbol]; ok {
				return text
			}
			return "0"
		}
		o.OwnedQuantity = value(exposure.Owned)
		o.PendingBuyNotional = value(exposure.PendingBuy)
		o.PendingSellQuantity = value(exposure.PendingSell)
		policy := SafetyPolicy{}
		if gate, ok := s.gate.(*policyGate); ok {
			policy = gate.policy
		}
		external, parseErr := policy.externalPositions()
		_, reserved := external[symbol]
		o.ReservedSymbol = reserved
		o.UnresolvedExecution = parseErr != nil || reserved || policy.ownershipEvidenceCode(o, exposure, s.now()) != ""
		return nil
	})
	if err != nil {
		return BrokerSnapshot{}, err
	}
	return s.store.saveObservation(ctx, o)
}
