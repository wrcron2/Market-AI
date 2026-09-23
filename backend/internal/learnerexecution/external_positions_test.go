package learnerexecution

import (
	"context"
	"path/filepath"
	"testing"
)

func TestExternalBaselineObservationIsNotLearnerOwnership(t *testing.T) {
	s := executionStore(t, filepath.Join(t.TempDir(), "external.db"))
	b := &policyBroker{journalBroker: newJournalBroker(), snapshot: freshSnapshot()}
	b.snapshot.Positions = []BrokerPosition{{Symbol: "XLE", Qty: "12.5", MarketValue: "1250"}}
	p := pilotPolicy()
	p.ExternalPositions = "XLE=12.5"
	service := policyService(t, s, b, p)
	o, err := service.Observe(context.Background(), "AAPL")
	if err != nil || o.UnresolvedExecution || o.ReservedSymbol || o.OwnedQuantity != "0" {
		t.Fatalf("unrelated observation: %+v %v", o, err)
	}
	o, err = service.Observe(context.Background(), "XLE")
	if err != nil || !o.UnresolvedExecution || !o.ReservedSymbol || o.OwnedQuantity != "0" {
		t.Fatalf("reserved symbol attributed: %+v %v", o, err)
	}
	b.snapshot.Positions[0].Qty = "13"
	o, err = service.Observe(context.Background(), "AAPL")
	if err != nil || !o.UnresolvedExecution {
		t.Fatalf("changed baseline ignored: %+v %v", o, err)
	}
}

// Catches the former exclusive-account check rejecting an unrelated AAPL buy,
// or incorrectly charging a pre-existing holding to the learner's pilot budget.
func TestExternalPositionBaselineAllowsUnrelatedLearnerTrade(t *testing.T) {
	i, _ := DecodeIntent(fixtureBytes(t))
	p := pilotPolicy()
	p.ExternalPositions = "XLE=12.5"
	o := freshSnapshot()
	o.Positions = []BrokerPosition{{Symbol: "XLE", Qty: "12.5", MarketValue: "1250"}}
	d := p.check(i, o, o, emptyExposure(), fixtureTime())
	if !d.Allowed || d.Measurements["portfolioNotionalBefore"] != "0" || d.Measurements["externalPortfolioNotional"] != "1250" {
		t.Fatalf("external holding blocked or became learner exposure: %+v", d)
	}
}

func TestExternalBaselineDoesNotHideDriftOrPermitTradingReservedSymbol(t *testing.T) {
	for _, tc := range []struct {
		name           string
		positions      []BrokerPosition
		symbol, reason string
	}{
		{"changed baseline", []BrokerPosition{{Symbol: "XLE", Qty: "13", MarketValue: "1300"}}, "AAPL", "external_account_drift"},
		{"missing baseline", []BrokerPosition{}, "AAPL", "external_account_drift"},
		{"unknown position", []BrokerPosition{{Symbol: "XLE", Qty: "12.5", MarketValue: "1250"}, {Symbol: "MSFT", Qty: "1", MarketValue: "100"}}, "AAPL", "external_account_drift"},
		{"reserved symbol", []BrokerPosition{{Symbol: "XLE", Qty: "12.5", MarketValue: "1250"}}, "XLE", "external_position_reserved"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			i, _ := DecodeIntent(fixtureBytes(t))
			i.Symbol = tc.symbol
			p := pilotPolicy()
			p.ExternalPositions = "XLE=12.5"
			o := freshSnapshot()
			o.Symbol = tc.symbol
			o.Positions = tc.positions
			d := p.check(i, o, o, emptyExposure(), fixtureTime())
			if d.Allowed || d.ReasonCode != tc.reason {
				t.Fatalf("want %s, got %+v", tc.reason, d)
			}
		})
	}
}

func TestExternalBaselineCannotMaskExistingLearnerOwnership(t *testing.T) {
	i, _ := DecodeIntent(fixtureBytes(t))
	p := pilotPolicy()
	p.ExternalPositions = "XLE=12.5"
	o := freshSnapshot()
	o.Positions = []BrokerPosition{{Symbol: "XLE", Qty: "12.5", MarketValue: "1250"}}
	e := emptyExposure()
	e.Owned["XLE"] = "0.1"
	if d := p.check(i, o, o, e, fixtureTime()); d.Allowed || d.ReasonCode != "external_account_drift" {
		t.Fatalf("masked learner position: %+v", d)
	}
}

func TestMalformedExternalBaselineCannotConfigureRuntime(t *testing.T) {
	for _, raw := range []string{"XLE", "xle=1", "XLE=0", "XLE=-1", "XLE=NaN", "XLE=1,XLE=2", " XLE=1", "XLE=1,", "XLE=1=2"} {
		p := pilotPolicy()
		p.ExternalPositions = raw
		if p.configured() {
			t.Errorf("invalid baseline accepted: %q", raw)
		}
	}
	t.Setenv("KIMI_EXTERNAL_POSITIONS", "XLE=12.5")
	if RuntimeConfigFromEnv().Policy.ExternalPositions != "XLE=12.5" {
		t.Fatal("runtime dropped baseline")
	}
}
