package learnerexecution

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"path/filepath"
	"testing"
	"time"
)

func TestObservationReferencesAreDurableImmutableAndOwnerScoped(t *testing.T) {
	path := filepath.Join(t.TempDir(), "observations.db")
	s := executionStore(t, path)
	ctx := context.Background()
	o := freshSnapshot()
	o.ID = "caller-supplied-id"
	saved, err := s.saveObservation(ctx, o)
	if err != nil {
		t.Fatal(err)
	}
	if saved.ID == o.ID || saved.ID == "" {
		t.Fatal("caller selected observation identity")
	}
	o.QuotePrice = "999"
	got, err := s.observation(ctx, saved.ID)
	if err != nil || got.QuotePrice != "100" {
		t.Fatalf("snapshot changed %+v %v", got, err)
	}
	s.db.Close()
	s = executionStore(t, path)
	got, err = s.observation(ctx, saved.ID)
	if err != nil || got.ID != saved.ID || got.QuotePrice != "100" {
		t.Fatalf("snapshot not durable %+v %v", got, err)
	}
	other, err := NewStore(s.db, "2", "paper-account-1")
	if err != nil {
		t.Fatal(err)
	}
	if _, err = other.observation(ctx, saved.ID); !errors.Is(err, ErrNotFound) {
		t.Fatalf("cross-owner snapshot read %v", err)
	}
	o.AccountID = "other"
	if _, err = s.saveObservation(ctx, o); !errors.Is(err, ErrScope) {
		t.Fatalf("cross-account snapshot %v", err)
	}
}

func readExposure(t *testing.T, s *Store, exclude string) Exposure {
	t.Helper()
	var e Exposure
	err := s.immediate(context.Background(), func(c *sql.Conn) error {
		var err error
		e, err = s.exposure(context.Background(), c, exclude)
		return err
	})
	if err != nil {
		t.Fatal(err)
	}
	return e
}

func TestExposureUsesConfirmedFillsAndRemainingReservations(t *testing.T) {
	s, r := submittedStore(t)
	ctx := context.Background()
	o := acceptedObservation()
	o.State = "partially_filled"
	o.FilledQty = "0.004"
	price := "99"
	o.FilledAveragePrice = &price
	if err := s.observe(ctx, r.DecisionID, o, fixtureTime().Add(time.Second)); err != nil {
		t.Fatal(err)
	}
	e := readExposure(t, s, "")
	if e.Owned["AAPL"] != "0.004" || e.PendingBuy["AAPL"] != "0.6" || e.TotalPendingBuy != "0.6" || e.KnownOrders[r.ClientOrderID].Record.BrokerOrderID == "" || e.Uncertain {
		t.Fatalf("bad attribution %+v", e)
	}
	o.State = "canceled"
	o.UpdatedAt = "2026-09-23T14:00:03.000Z"
	if err := s.observe(ctx, r.DecisionID, o, fixtureTime().Add(2*time.Second)); err != nil {
		t.Fatal(err)
	}
	e = readExposure(t, s, "")
	if e.Owned["AAPL"] != "0.004" || e.TotalPendingBuy != "0" {
		t.Fatalf("cancel erased fills or retained remainder %+v", e)
	}
	i, _ := DecodeIntent(fixtureBytes(t))
	i.DecisionID = "12345678-1234-4234-8234-123456789abd"
	i.Action = "SELL"
	i.Order.Side = "sell"
	i.Order.Qty = "0.002"
	raw, _ := json.Marshal(i)
	r, _, err := s.reserve(ctx, raw, fixtureTime(), "10", "")
	if err != nil {
		t.Fatal(err)
	}
	e = readExposure(t, s, "")
	if e.Owned["AAPL"] != "0.004" || e.PendingSell["AAPL"] != "0.002" {
		t.Fatalf("unfilled sell changed ownership %+v", e)
	}
	if won, err := s.claim(ctx, r.DecisionID, fixtureTime()); err != nil || !won {
		t.Fatal("sell claim failed")
	}
	o = acceptedObservation()
	o.OrderID = "broker-order-2"
	o.ClientOrderID = r.ClientOrderID
	o.Side = "sell"
	o.Qty = "0.002"
	o.FilledQty = "0.002"
	o.State = "filled"
	o.FilledAveragePrice = &price
	if err = s.observe(ctx, r.DecisionID, o, fixtureTime().Add(time.Second)); err != nil {
		t.Fatal(err)
	}
	e = readExposure(t, s, "")
	if e.Owned["AAPL"] != "0.002" || e.PendingSell["AAPL"] != "" {
		t.Fatalf("filled sell not attributed %+v", e)
	}
}

func TestExposureFlagsUnknownSubmissionAndExcludesOnlyOwnReservation(t *testing.T) {
	s, r := submittedStore(t)
	e := readExposure(t, s, "")
	if !e.Uncertain || e.TotalPendingBuy != "1" || e.Owned["AAPL"] != "0" {
		t.Fatalf("unknown execution hidden %+v", e)
	}
	e = readExposure(t, s, r.DecisionID)
	if e.Uncertain || e.TotalPendingBuy != "0" {
		t.Fatalf("own pending reservation counted twice %+v", e)
	}
}
