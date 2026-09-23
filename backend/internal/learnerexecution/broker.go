package learnerexecution

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/url"
	"time"
)

type Broker interface {
	SubmissionBroker
	Observe(context.Context, string) (BrokerSnapshot, error)
	Cancel(context.Context, string, string) error
}

func brokerTime(m map[string]any, key string) (time.Time, error) {
	text, ok := requiredString(m, key)
	if !ok {
		return time.Time{}, errBrokerResponse
	}
	at, err := time.Parse(time.RFC3339Nano, text)
	if err != nil {
		return time.Time{}, errBrokerResponse
	}
	return at, nil
}

func (b *PaperBroker) Observe(ctx context.Context, symbol string) (BrokerSnapshot, error) {
	var out BrokerSnapshot
	if !symbolPattern.MatchString(symbol) {
		return out, errors.New("invalid_symbol")
	}
	started := b.now()
	account, err := b.account(ctx)
	if err != nil {
		return out, err
	}
	status, ok := requiredString(account, "status")
	if !ok {
		return out, errBrokerResponse
	}
	blocked, ok := account["trading_blocked"].(bool)
	if !ok {
		return out, errBrokerResponse
	}
	accountBlocked, ok := account["account_blocked"].(bool)
	if !ok {
		return out, errBrokerResponse
	}
	power, ok := requiredString(account, "buying_power")
	if !ok {
		return out, errBrokerResponse
	}
	available, err := observedDecimal(power)
	if err != nil {
		return out, errBrokerResponse
	}
	cashText, ok := requiredString(account, "cash")
	if !ok {
		return out, errBrokerResponse
	}
	cash, err := observedDecimal(cashText)
	if err != nil {
		return out, errBrokerResponse
	}
	// The wire field is retained for compatibility; it exposes cash-limited
	// spending capacity, never Alpaca's margin buying power.
	if cash.Cmp(available) < 0 {
		available = cash
	}
	power = exactDecimal(available)
	clockValue, _, err := b.request(ctx, http.MethodGet, "/v2/clock", nil)
	if err != nil {
		return out, err
	}
	clock, ok := clockValue.(map[string]any)
	if !ok {
		return out, errBrokerResponse
	}
	clockAt, err := brokerTime(clock, "timestamp")
	if err != nil {
		return out, err
	}
	open, ok := clock["is_open"].(bool)
	if !ok {
		return out, errBrokerResponse
	}
	assetValue, _, err := b.request(ctx, http.MethodGet, "/v2/assets/"+url.PathEscape(symbol), nil)
	if err != nil {
		return out, err
	}
	asset, ok := assetValue.(map[string]any)
	if !ok {
		return out, errBrokerResponse
	}
	assetSymbol, ok := requiredString(asset, "symbol")
	if !ok || assetSymbol != symbol {
		return out, errBrokerResponse
	}
	class, ok := requiredString(asset, "class")
	if !ok {
		return out, errBrokerResponse
	}
	assetStatus, ok := requiredString(asset, "status")
	if !ok {
		return out, errBrokerResponse
	}
	tradable, ok := asset["tradable"].(bool)
	if !ok {
		return out, errBrokerResponse
	}
	fractionable, ok := asset["fractionable"].(bool)
	if !ok {
		return out, errBrokerResponse
	}
	positionsValue, _, err := b.request(ctx, http.MethodGet, "/v2/positions", nil)
	if err != nil {
		return out, err
	}
	positions, ok := positionsValue.([]any)
	if !ok {
		return out, errBrokerResponse
	}
	owned := make([]BrokerPosition, 0, len(positions))
	for _, value := range positions {
		m, ok := value.(map[string]any)
		if !ok {
			return out, errBrokerResponse
		}
		sym, ok := requiredString(m, "symbol")
		if !ok || !symbolPattern.MatchString(sym) {
			return out, errBrokerResponse
		}
		qty, ok := requiredString(m, "qty")
		if !ok {
			return out, errBrokerResponse
		}
		marketValue, ok := requiredString(m, "market_value")
		if !ok {
			return out, errBrokerResponse
		}
		if _, err := observedDecimal(qty); err != nil {
			return out, errBrokerResponse
		}
		if _, err := observedDecimal(marketValue); err != nil {
			return out, errBrokerResponse
		}
		owned = append(owned, BrokerPosition{Symbol: sym, Qty: qty, MarketValue: marketValue})
	}
	ordersValue, _, err := b.request(ctx, http.MethodGet, "/v2/orders?status=open&limit=500&nested=false", nil)
	if err != nil {
		return out, err
	}
	orders, ok := ordersValue.([]any)
	if !ok || len(orders) >= 500 {
		return out, errors.New("broker_orders_incomplete")
	}
	openOrders := make([]BrokerOrder, 0, len(orders))
	for _, value := range orders {
		o, err := normalizeOrder(value, b.config.AccountID)
		if err != nil {
			return out, err
		}
		openOrders = append(openOrders, o)
	}
	quoteValue, _, err := b.requestHost(ctx, "https://data.alpaca.markets", http.MethodGet, "/v2/stocks/quotes/latest?symbols="+url.QueryEscape(symbol)+"&feed=iex", nil)
	if err != nil {
		return out, err
	}
	envelope, ok := quoteValue.(map[string]any)
	if !ok {
		return out, errBrokerResponse
	}
	quotes, ok := envelope["quotes"].(map[string]any)
	if !ok {
		return out, errBrokerResponse
	}
	quote, ok := quotes[symbol].(map[string]any)
	if !ok {
		return out, errBrokerResponse
	}
	askText, ok := quote["ap"].(json.Number)
	if !ok {
		return out, errBrokerResponse
	}
	bidText, ok := quote["bp"].(json.Number)
	if !ok {
		return out, errBrokerResponse
	}
	ask, err := observedDecimal(askText.String())
	if err != nil || ask.Sign() <= 0 {
		return out, errBrokerResponse
	}
	bid, err := observedDecimal(bidText.String())
	if err != nil || bid.Sign() <= 0 || bid.Cmp(ask) > 0 {
		return out, errBrokerResponse
	}
	quoteAt, err := brokerTime(quote, "t")
	if err != nil {
		return out, err
	}
	sourceTime, _ := requiredString(quote, "t")
	return BrokerSnapshot{Source: "alpaca/iex", SourceQuoteAt: sourceTime, AccountID: b.config.AccountID, Symbol: symbol, Paper: true, Complete: true, AccountStatus: status, TradingBlocked: blocked || accountBlocked, BuyingPower: power, QuotePrice: askText.String(), QuoteAt: quoteAt.Truncate(time.Millisecond), RetrievedAt: started, ClockAt: clockAt, SessionOpen: open, AssetClass: class, Tradable: tradable && assetStatus == "active", Fractionable: fractionable, Positions: owned, OpenOrders: openOrders}, nil
}

// Cancellation targets one verified learner client/order pair. A 204 means the
// request was accepted, not that cancellation or zero fills are confirmed.
func (b *PaperBroker) Cancel(ctx context.Context, clientID, orderID string) error {
	if !idPattern.MatchString(orderID) {
		return errors.New("invalid_order_id")
	}
	o, err := b.LookupByClientID(ctx, clientID)
	if err != nil {
		return err
	}
	if o == nil || o.OrderID != orderID {
		return errors.New("cancel_order_mismatch")
	}
	_, status, err := b.request(ctx, http.MethodDelete, "/v2/orders/"+url.PathEscape(orderID), nil)
	if err != nil {
		return err
	}
	if status != http.StatusNoContent {
		return errBrokerResponse
	}
	return nil
}
