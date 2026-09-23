package learnerexecution

import (
	"math/big"
	"time"
)

// SafetyPolicy contains operator-set mechanical bounds, never model judgments.
// Missing limits disable submission. Values are copied by the service factory.
type SafetyPolicy struct {
	// Operator-recorded holdings excluded from this application's ownership.
	ExternalPositions string
	Enabled           bool
	KillSwitch        bool
	AccountID         string
	Version           string
	MaxOrder          string
	MaxPosition       string
	MaxPortfolio      string
	MaxDaily          string
	MaxAge            time.Duration
}

type BrokerPosition struct {
	Symbol      string `json:"symbol"`
	Qty         string `json:"qty"`
	MarketValue string `json:"marketValue"`
}

// Complete means all mandatory broker reads succeeded without truncation.
// RetrievedAt is local retrieval time; QuoteAt and ClockAt are broker timestamps.
// Neither can be substituted for the other to make stale data appear fresh.
type BrokerSnapshot struct {
	OwnedQuantity       string           `json:"ownedQuantity"`
	PendingBuyNotional  string           `json:"pendingBuyNotional"`
	PendingSellQuantity string           `json:"pendingSellQuantity"`
	UnresolvedExecution bool             `json:"unresolvedExecution"`
	ReservedSymbol      bool             `json:"reservedSymbol"`
	Source              string           `json:"source"`
	SourceQuoteAt       string           `json:"sourceQuoteAt"`
	ID                  string           `json:"id"`
	AccountID           string           `json:"accountId"`
	Symbol              string           `json:"symbol"`
	Paper               bool             `json:"paper"`
	Complete            bool             `json:"complete"`
	AccountStatus       string           `json:"accountStatus"`
	TradingBlocked      bool             `json:"tradingBlocked"`
	BuyingPower         string           `json:"buyingPower"`
	QuotePrice          string           `json:"quotePrice"`
	QuoteAt             time.Time        `json:"quoteAt"`
	RetrievedAt         time.Time        `json:"retrievedAt"`
	ClockAt             time.Time        `json:"clockAt"`
	SessionOpen         bool             `json:"sessionOpen"`
	AssetClass          string           `json:"assetClass"`
	Tradable            bool             `json:"tradable"`
	Fractionable        bool             `json:"fractionable"`
	Positions           []BrokerPosition `json:"positions"`
	OpenOrders          []BrokerOrder    `json:"openOrders"`
}

// Exposure must be derived from the durable journal, never supplied by a user
// or inferred from an account's total holdings. Pending buys are limit notional;
// pending sells are quantities. All maps are scoped to the configured account.
type Exposure struct {
	Owned           map[string]string
	PendingBuy      map[string]string
	PendingSell     map[string]string
	KnownOrders     map[string]JournalOrder
	TotalPendingBuy string
	Uncertain       bool
}

type JournalOrder struct {
	Intent Intent
	Record ExecutionRecord
}

func accountingAmount(value string) (*big.Rat, bool) {
	if !storedNotionalPattern.MatchString(value) {
		return nil, false
	}
	n, ok := new(big.Rat).SetString(value)
	return n, ok && n.Sign() >= 0
}
func fresh(at, now time.Time, age time.Duration) bool {
	return !at.IsZero() && !at.After(now) && now.Sub(at) <= age
}

// Used for both mechanical admission and read-side ownership claims. A fresh
// broker retrieval does not make historical journal attribution current.
func (p SafetyPolicy) ownershipEvidenceCode(current BrokerSnapshot, e Exposure, now time.Time) string {
	external, err := p.externalPositions()
	if err != nil {
		return "external_positions_invalid"
	}
	if !current.Complete || current.Positions == nil || current.OpenOrders == nil {
		return "observation_incomplete"
	}
	if e.Uncertain {
		return "reconciliation_required"
	}
	if e.Owned == nil || e.KnownOrders == nil {
		return "exposure_unavailable"
	}
	positions := map[string]*big.Rat{}
	for _, position := range current.Positions {
		if !symbolPattern.MatchString(position.Symbol) {
			return "observation_incomplete"
		}
		if _, exists := positions[position.Symbol]; exists {
			return "observation_incomplete"
		}
		qty, err := signedPositionDecimal(position.Qty)
		if err != nil {
			return "external_account_drift"
		}
		positions[position.Symbol] = qty
		owned := new(big.Rat)
		if text, exists := e.Owned[position.Symbol]; exists {
			var valid bool
			owned, valid = accountingAmount(text)
			if !valid {
				return "exposure_unavailable"
			}
		}
		if baseline, reserved := external[position.Symbol]; reserved {
			expected, baselineErr := signedPositionDecimal(baseline)
			if baselineErr != nil || owned.Sign() != 0 || qty.Cmp(expected) != 0 {
				return "external_account_drift"
			}
		} else if owned.Cmp(qty) != 0 {
			return "external_account_drift"
		}
	}
	for symbol := range external {
		if _, exists := positions[symbol]; !exists {
			return "external_account_drift"
		}
	}
	for symbol, text := range e.Owned {
		owned, valid := accountingAmount(text)
		if !valid {
			return "exposure_unavailable"
		}
		if _, exists := positions[symbol]; !exists && owned.Sign() != 0 {
			return "external_account_drift"
		}
	}
	seen := map[string]bool{}
	for _, order := range current.OpenOrders {
		if _, reserved := external[order.Symbol]; reserved {
			return "external_account_drift"
		}
		if seen[order.ClientOrderID] {
			return "observation_incomplete"
		}
		seen[order.ClientOrderID] = true
		known, exists := e.KnownOrders[order.ClientOrderID]
		if !exists || !validBrokerOrder(known.Intent, known.Record, order, now) || !sameDecimal(known.Record.FilledQty, order.FilledQty) || terminal(order.State) {
			return "external_account_drift"
		}
	}
	for id := range e.KnownOrders {
		if !seen[id] {
			return "reconciliation_required"
		}
	}
	return ""
}

func (p SafetyPolicy) check(i Intent, reference, current BrokerSnapshot, e Exposure, now time.Time) SafetyDecision {
	d := SafetyDecision{Allowed: false, PolicyVersion: p.Version, DailyLimit: p.MaxDaily}
	for key, value := range map[string]string{"maxOrderNotional": p.MaxOrder, "maxPositionNotional": p.MaxPosition, "maxPortfolioNotional": p.MaxPortfolio, "maxDailyNotional": p.MaxDaily} {
		d.measure(key, value)
	}
	deny := func(code string) SafetyDecision { d.ReasonCode = code; return d }
	external, err := p.externalPositions()
	if err != nil {
		return deny("external_positions_invalid")
	}
	if _, reserved := external[i.Symbol]; reserved {
		return deny("external_position_reserved")
	}
	if !p.Enabled {
		return deny("execution_disabled")
	}
	if p.KillSwitch {
		return deny("kill_switch")
	}
	if i.ValidateFresh(now) != nil {
		return deny("intent_expired_or_future")
	}
	if p.Version != i.PolicyVersion {
		return deny("policy_version_mismatch")
	}
	limits := make([]*big.Rat, 0, 4)
	for _, value := range []string{p.MaxOrder, p.MaxPosition, p.MaxPortfolio, p.MaxDaily} {
		n, err := decimal(value, pricePattern)
		if err != nil {
			return deny("limits_not_configured")
		}
		limits = append(limits, n)
	}
	if p.MaxAge <= 0 || p.MaxAge > time.Minute {
		return deny("limits_not_configured")
	}
	if !current.Paper || !reference.Paper {
		return deny("paper_required")
	}
	if p.AccountID == "" || p.AccountID != i.AccountID || current.AccountID != i.AccountID || reference.AccountID != i.AccountID {
		return deny("account_mismatch")
	}
	if !current.Complete || !reference.Complete || current.Positions == nil || current.OpenOrders == nil {
		return deny("observation_incomplete")
	}
	marketAt, _ := stamp(i.MarketDataAt)
	decidedAt, _ := stamp(i.DecidedAt)
	if reference.ID != i.ObservationID || reference.Symbol != i.Symbol || current.Symbol != i.Symbol || !reference.QuoteAt.Equal(marketAt) || reference.RetrievedAt.After(decidedAt) {
		return deny("observation_reference_mismatch")
	}
	if !fresh(reference.RetrievedAt, now, p.MaxAge) || !fresh(current.RetrievedAt, now, p.MaxAge) {
		return deny("observation_stale")
	}
	if !fresh(reference.QuoteAt, now, p.MaxAge) || !fresh(current.QuoteAt, now, p.MaxAge) {
		return deny("market_data_stale")
	}
	d.measure("referenceObservationId", reference.ID)
	d.measure("currentRetrievedAt", current.RetrievedAt.UTC().Format(timestampLayout))
	d.measure("currentQuoteAt", current.QuoteAt.UTC().Format(timestampLayout))
	d.measure("checkedAt", now.UTC().Format(timestampLayout))
	if current.AccountStatus != "ACTIVE" || current.TradingBlocked {
		return deny("account_unavailable")
	}
	power, ok := observedDecimal(current.BuyingPower)
	if ok != nil {
		return deny("buying_power_unavailable")
	}
	d.measure("buyingPower", exactDecimal(power))
	quote, err := observedDecimal(current.QuotePrice)
	if err != nil || quote.Sign() <= 0 {
		return deny("market_data_unavailable")
	}
	d.measure("quotePrice", exactDecimal(quote))
	if !current.SessionOpen || !fresh(current.ClockAt, now, p.MaxAge) {
		return deny("session_unavailable")
	}
	until, _ := stamp(i.ExpiresAt)
	for _, at := range []time.Time{reference.RetrievedAt, reference.QuoteAt, current.RetrievedAt, current.QuoteAt, current.ClockAt} {
		cutoff := at.Add(p.MaxAge)
		if cutoff.Before(until) {
			until = cutoff
		}
	}
	d.ValidUntil = &until
	if current.AssetClass != "us_equity" || !current.Tradable {
		return deny("unsupported_asset")
	}
	if e.Uncertain {
		return deny("reconciliation_required")
	}
	if e.Owned == nil || e.PendingBuy == nil || e.PendingSell == nil || e.KnownOrders == nil {
		return deny("exposure_unavailable")
	}
	pending, valid := accountingAmount(e.TotalPendingBuy)
	if !valid {
		return deny("exposure_unavailable")
	}
	d.measure("pendingBuyNotional", exactDecimal(pending))
	ownedQuantity := e.Owned[i.Symbol]
	if ownedQuantity == "" {
		ownedQuantity = "0"
	}
	d.measure("ownedQuantity", ownedQuantity)
	positions := map[string]*big.Rat{}
	positionValue := new(big.Rat)
	portfolioValue := new(big.Rat)
	externalValue := new(big.Rat)
	for _, position := range current.Positions {
		if !symbolPattern.MatchString(position.Symbol) {
			return deny("observation_incomplete")
		}
		if _, exists := positions[position.Symbol]; exists {
			return deny("observation_incomplete")
		}
		qty, err := signedPositionDecimal(position.Qty)
		if err != nil {
			return deny("external_account_drift")
		}
		value, err := signedPositionDecimal(position.MarketValue)
		if err != nil || qty.Sign() != value.Sign() {
			return deny("observation_incomplete")
		}
		positions[position.Symbol] = qty
		portfolioValue.Add(portfolioValue, value)
		if _, reserved := external[position.Symbol]; reserved {
			externalValue.Add(externalValue, new(big.Rat).Abs(value))
			portfolioValue.Sub(portfolioValue, value)
			continue
		}
		if position.Symbol == i.Symbol {
			positionValue.Set(value)
		}
	}
	d.measure("positionNotionalBefore", exactDecimal(positionValue))
	d.measure("portfolioNotionalBefore", exactDecimal(portfolioValue))
	d.measure("externalPortfolioNotional", exactDecimal(externalValue))
	if code := p.ownershipEvidenceCode(current, e, now); code != "" {
		return deny(code)
	}
	for _, order := range current.OpenOrders {
		if order.Symbol == i.Symbol {
			return deny("open_order_conflict")
		}
	}
	for _, amounts := range []map[string]string{e.PendingBuy, e.PendingSell} {
		if text, exists := amounts[i.Symbol]; exists {
			n, valid := accountingAmount(text)
			if !valid {
				return deny("exposure_unavailable")
			}
			if n.Sign() > 0 {
				return deny("open_order_conflict")
			}
		}
	}
	if i.Action == "HOLD" {
		d.Allowed = true
		return d
	}
	qty, _ := decimal(i.Order.Qty, qtyPattern)
	price, _ := decimal(i.Order.LimitPrice, pricePattern)
	if !current.Fractionable && !qty.IsInt() {
		return deny("fractional_unsupported")
	}
	tick := int64(100)
	if price.Cmp(big.NewRat(1, 1)) < 0 {
		tick = 10000
	}
	if !new(big.Rat).Mul(price, big.NewRat(tick, 1)).IsInt() {
		return deny("unsupported_price_tick")
	}
	amount := new(big.Rat).Mul(qty, price)
	d.measure("orderNotional", exactDecimal(amount))
	if i.Order.Side == "buy" {
		d.measure("projectedPositionNotional", exactDecimal(new(big.Rat).Add(positionValue, amount)))
		d.measure("projectedPortfolioNotional", exactDecimal(new(big.Rat).Add(new(big.Rat).Add(portfolioValue, pending), amount)))
	}
	if amount.Cmp(limits[0]) > 0 {
		return deny("order_cap_exceeded")
	}
	if i.Order.Side == "buy" {
		if new(big.Rat).Add(amount, pending).Cmp(power) > 0 {
			return deny("insufficient_buying_power")
		}
		if new(big.Rat).Add(positionValue, amount).Cmp(limits[1]) > 0 {
			return deny("position_cap_exceeded")
		}
		portfolioValue.Add(portfolioValue, pending)
		portfolioValue.Add(portfolioValue, amount)
		if portfolioValue.Cmp(limits[2]) > 0 {
			return deny("portfolio_cap_exceeded")
		}
	} else {
		owned := new(big.Rat)
		if text, exists := e.Owned[i.Symbol]; exists {
			owned, _ = accountingAmount(text)
		}
		if owned == nil || qty.Cmp(owned) > 0 {
			return deny("insufficient_owned_position")
		}
		if i.Action == "EXIT" && qty.Cmp(owned) != 0 {
			return deny("exit_quantity_mismatch")
		}
	}
	d.Allowed = true
	return d
}
