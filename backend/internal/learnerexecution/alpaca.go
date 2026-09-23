package learnerexecution

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/url"
	"regexp"
	"time"

	"github.com/marketflow/backend/internal/operatingmode"
)

const maxBrokerBody = 1 << 20

var clientIDPattern = regexp.MustCompile(`^kl-[0-9a-f]{40}$`)
var errBrokerResponse = errors.New("broker_response_invalid")
var errBrokerUnavailable = errors.New("broker_unavailable")

type PaperBrokerConfig struct {
	BaseURL       string
	APIKey        string
	SecretKey     string
	AccountID     string
	Authority     string
	OperatingMode string
	Paper         bool
}
type PaperBroker struct {
	config PaperBrokerConfig
	client *http.Client
	now    func() time.Time
}

func NewPaperBroker(config PaperBrokerConfig) (*PaperBroker, error) {
	return newPaperBroker(config, http.DefaultTransport)
}
func newPaperBroker(c PaperBrokerConfig, transport http.RoundTripper) (*PaperBroker, error) {
	if c.BaseURL != operatingmode.PaperBrokerBaseURL || c.Authority != "KIMI" || c.OperatingMode != "paper" || !c.Paper || c.APIKey == "" || c.SecretKey == "" || !idPattern.MatchString(c.AccountID) || transport == nil {
		return nil, errors.New("paper_broker_not_configured")
	}
	return &PaperBroker{config: c, now: time.Now, client: &http.Client{Transport: transport, Timeout: 10 * time.Second, CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}}, nil
}

// No provider body, credentials, signed URL or underlying transport error is
// included in returned errors. Every response is bounded and duplicate-safe.
func (b *PaperBroker) request(ctx context.Context, method, path string, body []byte) (any, int, error) {
	return b.requestHost(ctx, b.config.BaseURL, method, path, body)
}
func (b *PaperBroker) requestHost(ctx context.Context, host, method, path string, body []byte) (any, int, error) {
	if host != operatingmode.PaperBrokerBaseURL && (host != "https://data.alpaca.markets" || method != http.MethodGet) {
		return nil, 0, errBrokerUnavailable
	}
	req, err := http.NewRequestWithContext(ctx, method, host+path, bytes.NewReader(body))
	if err != nil {
		return nil, 0, errBrokerUnavailable
	}
	req.Header.Set("APCA-API-KEY-ID", b.config.APIKey)
	req.Header.Set("APCA-API-SECRET-KEY", b.config.SecretKey)
	req.Header.Set("Accept", "application/json")
	if body != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	resp, err := b.client.Do(req)
	if err != nil {
		return nil, 0, errBrokerUnavailable
	}
	defer resp.Body.Close()
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return nil, resp.StatusCode, errBrokerUnavailable
	}
	if resp.StatusCode == http.StatusNoContent {
		return nil, resp.StatusCode, nil
	}
	raw, err := io.ReadAll(io.LimitReader(resp.Body, maxBrokerBody+1))
	if err != nil || len(raw) > maxBrokerBody {
		return nil, resp.StatusCode, errBrokerResponse
	}
	d := json.NewDecoder(bytes.NewReader(raw))
	d.UseNumber()
	v, err := readValue(d, 0)
	if err != nil {
		return nil, resp.StatusCode, errBrokerResponse
	}
	if _, err = d.Token(); err != io.EOF {
		return nil, resp.StatusCode, errBrokerResponse
	}
	return v, resp.StatusCode, nil
}
func requiredString(m map[string]any, key string) (string, bool) {
	s, ok := m[key].(string)
	return s, ok && len(s) > 0 && len(s) <= 256
}

func (b *PaperBroker) account(ctx context.Context) (map[string]any, error) {
	v, _, err := b.request(ctx, http.MethodGet, "/v2/account", nil)
	if err != nil {
		return nil, err
	}
	m, ok := v.(map[string]any)
	if !ok {
		return nil, errBrokerResponse
	}
	id, ok := requiredString(m, "id")
	if !ok || id != b.config.AccountID {
		return nil, errors.New("broker_account_mismatch")
	}
	return m, nil
}

func tradableAccount(m map[string]any) error {
	status, ok := requiredString(m, "status")
	if !ok || status != "ACTIVE" {
		return errors.New("broker_account_unavailable")
	}
	for _, key := range []string{"trading_blocked", "account_blocked"} {
		blocked, ok := m[key].(bool)
		if !ok || blocked {
			return errors.New("broker_account_unavailable")
		}
	}
	return nil
}

func normalizeOrder(value any, accountID string) (BrokerOrder, error) {
	var o BrokerOrder
	m, ok := value.(map[string]any)
	if !ok {
		return o, errBrokerResponse
	}
	fields := map[string]*string{"id": &o.OrderID, "client_order_id": &o.ClientOrderID, "symbol": &o.Symbol, "side": &o.Side, "qty": &o.Qty, "limit_price": &o.LimitPrice, "type": &o.Type, "time_in_force": &o.TimeInForce, "status": &o.State, "filled_qty": &o.FilledQty, "updated_at": &o.UpdatedAt}
	for key, target := range fields {
		value, ok := requiredString(m, key)
		if !ok {
			return o, errBrokerResponse
		}
		*target = value
	}
	if !idPattern.MatchString(o.OrderID) || !symbolPattern.MatchString(o.Symbol) || o.Type != "limit" || o.TimeInForce != "day" || (o.Side != "buy" && o.Side != "sell") {
		return o, errBrokerResponse
	}
	class, ok := m["order_class"].(string)
	if !ok || (class != "" && class != "simple") {
		return o, errBrokerResponse
	}
	extended, ok := m["extended_hours"].(bool)
	if !ok || extended {
		return o, errBrokerResponse
	}
	if legs, present := m["legs"]; present && legs != nil {
		a, ok := legs.([]any)
		if !ok || len(a) != 0 {
			return o, errBrokerResponse
		}
	}
	if _, err := time.Parse(time.RFC3339Nano, o.UpdatedAt); err != nil {
		return o, errBrokerResponse
	}
	qty, err := observedDecimal(o.Qty)
	if err != nil || qty.Sign() <= 0 {
		return o, errBrokerResponse
	}
	price, err := observedDecimal(o.LimitPrice)
	if err != nil || price.Sign() <= 0 {
		return o, errBrokerResponse
	}
	filled, err := observedDecimal(o.FilledQty)
	if err != nil || filled.Cmp(qty) > 0 {
		return o, errBrokerResponse
	}
	avg, present := m["filled_avg_price"]
	if !present {
		return o, errBrokerResponse
	}
	if avg != nil {
		value, ok := avg.(string)
		if !ok {
			return o, errBrokerResponse
		}
		n, err := observedDecimal(value)
		if err != nil || n.Sign() <= 0 {
			return o, errBrokerResponse
		}
		o.FilledAveragePrice = &value
	}
	if (filled.Sign() > 0) != (o.FilledAveragePrice != nil) {
		return o, errBrokerResponse
	}
	switch o.State {
	case "new", "pending_new", "accepted", "accepted_for_bidding", "pending_cancel":
		o.State = "accepted"
		if filled.Sign() > 0 {
			o.State = "partially_filled"
		}
	}
	o.AccountID = accountID
	return o, nil
}

func (b *PaperBroker) Submit(ctx context.Context, i Intent, clientID string) (BrokerOrder, error) {
	if i.ValidateFresh(b.now()) != nil || i.Order == nil || i.AccountID != b.config.AccountID || !clientIDPattern.MatchString(clientID) || clientID != clientOrderID(i.AccountID, i.DecisionID) {
		return BrokerOrder{}, errors.New("invalid_broker_instruction")
	}
	account, err := b.account(ctx)
	if err != nil {
		return BrokerOrder{}, err
	}
	if err = tradableAccount(account); err != nil {
		return BrokerOrder{}, err
	}
	if i.ValidateFresh(b.now()) != nil {
		return BrokerOrder{}, errors.New("intent_expired_or_future")
	}
	// Only these exact mechanics are permitted. There is no quote-as-fill fallback,
	// strategy filter, notional conversion, rounding, replacement or auto-retry.
	body, _ := json.Marshal(map[string]any{"symbol": i.Symbol, "side": i.Order.Side, "qty": i.Order.Qty, "type": i.Order.Type, "limit_price": i.Order.LimitPrice, "time_in_force": i.Order.TimeInForce, "client_order_id": clientID, "order_class": "simple", "extended_hours": false})
	v, _, err := b.request(ctx, http.MethodPost, "/v2/orders", body)
	if err != nil {
		return BrokerOrder{}, err
	}
	o, err := normalizeOrder(v, b.config.AccountID)
	if err != nil {
		return o, err
	}
	if o.ClientOrderID != clientID || o.Symbol != i.Symbol || o.Side != i.Order.Side || !sameDecimal(o.Qty, i.Order.Qty) || !sameDecimal(o.LimitPrice, i.Order.LimitPrice) {
		return BrokerOrder{}, errBrokerResponse
	}
	return o, nil
}
func (b *PaperBroker) LookupByClientID(ctx context.Context, id string) (*BrokerOrder, error) {
	if !clientIDPattern.MatchString(id) {
		return nil, errors.New("invalid_client_order_id")
	}
	if _, err := b.account(ctx); err != nil {
		return nil, err
	}
	v, status, err := b.request(ctx, http.MethodGet, "/v2/orders:by_client_order_id?client_order_id="+url.QueryEscape(id), nil)
	if status == http.StatusNotFound {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	o, err := normalizeOrder(v, b.config.AccountID)
	if err != nil {
		return nil, err
	}
	if o.ClientOrderID != id {
		return nil, errBrokerResponse
	}
	return &o, nil
}
