package learnerexecution

import (
	"context"
	"crypto/sha256"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"math/big"
	"regexp"
	"strings"
	"time"
)

// BrokerOrder is normalized evidence from the paper adapter, not an instruction.
// AccountID must come from verified account context, never the request payload.
type BrokerOrder struct {
	OrderID            string  `json:"orderId"`
	AccountID          string  `json:"accountId"`
	ClientOrderID      string  `json:"clientOrderId"`
	Symbol             string  `json:"symbol"`
	Side               string  `json:"side"`
	Qty                string  `json:"qty"`
	LimitPrice         string  `json:"limitPrice"`
	Type               string  `json:"type"`
	TimeInForce        string  `json:"timeInForce"`
	State              string  `json:"state"`
	FilledQty          string  `json:"filledQty"`
	FilledAveragePrice *string `json:"filledAveragePrice"`
	UpdatedAt          string  `json:"updatedAt"`
}

var observedDecimalPattern = regexp.MustCompile(`^(0|[1-9][0-9]{0,11})(\.[0-9]{1,12})?$`)
var storedNotionalPattern = regexp.MustCompile(`^(0|[1-9][0-9]{0,23})(\.[0-9]{1,24})?$`)

func observedDecimal(value string) (*big.Rat, error) {
	if !observedDecimalPattern.MatchString(value) {
		return nil, errors.New("invalid_broker_decimal")
	}
	n, ok := new(big.Rat).SetString(value)
	if !ok || n.Sign() < 0 {
		return nil, errors.New("invalid_broker_decimal")
	}
	return n, nil
}
func sameDecimal(a, b string) bool {
	x, e := observedDecimal(a)
	if e != nil {
		return false
	}
	y, e := observedDecimal(b)
	return e == nil && x.Cmp(y) == 0
}

// Products have at most 24 decimal places (two <=12-place broker values).
func exactDecimal(n *big.Rat) string {
	v := strings.TrimRight(strings.TrimRight(n.FloatString(24), "0"), ".")
	if v == "" {
		return "0"
	}
	return v
}
func terminal(state string) bool {
	switch state {
	case "filled", "canceled", "expired", "rejected":
		return true
	}
	return false
}
func validBrokerOrder(i Intent, r ExecutionRecord, o BrokerOrder, now time.Time) bool {
	if i.Order == nil || !idPattern.MatchString(o.OrderID) || o.AccountID != i.AccountID || o.ClientOrderID != r.ClientOrderID || o.Symbol != i.Symbol || o.Side != i.Order.Side || o.Type != i.Order.Type || o.TimeInForce != i.Order.TimeInForce || !sameDecimal(o.Qty, i.Order.Qty) || !sameDecimal(o.LimitPrice, i.Order.LimitPrice) {
		return false
	}
	if r.BrokerOrderID != "" && o.OrderID != r.BrokerOrderID {
		return false
	}
	updated, err := time.Parse(time.RFC3339Nano, o.UpdatedAt)
	if err != nil || updated.After(now) {
		return false
	}
	qty, err := observedDecimal(o.Qty)
	if err != nil {
		return false
	}
	filled, err := observedDecimal(o.FilledQty)
	if err != nil || filled.Cmp(qty) > 0 {
		return false
	}
	if filled.Sign() > 0 {
		if o.FilledAveragePrice == nil {
			return false
		}
		price, err := observedDecimal(*o.FilledAveragePrice)
		if err != nil || price.Sign() <= 0 {
			return false
		}
	} else if o.FilledAveragePrice != nil {
		return false
	}
	switch o.State {
	case "accepted":
		return filled.Sign() == 0
	case "partially_filled":
		return filled.Sign() > 0 && filled.Cmp(qty) < 0
	case "filled":
		return filled.Cmp(qty) == 0
	case "canceled", "expired":
		return filled.Cmp(qty) < 0
	case "rejected":
		return filled.Sign() == 0
	default:
		return false
	}
}

// observe atomically journals broker truth. Conflicting or incomplete evidence
// never releases a reservation or overwrites an attributable fill.
func (s *Store) observe(ctx context.Context, id string, o BrokerOrder, now time.Time) error {
	return s.immediate(ctx, func(c *sql.Conn) error {
		r, err := scanRecord(c.QueryRowContext(ctx, "SELECT "+recordColumns+" FROM learner_intents WHERE decision_id=? AND owner_id=? AND account_id=?", id, s.ownerID, s.accountID))
		if err != nil {
			return err
		}
		if r.State == "reserved" || r.State == "hold_recorded" || (r.State == "rejected" && r.BrokerOrderID == "") {
			return errors.New("order_not_submitted")
		}
		i, err := DecodeIntent(r.RawBody)
		if err != nil {
			return err
		}
		conflict := func() error {
			if r.State == "reconciliation_required" && r.ReasonCode == "broker_evidence_conflict" {
				return nil
			}
			qty, _ := decimal(i.Order.Qty, qtyPattern)
			limit, _ := decimal(i.Order.LimitPrice, pricePattern)
			fullReservation := exactDecimal(new(big.Rat).Mul(qty, limit))
			_, err := c.ExecContext(ctx, `UPDATE learner_intents SET state='reconciliation_required',reason_code='broker_evidence_conflict',reserved_notional=? WHERE decision_id=?`, fullReservation, id)
			if err != nil {
				return err
			}
			return appendEvent(ctx, c, id, "reconciliation_required", "broker_evidence_conflict", now)
		}
		if !validBrokerOrder(i, r, o, now) {
			return conflict()
		}
		evidence, err := json.Marshal(o)
		if err != nil || len(evidence) > MaxBodyBytes {
			return conflict()
		}
		h := fmt.Sprintf("%x", sha256.Sum256(evidence))
		updated, _ := time.Parse(time.RFC3339Nano, o.UpdatedAt)
		if r.BrokerUpdatedAt != "" {
			previous, err := time.Parse(time.RFC3339Nano, r.BrokerUpdatedAt)
			if err != nil {
				return errors.New("corrupt_broker_timestamp")
			}
			if updated.Before(previous) {
				return nil
			}
			if updated.Equal(previous) {
				if h == r.brokerHash {
					if r.State == "reconciliation_required" && r.ReasonCode == "broker_outcome_unknown" {
						_, err := c.ExecContext(ctx, `UPDATE learner_intents SET state=?,reason_code='' WHERE decision_id=?`, r.BrokerState, id)
						if err != nil {
							return err
						}
						return appendEvent(ctx, c, id, r.BrokerState, "", now)
					}
					return nil
				}
				return conflict()
			}
		}
		filled, _ := observedDecimal(o.FilledQty)
		previous, err := observedDecimal(r.FilledQty)
		if err != nil {
			return err
		}
		if filled.Cmp(previous) < 0 || (terminal(r.BrokerState) && o.State != r.BrokerState) {
			return conflict()
		}
		var notional *string
		if filled.Sign() > 0 {
			price, _ := observedDecimal(*o.FilledAveragePrice)
			value := exactDecimal(new(big.Rat).Mul(filled, price))
			notional = &value
			if r.FillNotional != nil {
				if !storedNotionalPattern.MatchString(*r.FillNotional) {
					return errors.New("corrupt_fill_notional")
				}
				prior, ok := new(big.Rat).SetString(*r.FillNotional)
				if !ok {
					return errors.New("corrupt_fill_notional")
				}
				current, _ := new(big.Rat).SetString(value)
				if current.Cmp(prior) < 0 || (filled.Cmp(previous) == 0 && current.Cmp(prior) != 0) {
					return conflict()
				}
			}
		}
		reservation := r.ReservedNotional
		if terminal(o.State) {
			limit, _ := decimal(i.Order.LimitPrice, pricePattern)
			reservation = exactDecimal(new(big.Rat).Mul(filled, limit))
		}
		_, err = c.ExecContext(ctx, `UPDATE learner_intents SET state=?,reason_code='',reserved_notional=?,broker_order_id=?,broker_state=?,broker_updated_at=?,broker_hash=?,filled_qty=?,fill_notional=? WHERE decision_id=?`, o.State, reservation, o.OrderID, o.State, o.UpdatedAt, h, exactDecimal(filled), notional, id)
		if err != nil {
			return err
		}
		_, err = c.ExecContext(ctx, `INSERT INTO learner_execution_events(decision_id,state,reason_code,observed_at,evidence) VALUES(?,?,'',?,?)`, id, o.State, now.UTC().Format(timestampLayout), evidence)
		return err
	})
}
