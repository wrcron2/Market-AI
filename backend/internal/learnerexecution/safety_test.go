package learnerexecution

import (
	"reflect"
	"testing"
	"time"
)

func pilotPolicy() SafetyPolicy {
	return SafetyPolicy{Enabled: true, AccountID: "paper-account-1", Version: "paper-v1", MaxOrder: "10", MaxPosition: "20", MaxPortfolio: "30", MaxDaily: "100", MaxAge: 30 * time.Second}
}
func freshSnapshot() BrokerSnapshot {
	return BrokerSnapshot{ID: "obs-1", AccountID: "paper-account-1", Symbol: "AAPL", Paper: true, Complete: true, AccountStatus: "ACTIVE", BuyingPower: "100", QuotePrice: "100", QuoteAt: time.Date(2026, 9, 23, 13, 59, 59, 0, time.UTC), RetrievedAt: fixtureTime().Add(-time.Second), ClockAt: fixtureTime().Add(-time.Second), SessionOpen: true, AssetClass: "us_equity", Tradable: true, Fractionable: true, Positions: []BrokerPosition{}, OpenOrders: []BrokerOrder{}}
}
func emptyExposure() Exposure {
	return Exposure{Owned: map[string]string{}, PendingBuy: map[string]string{}, PendingSell: map[string]string{}, KnownOrders: map[string]JournalOrder{}, TotalPendingBuy: "0"}
}

func TestMechanicalSafetyPreservesValidEconomics(t *testing.T) {
	i, _ := DecodeIntent(fixtureBytes(t))
	before, _ := DecodeIntent(fixtureBytes(t))
	o := freshSnapshot()
	p := pilotPolicy()
	d := p.check(i, o, o, emptyExposure(), fixtureTime())
	if !d.Allowed || d.ReasonCode != "" || d.PolicyVersion != "paper-v1" || d.DailyLimit != "100" {
		t.Fatalf("valid order denied %+v", d)
	}
	if !reflect.DeepEqual(i, before) {
		t.Fatal("mechanical check changed economic intent")
	}
}

func TestMechanicalSafetyDenials(t *testing.T) {
	for _, tc := range []struct {
		name, reason string
		change       func(*SafetyPolicy, *BrokerSnapshot, *BrokerSnapshot, *Exposure)
	}{
		{"disabled", "execution_disabled", func(p *SafetyPolicy, _, _ *BrokerSnapshot, _ *Exposure) { p.Enabled = false }},
		{"kill switch", "kill_switch", func(p *SafetyPolicy, _, _ *BrokerSnapshot, _ *Exposure) { p.KillSwitch = true }},
		{"limits", "limits_not_configured", func(p *SafetyPolicy, _, _ *BrokerSnapshot, _ *Exposure) { p.MaxPosition = "" }},
		{"policy", "policy_version_mismatch", func(p *SafetyPolicy, _, _ *BrokerSnapshot, _ *Exposure) { p.Version = "new" }},
		{"live", "paper_required", func(_ *SafetyPolicy, _, o *BrokerSnapshot, _ *Exposure) { o.Paper = false }},
		{"account", "account_mismatch", func(_ *SafetyPolicy, _, o *BrokerSnapshot, _ *Exposure) { o.AccountID = "other" }},
		{"incomplete", "observation_incomplete", func(_ *SafetyPolicy, _, o *BrokerSnapshot, _ *Exposure) { o.Complete = false }},
		{"inactive", "account_unavailable", func(_ *SafetyPolicy, _, o *BrokerSnapshot, _ *Exposure) { o.AccountStatus = "INACTIVE" }},
		{"blocked", "account_unavailable", func(_ *SafetyPolicy, _, o *BrokerSnapshot, _ *Exposure) { o.TradingBlocked = true }},
		{"missing power", "buying_power_unavailable", func(_ *SafetyPolicy, _, o *BrokerSnapshot, _ *Exposure) { o.BuyingPower = "" }},
		{"power", "insufficient_buying_power", func(_ *SafetyPolicy, _, o *BrokerSnapshot, _ *Exposure) { o.BuyingPower = "0.5" }},
		{"stale quote", "market_data_stale", func(_ *SafetyPolicy, _, o *BrokerSnapshot, _ *Exposure) { o.QuoteAt = o.QuoteAt.Add(-time.Minute) }},
		{"future quote", "market_data_stale", func(_ *SafetyPolicy, _, o *BrokerSnapshot, _ *Exposure) { o.QuoteAt = o.QuoteAt.Add(time.Minute) }},
		{"stale fetch", "observation_stale", func(_ *SafetyPolicy, _, o *BrokerSnapshot, _ *Exposure) {
			o.RetrievedAt = o.RetrievedAt.Add(-time.Minute)
		}},
		{"stale clock", "session_unavailable", func(_ *SafetyPolicy, _, o *BrokerSnapshot, _ *Exposure) { o.ClockAt = o.ClockAt.Add(-time.Minute) }},
		{"closed", "session_unavailable", func(_ *SafetyPolicy, _, o *BrokerSnapshot, _ *Exposure) { o.SessionOpen = false }},
		{"unknown asset", "unsupported_asset", func(_ *SafetyPolicy, _, o *BrokerSnapshot, _ *Exposure) { o.AssetClass = "crypto" }},
		{"not tradable", "unsupported_asset", func(_ *SafetyPolicy, _, o *BrokerSnapshot, _ *Exposure) { o.Tradable = false }},
		{"fractional", "fractional_unsupported", func(_ *SafetyPolicy, _, o *BrokerSnapshot, _ *Exposure) { o.Fractionable = false }},
		{"quote missing", "market_data_unavailable", func(_ *SafetyPolicy, _, o *BrokerSnapshot, _ *Exposure) { o.QuotePrice = "" }},
		{"reference missing", "observation_reference_mismatch", func(_ *SafetyPolicy, r, _ *BrokerSnapshot, _ *Exposure) { r.ID = "other" }},
		{"reference timestamp", "observation_reference_mismatch", func(_ *SafetyPolicy, r, _ *BrokerSnapshot, _ *Exposure) { r.QuoteAt = r.QuoteAt.Add(time.Second) }},
		{"reference symbol", "observation_reference_mismatch", func(_ *SafetyPolicy, r, _ *BrokerSnapshot, _ *Exposure) { r.Symbol = "MSFT" }},
		{"external holdings", "external_account_drift", func(_ *SafetyPolicy, _, o *BrokerSnapshot, _ *Exposure) {
			o.Positions = []BrokerPosition{{Symbol: "AAPL", Qty: "1", MarketValue: "100"}}
		}},
		{"unknown order", "external_account_drift", func(_ *SafetyPolicy, _, o *BrokerSnapshot, _ *Exposure) {
			o.OpenOrders = []BrokerOrder{{ClientOrderID: "external", Symbol: "AAPL"}}
		}},
		{"uncertainty", "reconciliation_required", func(_ *SafetyPolicy, _, _ *BrokerSnapshot, e *Exposure) { e.Uncertain = true }},
		{"pending same symbol", "open_order_conflict", func(_ *SafetyPolicy, _, _ *BrokerSnapshot, e *Exposure) {
			e.PendingBuy["AAPL"] = "1"
			e.TotalPendingBuy = "1"
		}},
		{"order cap", "order_cap_exceeded", func(p *SafetyPolicy, _, _ *BrokerSnapshot, _ *Exposure) { p.MaxOrder = "0.5" }},
		{"position cap", "position_cap_exceeded", func(p *SafetyPolicy, _, _ *BrokerSnapshot, _ *Exposure) { p.MaxPosition = "0.5" }},
		{"portfolio cap", "portfolio_cap_exceeded", func(p *SafetyPolicy, _, _ *BrokerSnapshot, _ *Exposure) { p.MaxPortfolio = "0.5" }},
		{"pending portfolio", "portfolio_cap_exceeded", func(_ *SafetyPolicy, _, _ *BrokerSnapshot, e *Exposure) {
			e.TotalPendingBuy = "30"
			e.PendingBuy["MSFT"] = "30"
		}},
	} {
		t.Run(tc.name, func(t *testing.T) {
			i, _ := DecodeIntent(fixtureBytes(t))
			p := pilotPolicy()
			ref, current := freshSnapshot(), freshSnapshot()
			e := emptyExposure()
			tc.change(&p, &ref, &current, &e)
			d := p.check(i, ref, current, e, fixtureTime())
			if d.Allowed || d.ReasonCode != tc.reason {
				t.Fatalf("wanted %s got %+v", tc.reason, d)
			}
		})
	}
}

func TestMechanicalSellRequiresLearnerOwnedQuantityAndExitIsExact(t *testing.T) {
	i, _ := DecodeIntent(fixtureBytes(t))
	i.Action = "SELL"
	i.Order.Side = "sell"
	p := pilotPolicy()
	o := freshSnapshot()
	e := emptyExposure()
	if d := p.check(i, o, o, e, fixtureTime()); d.Allowed || d.ReasonCode != "insufficient_owned_position" {
		t.Fatalf("unowned sale %+v", d)
	}
	e.Owned["AAPL"] = "0.02"
	o.Positions = []BrokerPosition{{Symbol: "AAPL", Qty: "0.02", MarketValue: "2"}}
	if d := p.check(i, o, o, e, fixtureTime()); !d.Allowed {
		t.Fatalf("owned sale %+v", d)
	}
	i.Action = "EXIT"
	if d := p.check(i, o, o, e, fixtureTime()); d.Allowed || d.ReasonCode != "exit_quantity_mismatch" {
		t.Fatalf("partial exit %+v", d)
	}
	i.Order.Qty = "0.02"
	if d := p.check(i, o, o, e, fixtureTime()); !d.Allowed {
		t.Fatalf("exact exit %+v", d)
	}
}

func TestMechanicalPriceTickRejectsInsteadOfRounding(t *testing.T) {
	i, _ := DecodeIntent(fixtureBytes(t))
	i.Order.LimitPrice = "100.001"
	o := freshSnapshot()
	d := pilotPolicy().check(i, o, o, emptyExposure(), fixtureTime())
	if d.Allowed || d.ReasonCode != "unsupported_price_tick" || i.Order.LimitPrice != "100.001" {
		t.Fatalf("price changed or accepted %+v", d)
	}
}

func TestOpenOrderClientIDDoesNotAuthorizeChangedEconomics(t *testing.T) {
	intent, _ := DecodeIntent(fixtureBytes(t))
	prior, _ := DecodeIntent(fixtureBytes(t))
	prior.Symbol = "MSFT"
	order := BrokerOrder{OrderID: "known-order", AccountID: prior.AccountID, ClientOrderID: "known-client", Symbol: "MSFT", Side: "buy", Qty: "0.01", LimitPrice: "100", Type: "limit", TimeInForce: "day", State: "accepted", FilledQty: "0", UpdatedAt: fixtureTime().Format(timestampLayout)}
	for _, test := range []struct {
		name   string
		mutate func(*BrokerSnapshot)
		reason string
	}{
		{"matching", func(o *BrokerSnapshot) {}, ""},
		{"quantity", func(o *BrokerSnapshot) { o.OpenOrders[0].Qty = "1" }, "external_account_drift"},
		{"price", func(o *BrokerSnapshot) { o.OpenOrders[0].LimitPrice = "101" }, "external_account_drift"},
		{"side", func(o *BrokerSnapshot) { o.OpenOrders[0].Side = "sell" }, "external_account_drift"},
		{"symbol", func(o *BrokerSnapshot) { o.OpenOrders[0].Symbol = "TSLA" }, "external_account_drift"},
		{"broker ID", func(o *BrokerSnapshot) { o.OpenOrders[0].OrderID = "replaced-order" }, "external_account_drift"},
		{"account", func(o *BrokerSnapshot) { o.OpenOrders[0].AccountID = "other" }, "external_account_drift"},
		{"fill ahead", func(o *BrokerSnapshot) {
			o.OpenOrders[0].State = "partially_filled"
			o.OpenOrders[0].FilledQty = "0.001"
			price := "100"
			o.OpenOrders[0].FilledAveragePrice = &price
		}, "external_account_drift"},
		{"missing", func(o *BrokerSnapshot) { o.OpenOrders = []BrokerOrder{} }, "reconciliation_required"},
		{"duplicate", func(o *BrokerSnapshot) { o.OpenOrders = append(o.OpenOrders, order) }, "observation_incomplete"},
	} {
		t.Run(test.name, func(t *testing.T) {
			e := emptyExposure()
			e.KnownOrders = map[string]JournalOrder{"known-client": {Intent: prior, Record: ExecutionRecord{ClientOrderID: "known-client", BrokerOrderID: "known-order", FilledQty: "0"}}}
			e.PendingBuy["MSFT"] = "1"
			e.TotalPendingBuy = "1"
			current := freshSnapshot()
			current.OpenOrders = []BrokerOrder{order}
			test.mutate(&current)
			d := pilotPolicy().check(intent, freshSnapshot(), current, e, fixtureTime())
			if d.ReasonCode != test.reason || d.Allowed != (test.reason == "") {
				t.Fatalf("expected %s got %+v", test.reason, d)
			}
		})
	}
}

func TestSafetyDenialRetainsMeasuredValueAndConfiguredLimit(t *testing.T) {
	i, _ := DecodeIntent(fixtureBytes(t))
	p := pilotPolicy()
	p.MaxOrder = "0.5"
	d := p.check(i, freshSnapshot(), freshSnapshot(), emptyExposure(), fixtureTime())
	if d.Allowed || d.Measurements["orderNotional"] != "1" || d.Measurements["maxOrderNotional"] != "0.5" || d.Measurements["buyingPower"] != "100" {
		t.Fatalf("denial omitted comparison evidence: %+v", d)
	}
}
