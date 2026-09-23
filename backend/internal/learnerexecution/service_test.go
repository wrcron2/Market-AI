package learnerexecution

import (
	"bytes"
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"path/filepath"
	"sync"
	"testing"
	"time"
)

// Task 3 isolates the transaction/network boundary. No production startup can
// construct this test-only gate; the concrete mechanical policy follows in Task 4.
type journalTestGate struct{ decision SafetyDecision }

func (g journalTestGate) assess(context.Context, Intent) (SafetyDecision, error) {
	return g.decision, nil
}

// Only the external broker is simulated. SQLite, validation, claims, intent
// identity, reconciliation and all service control flow remain real.
type journalBroker struct {
	mu                   sync.Mutex
	submits              int
	lookups              int
	orders               map[string]BrokerOrder
	submitErr, lookupErr error
	absent               bool
	beforeReturn         func()
	seen                 Intent
	deadlineRemaining    time.Duration
}

func (b *journalBroker) Submit(ctx context.Context, i Intent, clientID string) (BrokerOrder, error) {
	b.mu.Lock()
	defer b.mu.Unlock()
	b.submits++
	if until, ok := ctx.Deadline(); ok {
		b.deadlineRemaining = time.Until(until)
	}
	b.seen = i
	o := acceptedObservation()
	o.OrderID = fmt.Sprintf("broker-order-%d", b.submits)
	o.ClientOrderID = clientID
	o.Symbol = i.Symbol
	o.Side = i.Order.Side
	o.Qty = i.Order.Qty
	o.LimitPrice = i.Order.LimitPrice
	b.orders[clientID] = o
	if b.beforeReturn != nil {
		b.beforeReturn()
	}
	return o, b.submitErr
}
func (b *journalBroker) LookupByClientID(_ context.Context, id string) (*BrokerOrder, error) {
	b.mu.Lock()
	defer b.mu.Unlock()
	b.lookups++
	if b.lookupErr != nil {
		return nil, b.lookupErr
	}
	if b.absent {
		return nil, nil
	}
	o, ok := b.orders[id]
	if !ok {
		return nil, nil
	}
	return &o, nil
}
func newJournalService(t *testing.T, store *Store, broker *journalBroker) *Service {
	t.Helper()
	s, err := newService(store, broker, journalTestGate{SafetyDecision{Allowed: true, PolicyVersion: "paper-v1", DailyLimit: "10"}})
	if err != nil {
		t.Fatal(err)
	}
	s.now = func() time.Time { return fixtureTime().Add(time.Second) }
	return s
}
func newJournalBroker() *journalBroker { return &journalBroker{orders: map[string]BrokerOrder{}} }

func TestServiceConcurrentRetryAndReopenSubmitExactlyOnce(t *testing.T) {
	path := filepath.Join(t.TempDir(), "service.db")
	a, b := executionStore(t, path), executionStore(t, path)
	broker := newJournalBroker()
	services := []*Service{newJournalService(t, a, broker), newJournalService(t, b, broker)}
	raw := fixtureBytes(t)
	intent, err := DecodeIntent(raw)
	if err != nil {
		t.Fatal(err)
	}
	var wg sync.WaitGroup
	for n := 0; n < 20; n++ {
		wg.Add(1)
		go func(n int) {
			defer wg.Done()
			r, err := services[n%2].Submit(context.Background(), intent, raw)
			if err != nil || r.DecisionID != intent.DecisionID {
				t.Errorf("submission err=%v state=%s", err, r.State)
			}
		}(n)
	}
	wg.Wait()
	if broker.submits != 1 {
		t.Fatalf("duplicate broker orders: %d", broker.submits)
	}
	if broker.seen.Action != "BUY" || broker.seen.Symbol != "AAPL" || broker.seen.Order.Qty != "0.01" || broker.seen.Order.LimitPrice != "100.00" {
		t.Fatalf("economics changed: %+v", broker.seen.Order)
	}
	a.db.Close()
	b.db.Close()
	reopened := newJournalService(t, executionStore(t, path), broker)
	reopened.now = func() time.Time { return fixtureTime().Add(time.Hour) }
	r, err := reopened.Submit(context.Background(), intent, raw)
	if err != nil || r.State != "accepted" || broker.submits != 1 || !bytes.Equal(r.RawBody, raw) {
		t.Fatalf("retry err=%v state=%s submits=%d", err, r.State, broker.submits)
	}
	if _, err = reopened.Submit(context.Background(), intent, append(raw, ' ')); !errors.Is(err, ErrConflict) {
		t.Fatalf("changed bytes: %v", err)
	}
}

func TestServiceTimeoutAfterAcceptanceResolvesByLookupAfterRestart(t *testing.T) {
	path := filepath.Join(t.TempDir(), "service.db")
	store := executionStore(t, path)
	broker := newJournalBroker()
	broker.submitErr = context.DeadlineExceeded
	service := newJournalService(t, store, broker)
	raw := fixtureBytes(t)
	intent, err := DecodeIntent(raw)
	if err != nil {
		t.Fatal(err)
	}
	r, err := service.Submit(context.Background(), intent, raw)
	if err != nil || r.State != "reconciliation_required" || broker.submits != 1 {
		t.Fatalf("timeout lost uncertainty: %v %s %d", err, r.State, broker.submits)
	}
	store.db.Close()
	service = newJournalService(t, executionStore(t, path), broker)
	if _, err = service.Submit(context.Background(), intent, raw); err != nil {
		t.Fatal(err)
	}
	r, err = service.Reconcile(context.Background(), intent.DecisionID)
	if err != nil || r.State != "accepted" || r.BrokerOrderID != "broker-order-1" || broker.submits != 1 || broker.lookups != 1 {
		t.Fatalf("recovery %v state=%s submits=%d lookups=%d", err, r.State, broker.submits, broker.lookups)
	}
}

func TestServiceAmbiguousAbsenceAndUnavailableLookupNeverResubmit(t *testing.T) {
	for _, absent := range []bool{false, true} {
		t.Run(map[bool]string{false: "unavailable", true: "absent"}[absent], func(t *testing.T) {
			store := executionStore(t, filepath.Join(t.TempDir(), "service.db"))
			broker := newJournalBroker()
			broker.submitErr = context.DeadlineExceeded
			service := newJournalService(t, store, broker)
			raw := fixtureBytes(t)
			intent, _ := DecodeIntent(raw)
			if _, err := service.Submit(context.Background(), intent, raw); err != nil {
				t.Fatal(err)
			}
			broker.absent = absent
			if !absent {
				broker.lookupErr = errors.New("provider unavailable")
			}
			for n := 0; n < 3; n++ {
				r, err := service.Reconcile(context.Background(), intent.DecisionID)
				if err != nil || r.State != "reconciliation_required" || r.ReservedNotional != "1" {
					t.Fatalf("lost uncertainty %v %s", err, r.State)
				}
				if _, err := service.Submit(context.Background(), intent, raw); err != nil {
					t.Fatal(err)
				}
			}
			if broker.submits != 1 {
				t.Fatalf("blind resubmission %d", broker.submits)
			}
		})
	}
}

func TestServiceStorageFailureBeforeNetworkMakesZeroCalls(t *testing.T) {
	store := executionStore(t, filepath.Join(t.TempDir(), "service.db"))
	broker := newJournalBroker()
	service := newJournalService(t, store, broker)
	store.db.Close()
	raw := fixtureBytes(t)
	intent, _ := DecodeIntent(raw)
	if _, err := service.Submit(context.Background(), intent, raw); err == nil {
		t.Fatal("closed store accepted")
	}
	if broker.submits != 0 || broker.lookups != 0 {
		t.Fatalf("network before durable storage %d %d", broker.submits, broker.lookups)
	}
}

func TestServiceStorageFailureAfterNetworkRecoversWithoutSecondOrder(t *testing.T) {
	path := filepath.Join(t.TempDir(), "service.db")
	store := executionStore(t, path)
	broker := newJournalBroker()
	service := newJournalService(t, store, broker)
	broker.beforeReturn = func() { store.db.Close() }
	raw := fixtureBytes(t)
	intent, _ := DecodeIntent(raw)
	if _, err := service.Submit(context.Background(), intent, raw); err == nil {
		t.Fatal("lost acknowledgement should report storage failure")
	}
	broker.beforeReturn = nil
	store = executionStore(t, path)
	service = newJournalService(t, store, broker)
	r, err := store.Get(context.Background(), intent.DecisionID)
	if err != nil || r.State != "submitting" {
		t.Fatalf("durable claim lost %v %s", err, r.State)
	}
	if _, err = service.Submit(context.Background(), intent, raw); err != nil {
		t.Fatal(err)
	}
	r, err = service.Reconcile(context.Background(), intent.DecisionID)
	if err != nil || r.State != "accepted" || broker.submits != 1 {
		t.Fatalf("recovery %v %s %d", err, r.State, broker.submits)
	}
}

func TestServiceRejectsSplitRepresentationAndScopeBeforeBroker(t *testing.T) {
	store := executionStore(t, filepath.Join(t.TempDir(), "service.db"))
	broker := newJournalBroker()
	service := newJournalService(t, store, broker)
	raw := fixtureBytes(t)
	intent, _ := DecodeIntent(raw)
	intent.Order.Side = "sell"
	if _, err := service.Submit(context.Background(), intent, raw); err == nil {
		t.Fatal("parsed/raw mismatch accepted")
	}
	intent, _ = DecodeIntent(raw)
	intent.OwnerID = "2"
	if _, err := service.Submit(context.Background(), intent, raw); err == nil {
		t.Fatal("wrong owner accepted")
	}
	if broker.submits != 0 || broker.lookups != 0 {
		t.Fatal("invalid intent reached broker")
	}
}

func TestServiceClaimCommittedBeforeBrokerAndNoWriteLockAcrossNetwork(t *testing.T) {
	path := filepath.Join(t.TempDir(), "service.db")
	store := executionStore(t, path)
	other := executionStore(t, path)
	broker := newJournalBroker()
	service := newJournalService(t, store, broker)
	broker.beforeReturn = func() {
		r, err := other.Get(context.Background(), testNonce)
		if err != nil || r.State != "submitting" {
			t.Errorf("network before commit: %v %s", err, r.State)
		}
		ctx, cancel := context.WithTimeout(context.Background(), time.Second)
		defer cancel()
		if err := other.immediate(ctx, func(c *sql.Conn) error { _, err := c.ExecContext(ctx, "SELECT 1"); return err }); err != nil {
			t.Errorf("write lock held across network: %v", err)
		}
	}
	raw := fixtureBytes(t)
	intent, _ := DecodeIntent(raw)
	if _, err := service.Submit(context.Background(), intent, raw); err != nil {
		t.Fatal(err)
	}
}

func TestServiceUnknownLookupPreservesKnownFillThenRecoversSameEvidence(t *testing.T) {
	store := executionStore(t, filepath.Join(t.TempDir(), "service.db"))
	broker := newJournalBroker()
	service := newJournalService(t, store, broker)
	raw := fixtureBytes(t)
	intent, _ := DecodeIntent(raw)
	r, err := service.Submit(context.Background(), intent, raw)
	if err != nil {
		t.Fatal(err)
	}
	broker.lookupErr = context.DeadlineExceeded
	r, err = service.Reconcile(context.Background(), intent.DecisionID)
	if err != nil || r.State != "reconciliation_required" || r.BrokerState != "accepted" || r.ReservedNotional != "1" {
		t.Fatalf("lookup uncertainty %v %s %s", err, r.State, r.BrokerState)
	}
	broker.lookupErr = nil
	r, err = service.Reconcile(context.Background(), intent.DecisionID)
	if err != nil || r.State != "accepted" || broker.submits != 1 {
		t.Fatalf("same evidence failed to recover %v %s", err, r.State)
	}
}

func TestServiceCanceledRequestStillRecordsBrokerTimeout(t *testing.T) {
	store := executionStore(t, filepath.Join(t.TempDir(), "service.db"))
	broker := newJournalBroker()
	service := newJournalService(t, store, broker)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	broker.beforeReturn = cancel
	broker.submitErr = context.Canceled
	raw := fixtureBytes(t)
	intent, _ := DecodeIntent(raw)
	r, err := service.Submit(ctx, intent, raw)
	if err != nil || r.State != "reconciliation_required" || broker.submits != 1 {
		t.Fatalf("request cancellation erased outcome: %v %s", err, r.State)
	}
}

func TestServiceHoldAndDeniedSafetyNeverCallBroker(t *testing.T) {
	for _, tc := range []struct {
		name, action, cap, reason, want string
		allowed                         bool
	}{
		{"hold", "HOLD", "10", "", "hold_recorded", true},
		{"denied", "BUY", "10", "kill_switch", "rejected", false},
		{"missing limits", "BUY", "", "", "rejected", true},
		{"exhausted cap", "BUY", "0.50", "", "rejected", true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			store := executionStore(t, filepath.Join(t.TempDir(), "service.db"))
			broker := newJournalBroker()
			service := newJournalService(t, store, broker)
			service.gate = journalTestGate{SafetyDecision{Allowed: tc.allowed, ReasonCode: tc.reason, PolicyVersion: "paper-v1", DailyLimit: tc.cap}}
			intent, _ := DecodeIntent(fixtureBytes(t))
			intent.Action = tc.action
			if tc.action == "HOLD" {
				intent.Order = nil
			}
			raw, _ := json.Marshal(intent)
			r, err := service.Submit(context.Background(), intent, raw)
			if err != nil || r.State != tc.want || broker.submits != 0 || broker.lookups != 0 {
				t.Fatalf("non-order mutated broker: %v %s", err, r.State)
			}
			var safety SafetyDecision
			if err = json.Unmarshal(r.Safety, &safety); err != nil {
				t.Fatal(err)
			}
			if tc.want == "rejected" && (safety.Allowed || safety.ReasonCode != r.ReasonCode) {
				t.Fatalf("safety evidence contradicts rejection: %+v reason=%s", safety, r.ReasonCode)
			}
		})
	}
}

func TestServiceDatabaseLockFailsBeforeBroker(t *testing.T) {
	path := filepath.Join(t.TempDir(), "service.db")
	store := executionStore(t, path)
	other := executionStore(t, path)
	store.db.SetMaxOpenConns(1)
	if _, err := store.db.Exec("PRAGMA busy_timeout=10"); err != nil {
		t.Fatal(err)
	}
	c, err := other.db.Conn(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	defer c.Close()
	if _, err = c.ExecContext(context.Background(), "BEGIN IMMEDIATE"); err != nil {
		t.Fatal(err)
	}
	defer c.ExecContext(context.Background(), "ROLLBACK")
	broker := newJournalBroker()
	service := newJournalService(t, store, broker)
	raw := fixtureBytes(t)
	intent, _ := DecodeIntent(raw)
	if _, err = service.Submit(context.Background(), intent, raw); err == nil {
		t.Fatal("locked database admitted submission")
	}
	if broker.submits != 0 || broker.lookups != 0 {
		t.Fatal("locked storage reached broker")
	}
}

func TestServiceConcurrentDistinctDecisionsRespectSharedBudget(t *testing.T) {
	path := filepath.Join(t.TempDir(), "service.db")
	a, b := executionStore(t, path), executionStore(t, path)
	broker := newJournalBroker()
	services := []*Service{newJournalService(t, a, broker), newJournalService(t, b, broker)}
	for _, service := range services {
		service.gate = journalTestGate{SafetyDecision{Allowed: true, PolicyVersion: "paper-v1", DailyLimit: "3"}}
	}
	var wg sync.WaitGroup
	for n := 0; n < 20; n++ {
		wg.Add(1)
		go func(n int) {
			defer wg.Done()
			intent, _ := DecodeIntent(fixtureBytes(t))
			intent.DecisionID = fmt.Sprintf("12345678-1234-4234-8234-%012d", n)
			raw, _ := json.Marshal(intent)
			r, err := services[n%2].Submit(context.Background(), intent, raw)
			if err != nil {
				t.Error(err)
				return
			}
			if r.State != "accepted" && (r.State != "rejected" || r.ReasonCode != "daily_cap_exceeded") {
				t.Errorf("unexpected state %s reason=%s", r.State, r.ReasonCode)
			}
		}(n)
	}
	wg.Wait()
	if broker.submits != 3 {
		t.Fatalf("shared budget produced %d orders, want 3", broker.submits)
	}
}

func TestServiceReservedRetryRechecksLoweredDailyCap(t *testing.T) {
	store := executionStore(t, filepath.Join(t.TempDir(), "limits.db"))
	raw := fixtureBytes(t)
	i, _ := DecodeIntent(raw)
	if _, _, err := store.reserve(context.Background(), raw, fixtureTime(), "10", ""); err != nil {
		t.Fatal(err)
	}
	b := newJournalBroker()
	service := newJournalService(t, store, b)
	service.gate = journalTestGate{SafetyDecision{Allowed: true, PolicyVersion: "paper-v1", DailyLimit: "0.5"}}
	r, err := service.Submit(context.Background(), i, raw)
	if err != nil || b.submits != 0 || r.State != "rejected" || r.ReasonCode != "daily_cap_exceeded" {
		t.Fatalf("resumed reservation bypassed changed cap: err=%v state=%s submits=%d", err, r.State, b.submits)
	}
}
