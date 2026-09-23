package learnerexecution

import (
	"context"
	"encoding/json"
	"fmt"
	"path/filepath"
	"sync"
	"testing"
	"time"
)

type policyBroker struct {
	*journalBroker
	snapshot BrokerSnapshot
}

func (b *policyBroker) Observe(_ context.Context, symbol string) (BrokerSnapshot, error) {
	o := b.snapshot
	o.Symbol = symbol
	return o, nil
}
func (b *policyBroker) Cancel(context.Context, string, string) error { return nil }

func TestObservationDoesNotRelabelHistoricalOwnershipAsCurrent(t *testing.T) {
	for _, mode := range []string{"matching", "external removal", "external addition", "sell filled before reconciliation", "pending order vanished"} {
		t.Run(mode, func(t *testing.T) {
			store, buy := submittedStore(t)
			ctx := context.Background()
			price := "100"
			filled := acceptedObservation()
			filled.State, filled.FilledQty, filled.FilledAveragePrice = "filled", "0.01", &price
			if err := store.observe(ctx, buy.DecisionID, filled, fixtureTime().Add(time.Second)); err != nil {
				t.Fatal(err)
			}
			broker := &policyBroker{journalBroker: newJournalBroker(), snapshot: freshSnapshot()}
			broker.orders[buy.ClientOrderID] = filled
			broker.snapshot.Positions = []BrokerPosition{{Symbol: "AAPL", Qty: "0.01", MarketValue: "1"}}
			var sell ExecutionRecord
			if mode == "sell filled before reconciliation" || mode == "pending order vanished" {
				i, _ := DecodeIntent(fixtureBytes(t))
				i.DecisionID = "12345678-1234-4234-8234-123456789abd"
				i.Action, i.Order.Side = "SELL", "sell"
				raw, _ := json.Marshal(i)
				var err error
				sell, _, err = store.reserve(ctx, raw, fixtureTime(), "10", "")
				if err != nil {
					t.Fatal(err)
				}
				if won, err := store.claim(ctx, sell.DecisionID, fixtureTime()); err != nil || !won {
					t.Fatal("sell claim failed")
				}
				o := acceptedObservation()
				o.OrderID, o.ClientOrderID, o.Side = "broker-sell", sell.ClientOrderID, "sell"
				if err := store.observe(ctx, sell.DecisionID, o, fixtureTime().Add(time.Second)); err != nil {
					t.Fatal(err)
				}
				if mode == "sell filled before reconciliation" {
					o.State, o.FilledQty, o.FilledAveragePrice = "filled", "0.01", &price
					o.UpdatedAt = "2026-09-23T14:00:03.000Z"
				}
				broker.orders[sell.ClientOrderID] = o
			}
			switch mode {
			case "external removal", "sell filled before reconciliation":
				broker.snapshot.Positions = []BrokerPosition{}
			case "external addition":
				broker.snapshot.Positions[0].Qty = "0.02"
			}
			service := policyService(t, store, broker, pilotPolicy())
			service.now = func() time.Time { return fixtureTime().Add(3 * time.Second) }
			// Refreshing the BUY cannot implicitly reconcile a different SELL.
			if _, err := service.Reconcile(ctx, buy.DecisionID); err != nil {
				t.Fatal(err)
			}
			o, err := service.Observe(ctx, "AAPL")
			if err != nil {
				t.Fatal(err)
			}
			if o.OwnedQuantity != "0.01" {
				t.Fatal("historical attribution was rewritten")
			}
			if o.UnresolvedExecution != (mode != "matching") {
				t.Fatalf("%s falsely asserts current ownership: %+v", mode, o)
			}
			if mode == "sell filled before reconciliation" {
				if _, err := service.Reconcile(ctx, sell.DecisionID); err != nil {
					t.Fatal(err)
				}
				o, err = service.Observe(ctx, "AAPL")
				if err != nil || o.UnresolvedExecution || o.OwnedQuantity != "0" {
					t.Fatalf("reconciled ownership unavailable: %+v %v", o, err)
				}
			}
		})
	}
}
func policyService(t *testing.T, s *Store, b *policyBroker, p SafetyPolicy) *Service {
	t.Helper()
	service, err := newPolicyService(s, b, p)
	if err != nil {
		t.Fatal(err)
	}
	service.now = func() time.Time { return fixtureTime().Add(time.Second) }
	return service
}
func referencedIntent(t *testing.T, service *Service, symbol string) (Intent, []byte) {
	t.Helper()
	o, err := service.Observe(context.Background(), symbol)
	if err != nil {
		t.Fatal(err)
	}
	i, err := DecodeIntent(fixtureBytes(t))
	if err != nil {
		t.Fatal(err)
	}
	i.Symbol = symbol
	i.ObservationID = o.ID
	raw, err := json.Marshal(i)
	if err != nil {
		t.Fatal(err)
	}
	return i, raw
}

func TestConcretePolicyServiceRequiresSavedReferenceAndFreshBrokerState(t *testing.T) {
	for _, mode := range []string{"valid", "missing reference", "account drift", "disabled", "kill switch"} {
		t.Run(mode, func(t *testing.T) {
			s := executionStore(t, filepath.Join(t.TempDir(), "policy.db"))
			b := &policyBroker{journalBroker: newJournalBroker(), snapshot: freshSnapshot()}
			p := pilotPolicy()
			if mode == "disabled" {
				p.Enabled = false
			}
			if mode == "kill switch" {
				p.KillSwitch = true
			}
			service := policyService(t, s, b, p)
			i, raw := referencedIntent(t, service, "AAPL")
			if mode == "missing reference" {
				i.ObservationID = "unknown"
				raw, _ = json.Marshal(i)
			}
			if mode == "account drift" {
				b.snapshot.Positions = []BrokerPosition{{Symbol: "AAPL", Qty: "1", MarketValue: "100"}}
			}
			r, err := service.Submit(context.Background(), i, raw)
			if err != nil {
				t.Fatal(err)
			}
			if mode == "valid" {
				if b.submits != 1 || r.State != "accepted" {
					t.Fatalf("valid path %s submits=%d", r.State, b.submits)
				}
				var safety SafetyDecision
				if err = json.Unmarshal(r.Safety, &safety); err != nil {
					t.Fatal(err)
				}
				for key, want := range map[string]string{"orderNotional": "1", "buyingPower": "100", "maxOrderNotional": "10", "projectedPortfolioNotional": "1", "dailyCommittedNotional": "1"} {
					if safety.Measurements[key] != want {
						t.Fatalf("missing/wrong measured %s: %+v", key, safety.Measurements)
					}
				}
			} else if b.submits != 0 || r.State != "rejected" {
				t.Fatalf("unsafe path %s submits=%d", r.State, b.submits)
			}
		})
	}
}

func TestConcretePolicyPortfolioReservationIsAtomicAcrossConnections(t *testing.T) {
	path := filepath.Join(t.TempDir(), "policy.db")
	a, b := executionStore(t, path), executionStore(t, path)
	broker := &policyBroker{journalBroker: newJournalBroker(), snapshot: freshSnapshot()}
	p := pilotPolicy()
	p.MaxPortfolio = "2"
	services := []*Service{policyService(t, a, broker, p), policyService(t, b, broker, p)}
	intents := make([]Intent, 20)
	bodies := make([][]byte, 20)
	for n := 0; n < 20; n++ {
		i, _ := referencedIntent(t, services[0], fmt.Sprintf("S%c", 'A'+n))
		i.DecisionID = fmt.Sprintf("12345678-1234-4234-8234-%012d", n)
		intents[n] = i
		bodies[n], _ = json.Marshal(i)
	}
	var wg sync.WaitGroup
	for n := 0; n < 20; n++ {
		wg.Add(1)
		go func(n int) {
			defer wg.Done()
			r, err := services[n%2].Submit(context.Background(), intents[n], bodies[n])
			if err != nil {
				t.Error(err)
				return
			}
			if r.State != "accepted" && r.State != "rejected" {
				t.Errorf("unexpected %s", r.State)
			}
		}(n)
	}
	wg.Wait()
	if broker.submits > 2 || broker.submits < 1 {
		t.Fatalf("portfolio limit produced %d broker submissions", broker.submits)
	}
}

func TestConcretePolicyBoundsNetworkByObservationExpiry(t *testing.T) {
	s := executionStore(t, filepath.Join(t.TempDir(), "deadline.db"))
	b := &policyBroker{journalBroker: newJournalBroker(), snapshot: freshSnapshot()}
	p := pilotPolicy()
	p.MaxAge = 5 * time.Second
	service := policyService(t, s, b, p)
	i, raw := referencedIntent(t, service, "AAPL")
	r, err := service.Submit(context.Background(), i, raw)
	if err != nil {
		t.Fatal(err)
	}
	if r.State != "accepted" || b.deadlineRemaining <= 0 || b.deadlineRemaining > 2*time.Second {
		t.Fatalf("network not bounded by quote freshness: %v", b.deadlineRemaining)
	}
}

func TestObservationSeparatesBrokerHoldingsFromLearnerOwnership(t *testing.T) {
	s := executionStore(t, filepath.Join(t.TempDir(), "ownership.db"))
	b := &policyBroker{journalBroker: newJournalBroker(), snapshot: freshSnapshot()}
	b.snapshot.Positions = []BrokerPosition{{Symbol: "AAPL", Qty: "4", MarketValue: "400"}}
	service := policyService(t, s, b, pilotPolicy())
	o, err := service.Observe(context.Background(), "AAPL")
	if err != nil {
		t.Fatal(err)
	}
	if o.OwnedQuantity != "0" || o.PendingBuyNotional != "0" || o.PendingSellQuantity != "0" || !o.UnresolvedExecution || o.Positions[0].Qty != "4" {
		t.Fatalf("external shares labeled as learner-owned %+v", o)
	}
}
