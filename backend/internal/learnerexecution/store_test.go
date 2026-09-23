package learnerexecution

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"path/filepath"
	"sync"
	"sync/atomic"
	"testing"
	"time"
)

func executionStore(t *testing.T, path string) *Store {
	t.Helper()
	s, err := NewStore(testNonceDB(t, path), "1", "paper-account-1")
	if err != nil {
		t.Fatal(err)
	}
	return s
}

func TestRejectedResumedReservationCannotKeepAllowedSafetyEvidence(t *testing.T) {
	s := executionStore(t, filepath.Join(t.TempDir(), "execution.db"))
	ctx := context.Background()
	r, _, err := s.reserve(ctx, fixtureBytes(t), fixtureTime(), "10", "", SafetyDecision{Allowed: true, PolicyVersion: "paper-v1", DailyLimit: "10"})
	if err != nil {
		t.Fatal(err)
	}
	if err = s.rejectUnsent(ctx, r.DecisionID, "kill_switch", fixtureTime()); err != nil {
		t.Fatal(err)
	}
	r, err = s.Get(ctx, r.DecisionID)
	if err != nil {
		t.Fatal(err)
	}
	var safety SafetyDecision
	if err = json.Unmarshal(r.Safety, &safety); err != nil {
		t.Fatal(err)
	}
	if safety.Allowed || safety.ReasonCode != "kill_switch" || r.ReservedNotional != "0" {
		t.Fatalf("rejected reservation kept contradictory evidence: %+v", safety)
	}
}

func TestDurableClaimHasOneWinnerAcrossConnectionsAndRestart(t *testing.T) {
	// Removing the SQL uniqueness or conditional submission claim must fail this test.
	path := filepath.Join(t.TempDir(), "execution.db")
	a, b := executionStore(t, path), executionStore(t, path)
	raw := fixtureBytes(t)
	ctx := context.Background()
	var created, claimed atomic.Int32
	var wg sync.WaitGroup
	for n := 0; n < 20; n++ {
		wg.Add(1)
		go func(n int) {
			defer wg.Done()
			s := []*Store{a, b}[n%2]
			r, fresh, err := s.reserve(ctx, raw, fixtureTime(), "10", "")
			if err != nil {
				t.Error(err)
				return
			}
			if fresh {
				created.Add(1)
			}
			won, err := s.claim(ctx, r.DecisionID, fixtureTime())
			if err != nil {
				t.Error(err)
				return
			}
			if won {
				claimed.Add(1)
			}
		}(n)
	}
	wg.Wait()
	if created.Load() != 1 || claimed.Load() != 1 {
		t.Fatalf("created=%d claimed=%d", created.Load(), claimed.Load())
	}
	if err := a.db.Close(); err != nil {
		t.Fatal(err)
	}
	if err := b.db.Close(); err != nil {
		t.Fatal(err)
	}
	reopened := executionStore(t, path)
	r, fresh, err := reopened.reserve(ctx, raw, fixtureTime().Add(24*time.Hour), "10", "")
	if err != nil || fresh || r.State != "submitting" || r.ReservedNotional != "1" {
		t.Fatalf("retry: %+v fresh=%v err=%v", r, fresh, err)
	}
	// Independently computed with Python hashlib over account + NUL + decision ID.
	if r.ClientOrderID != "kl-3b93c90d59d0ae76a7d2cdaaee70cd0837122290" {
		t.Fatalf("unstable broker retry identity %q", r.ClientOrderID)
	}
	if won, err := reopened.claim(ctx, r.DecisionID, fixtureTime()); err != nil || won {
		t.Fatalf("reclaimed after restart: %v %v", won, err)
	}
	events, err := reopened.Events(ctx, r.DecisionID, 0, 100)
	if err != nil || len(events) != 3 {
		t.Fatalf("events: %+v %v", events, err)
	}
	for n, want := range []string{"validated", "reserved", "submitting"} {
		if events[n].State != want || (n > 0 && events[n].ID <= events[n-1].ID) {
			t.Fatalf("invalid event sequence: %+v", events)
		}
	}
}

func TestChangedBytesConflictAndScopeCannotRead(t *testing.T) {
	s := executionStore(t, filepath.Join(t.TempDir(), "execution.db"))
	ctx := context.Background()
	raw := fixtureBytes(t)
	r, _, err := s.reserve(ctx, raw, fixtureTime(), "10", "")
	if err != nil {
		t.Fatal(err)
	}
	if _, _, err = s.reserve(ctx, append(raw, ' '), fixtureTime(), "10", ""); !errors.Is(err, ErrConflict) {
		t.Fatalf("body rewrite accepted: %v", err)
	}
	other, err := NewStore(s.db, "2", "paper-account-1")
	if err != nil {
		t.Fatal(err)
	}
	if _, err = other.Get(ctx, r.DecisionID); !errors.Is(err, ErrNotFound) {
		t.Fatalf("cross-owner read: %v", err)
	}
	if _, _, err = other.reserve(ctx, raw, fixtureTime(), "10", ""); !errors.Is(err, ErrScope) {
		t.Fatalf("cross-owner write: %v", err)
	}
}

func TestConcurrentDistinctReservationsCannotExceedCap(t *testing.T) {
	path := filepath.Join(t.TempDir(), "execution.db")
	a, b := executionStore(t, path), executionStore(t, path)
	base, err := DecodeIntent(fixtureBytes(t))
	if err != nil {
		t.Fatal(err)
	}
	var reserved, rejected atomic.Int32
	var wg sync.WaitGroup
	for n := 0; n < 20; n++ {
		wg.Add(1)
		go func(n int) {
			defer wg.Done()
			i := base
			i.DecisionID = fmt.Sprintf("12345678-1234-4234-8234-%012d", n)
			raw, _ := json.Marshal(i)
			r, _, err := []*Store{a, b}[n%2].reserve(context.Background(), raw, fixtureTime(), "3", "")
			if err != nil {
				t.Error(err)
				return
			}
			switch r.State {
			case "reserved":
				reserved.Add(1)
			case "rejected":
				if r.ReasonCode != "daily_cap_exceeded" {
					t.Errorf("reason %s", r.ReasonCode)
				}
				rejected.Add(1)
			default:
				t.Errorf("unexpected state %s", r.State)
			}
		}(n)
	}
	wg.Wait()
	if reserved.Load() != 3 || rejected.Load() != 17 {
		t.Fatalf("reserved=%d rejected=%d", reserved.Load(), rejected.Load())
	}
}

func TestHoldExpiryAndMissingCapNeverClaimSubmission(t *testing.T) {
	for _, tc := range []struct {
		name, action, cap, denial, want string
		now                             time.Time
	}{
		{"hold", "HOLD", "", "", "hold_recorded", fixtureTime()},
		{"expired", "BUY", "10", "", "rejected", fixtureTime().Add(time.Hour)},
		{"missing limits", "BUY", "", "", "rejected", fixtureTime()},
		{"mechanical denial", "BUY", "10", "kill_switch", "rejected", fixtureTime()},
	} {
		t.Run(tc.name, func(t *testing.T) {
			s := executionStore(t, filepath.Join(t.TempDir(), "execution.db"))
			i, err := DecodeIntent(fixtureBytes(t))
			if err != nil {
				t.Fatal(err)
			}
			i.Action = tc.action
			if tc.action == "HOLD" {
				i.Order = nil
			}
			raw, _ := json.Marshal(i)
			r, _, err := s.reserve(context.Background(), raw, tc.now, tc.cap, tc.denial)
			if err != nil || r.State != tc.want || r.ReservedNotional != "0" {
				t.Fatalf("%+v %v", r, err)
			}
			if won, err := s.claim(context.Background(), r.DecisionID, tc.now); err != nil || won {
				t.Fatalf("claimed non-order %v %v", won, err)
			}
		})
	}
}

func TestUncertaintyRetainsReservationAndNeverReclaims(t *testing.T) {
	s := executionStore(t, filepath.Join(t.TempDir(), "execution.db"))
	ctx := context.Background()
	r, _, err := s.reserve(ctx, fixtureBytes(t), fixtureTime(), "1", "")
	if err != nil {
		t.Fatal(err)
	}
	if won, err := s.claim(ctx, r.DecisionID, fixtureTime()); err != nil || !won {
		t.Fatalf("claim %v %v", won, err)
	}
	if err = s.uncertain(ctx, r.DecisionID, fixtureTime()); err != nil {
		t.Fatal(err)
	}
	if err = s.uncertain(ctx, r.DecisionID, fixtureTime()); err != nil {
		t.Fatal(err)
	}
	r, err = s.Get(ctx, r.DecisionID)
	if err != nil || r.State != "reconciliation_required" || r.ReservedNotional != "1" {
		t.Fatalf("%+v %v", r, err)
	}
	if won, err := s.claim(ctx, r.DecisionID, fixtureTime()); err != nil || won {
		t.Fatalf("reclaim %v %v", won, err)
	}
	events, err := s.Events(ctx, r.DecisionID, 0, 100)
	if err != nil || len(events) != 4 {
		t.Fatalf("non-idempotent uncertainty: %+v %v", events, err)
	}
}

func TestClosedDatabaseCannotReserveOrClaim(t *testing.T) {
	s := executionStore(t, filepath.Join(t.TempDir(), "execution.db"))
	s.db.Close()
	if _, _, err := s.reserve(context.Background(), fixtureBytes(t), fixtureTime(), "10", ""); err == nil {
		t.Fatal("closed database reserved")
	}
	if won, err := s.claim(context.Background(), testNonce, fixtureTime()); err == nil || won {
		t.Fatal("closed database claimed")
	}
}

func acceptedObservation() BrokerOrder {
	return BrokerOrder{OrderID: "broker-order-1", AccountID: "paper-account-1", ClientOrderID: clientOrderID("paper-account-1", testNonce), Symbol: "AAPL", Side: "buy", Qty: "0.01", LimitPrice: "100.00", Type: "limit", TimeInForce: "day", State: "accepted", FilledQty: "0", UpdatedAt: "2026-09-23T14:00:02.000Z"}
}

func submittedStore(t *testing.T) (*Store, ExecutionRecord) {
	t.Helper()
	s := executionStore(t, filepath.Join(t.TempDir(), "execution.db"))
	r, _, err := s.reserve(context.Background(), fixtureBytes(t), fixtureTime(), "10", "")
	if err != nil {
		t.Fatal(err)
	}
	if won, err := s.claim(context.Background(), r.DecisionID, fixtureTime()); err != nil || !won {
		t.Fatalf("claim %v %v", won, err)
	}
	return s, r
}

func TestBrokerPartialFillReplayAndCancelPreserveExactAttribution(t *testing.T) {
	s, r := submittedStore(t)
	ctx := context.Background()
	o := acceptedObservation()
	if err := s.observe(ctx, r.DecisionID, o, fixtureTime().Add(time.Second)); err != nil {
		t.Fatal(err)
	}
	o.State = "partially_filled"
	o.FilledQty = "0.004"
	price := "99.125"
	o.FilledAveragePrice = &price
	o.UpdatedAt = "2026-09-23T14:00:03.000Z"
	for n := 0; n < 2; n++ {
		if err := s.observe(ctx, r.DecisionID, o, fixtureTime().Add(2*time.Second)); err != nil {
			t.Fatal(err)
		}
	}
	r, err := s.Get(ctx, r.DecisionID)
	if err != nil || r.FilledQty != "0.004" || r.FillNotional == nil || *r.FillNotional != "0.3965" || r.ReservedNotional != "1" {
		t.Fatalf("partial: %+v %v", r, err)
	}
	o.State = "canceled"
	o.UpdatedAt = "2026-09-23T14:00:04.000Z"
	if err = s.observe(ctx, r.DecisionID, o, fixtureTime().Add(3*time.Second)); err != nil {
		t.Fatal(err)
	}
	r, err = s.Get(ctx, r.DecisionID)
	if err != nil || r.State != "canceled" || r.ReservedNotional != "0.4" || r.FillNotional == nil || *r.FillNotional != "0.3965" {
		t.Fatalf("cancel: %+v %v", r, err)
	}
	events, err := s.Events(ctx, r.DecisionID, 0, 100)
	if err != nil || len(events) != 6 {
		t.Fatalf("duplicate fill created an event: %+v %v", events, err)
	}
}

func TestBrokerUnknownOrMismatchedEvidenceRetainsReservation(t *testing.T) {
	for _, mutate := range []func(*BrokerOrder){
		func(o *BrokerOrder) { o.AccountID = "other" }, func(o *BrokerOrder) { o.ClientOrderID = "other" },
		func(o *BrokerOrder) { o.Symbol = "MSFT" }, func(o *BrokerOrder) { o.Side = "sell" },
		func(o *BrokerOrder) { o.Qty = "0.02" }, func(o *BrokerOrder) { o.LimitPrice = "101" },
		func(o *BrokerOrder) { o.Type = "market" }, func(o *BrokerOrder) { o.TimeInForce = "gtc" },
		func(o *BrokerOrder) { o.State = "mystery" }, func(o *BrokerOrder) { o.OrderID = "" },
		func(o *BrokerOrder) { o.State = "filled"; o.FilledQty = "0.01" },
		func(o *BrokerOrder) { o.FilledQty = "0.011" },
	} {
		s, r := submittedStore(t)
		o := acceptedObservation()
		mutate(&o)
		if err := s.observe(context.Background(), r.DecisionID, o, fixtureTime().Add(time.Second)); err != nil {
			t.Fatal(err)
		}
		r, err := s.Get(context.Background(), r.DecisionID)
		if err != nil || r.State != "reconciliation_required" || r.ReservedNotional != "1" || r.FilledQty != "0" || r.FillNotional != nil {
			t.Fatalf("unsafe attribution %+v %v", r, err)
		}
	}
}

func TestFilledOrderIgnoresOlderObservationButFlagsNewContradiction(t *testing.T) {
	s, r := submittedStore(t)
	ctx := context.Background()
	o := acceptedObservation()
	o.State = "filled"
	o.FilledQty = "0.01"
	price := "99"
	o.FilledAveragePrice = &price
	o.UpdatedAt = "2026-09-23T14:00:04.000Z"
	if err := s.observe(ctx, r.DecisionID, o, fixtureTime().Add(3*time.Second)); err != nil {
		t.Fatal(err)
	}
	stale := acceptedObservation()
	if err := s.observe(ctx, r.DecisionID, stale, fixtureTime().Add(4*time.Second)); err != nil {
		t.Fatal(err)
	}
	r, err := s.Get(ctx, r.DecisionID)
	if err != nil || r.State != "filled" || r.FillNotional == nil || *r.FillNotional != "0.99" {
		t.Fatalf("stale erased fill %+v %v", r, err)
	}
	stale.UpdatedAt = "2026-09-23T14:00:05.000Z"
	if err = s.observe(ctx, r.DecisionID, stale, fixtureTime().Add(5*time.Second)); err != nil {
		t.Fatal(err)
	}
	r, err = s.Get(ctx, r.DecisionID)
	if err != nil || r.State != "reconciliation_required" || r.FilledQty != "0.01" || r.FillNotional == nil || *r.FillNotional != "0.99" {
		t.Fatalf("new contradiction lost fill %+v %v", r, err)
	}
}

func TestExactHighPrecisionFillSurvivesLaterObservation(t *testing.T) {
	s, r := submittedStore(t)
	ctx := context.Background()
	o := acceptedObservation()
	o.State = "partially_filled"
	o.FilledQty = "0.000000001"
	price := "99.123456789123"
	o.FilledAveragePrice = &price
	if err := s.observe(ctx, r.DecisionID, o, fixtureTime().Add(time.Second)); err != nil {
		t.Fatal(err)
	}
	o.State = "canceled"
	o.UpdatedAt = "2026-09-23T14:00:03.000Z"
	if err := s.observe(ctx, r.DecisionID, o, fixtureTime().Add(2*time.Second)); err != nil {
		t.Fatal(err)
	}
	r, err := s.Get(ctx, r.DecisionID)
	if err != nil || r.State != "canceled" || r.FillNotional == nil || *r.FillNotional != "0.000000099123456789123" || r.ReservedNotional != "0.0000001" {
		t.Fatalf("precision lost: %+v %v", r, err)
	}
}

func TestEventWriteFailureRollsBackSubmissionClaim(t *testing.T) {
	s := executionStore(t, filepath.Join(t.TempDir(), "execution.db"))
	ctx := context.Background()
	r, _, err := s.reserve(ctx, fixtureBytes(t), fixtureTime(), "10", "")
	if err != nil {
		t.Fatal(err)
	}
	_, err = s.db.Exec(`CREATE TRIGGER fail_claim BEFORE INSERT ON learner_execution_events WHEN NEW.state='submitting' BEGIN SELECT RAISE(ABORT,'injected storage failure'); END`)
	if err != nil {
		t.Fatal(err)
	}
	if won, err := s.claim(ctx, r.DecisionID, fixtureTime()); err == nil || won {
		t.Fatalf("claim escaped failed transaction %v %v", won, err)
	}
	r, err = s.Get(ctx, r.DecisionID)
	if err != nil || r.State != "reserved" {
		t.Fatalf("partial transaction %+v %v", r, err)
	}
}

func TestStorageFailureAfterBrokerEvidenceLeavesSubmittingRecoverable(t *testing.T) {
	s, r := submittedStore(t)
	ctx := context.Background()
	_, err := s.db.Exec(`CREATE TRIGGER fail_ack BEFORE INSERT ON learner_execution_events WHEN NEW.state='accepted' BEGIN SELECT RAISE(ABORT,'injected storage failure'); END`)
	if err != nil {
		t.Fatal(err)
	}
	if err = s.observe(ctx, r.DecisionID, acceptedObservation(), fixtureTime().Add(time.Second)); err == nil {
		t.Fatal("ack should fail")
	}
	r, err = s.Get(ctx, r.DecisionID)
	if err != nil || r.State != "submitting" || r.BrokerOrderID != "" {
		t.Fatalf("partial broker acknowledgement %+v %v", r, err)
	}
	if won, err := s.claim(ctx, r.DecisionID, fixtureTime()); err != nil || won {
		t.Fatal("unacknowledged order reclaimed")
	}
	if _, err = s.db.Exec(`DROP TRIGGER fail_ack`); err != nil {
		t.Fatal(err)
	}
	if err = s.observe(ctx, r.DecisionID, acceptedObservation(), fixtureTime().Add(time.Second)); err != nil {
		t.Fatal(err)
	}
	r, err = s.Get(ctx, r.DecisionID)
	if err != nil || r.State != "accepted" || r.BrokerOrderID != "broker-order-1" {
		t.Fatalf("lookup recovery failed %+v %v", r, err)
	}
}

func TestFilledAndUncertainOrdersContinueConsumingDailyBudget(t *testing.T) {
	for _, state := range []string{"filled", "uncertain"} {
		t.Run(state, func(t *testing.T) {
			s, r := submittedStore(t)
			ctx := context.Background()
			if state == "filled" {
				o := acceptedObservation()
				o.State = "filled"
				o.FilledQty = "0.01"
				price := "99"
				o.FilledAveragePrice = &price
				if err := s.observe(ctx, r.DecisionID, o, fixtureTime().Add(time.Second)); err != nil {
					t.Fatal(err)
				}
			} else {
				if err := s.uncertain(ctx, r.DecisionID, fixtureTime()); err != nil {
					t.Fatal(err)
				}
			}
			i, err := DecodeIntent(fixtureBytes(t))
			if err != nil {
				t.Fatal(err)
			}
			i.DecisionID = "12345678-1234-4234-8234-123456789abd"
			raw, _ := json.Marshal(i)
			r, _, err = s.reserve(ctx, raw, fixtureTime(), "1", "")
			if err != nil || r.State != "rejected" || r.ReasonCode != "daily_cap_exceeded" {
				t.Fatalf("consumed budget forgotten %+v %v", r, err)
			}
		})
	}
}

func TestExpiredReservationCannotAcquireSubmissionClaim(t *testing.T) {
	s := executionStore(t, filepath.Join(t.TempDir(), "execution.db"))
	ctx := context.Background()
	r, _, err := s.reserve(ctx, fixtureBytes(t), fixtureTime(), "10", "")
	if err != nil {
		t.Fatal(err)
	}
	if won, err := s.claim(ctx, r.DecisionID, fixtureTime().Add(time.Hour)); err != nil || won {
		t.Fatalf("expired reservation acquired claim %v %v", won, err)
	}
	r, err = s.Get(ctx, r.DecisionID)
	if err != nil || r.State != "rejected" || r.ReasonCode != "intent_expired_or_future" || r.ReservedNotional != "0" {
		t.Fatalf("expiry not journaled %+v %v", r, err)
	}
}

func TestContradictionAfterCancelRestoresFullReservation(t *testing.T) {
	s, r := submittedStore(t)
	ctx := context.Background()
	o := acceptedObservation()
	o.State = "canceled"
	if err := s.observe(ctx, r.DecisionID, o, fixtureTime().Add(time.Second)); err != nil {
		t.Fatal(err)
	}
	r, err := s.Get(ctx, r.DecisionID)
	if err != nil || r.ReservedNotional != "0" {
		t.Fatalf("cancel %+v %v", r, err)
	}
	o.State = "accepted"
	o.UpdatedAt = "2026-09-23T14:00:03.000Z"
	if err = s.observe(ctx, r.DecisionID, o, fixtureTime().Add(2*time.Second)); err != nil {
		t.Fatal(err)
	}
	r, err = s.Get(ctx, r.DecisionID)
	if err != nil || r.State != "reconciliation_required" || r.ReservedNotional != "1" {
		t.Fatalf("uncertainty released exposure %+v %v", r, err)
	}
}

func TestBrokerOrderCannotBeAttributedToTwoIntents(t *testing.T) {
	s, r := submittedStore(t)
	ctx := context.Background()
	o := acceptedObservation()
	if err := s.observe(ctx, r.DecisionID, o, fixtureTime().Add(time.Second)); err != nil {
		t.Fatal(err)
	}
	i, _ := DecodeIntent(fixtureBytes(t))
	i.DecisionID = "12345678-1234-4234-8234-123456789abd"
	raw, _ := json.Marshal(i)
	r, _, err := s.reserve(ctx, raw, fixtureTime(), "10", "")
	if err != nil {
		t.Fatal(err)
	}
	if won, err := s.claim(ctx, r.DecisionID, fixtureTime()); err != nil || !won {
		t.Fatal("claim failed")
	}
	o.ClientOrderID = r.ClientOrderID
	if err = s.observe(ctx, r.DecisionID, o, fixtureTime().Add(time.Second)); err == nil {
		t.Fatal("one broker order attributed twice")
	}
	r, err = s.Get(ctx, r.DecisionID)
	if err != nil || r.State != "submitting" || r.BrokerOrderID != "" {
		t.Fatalf("duplicate broker ID committed: %v %s", err, r.State)
	}
}
