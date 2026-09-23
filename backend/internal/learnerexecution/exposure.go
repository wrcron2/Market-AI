package learnerexecution

import (
	"context"
	"crypto/sha256"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"math/big"
)

func (s *Store) saveObservation(ctx context.Context, o BrokerSnapshot) (BrokerSnapshot, error) {
	if o.AccountID != s.accountID {
		return BrokerSnapshot{}, ErrScope
	}
	if !o.Complete || !o.Paper || !symbolPattern.MatchString(o.Symbol) {
		return BrokerSnapshot{}, errors.New("observation_incomplete")
	}
	o.ID = ""
	raw, err := json.Marshal(o)
	if err != nil {
		return BrokerSnapshot{}, err
	}
	h := sha256.Sum256(append([]byte(s.ownerID+"\x00"), raw...))
	o.ID = fmt.Sprintf("obs-%x", h[:20])
	raw, err = json.Marshal(o)
	if err != nil || len(raw) > MaxBodyBytes {
		return BrokerSnapshot{}, errors.New("observation_too_large")
	}
	_, err = s.db.ExecContext(ctx, `INSERT INTO learner_observations(id,owner_id,account_id,snapshot) VALUES(?,?,?,?) ON CONFLICT(id) DO NOTHING`, o.ID, s.ownerID, s.accountID, raw)
	if err != nil {
		return BrokerSnapshot{}, err
	}
	return o, nil
}
func (s *Store) observation(ctx context.Context, id string) (BrokerSnapshot, error) {
	var raw []byte
	var o BrokerSnapshot
	err := s.db.QueryRowContext(ctx, `SELECT snapshot FROM learner_observations WHERE id=? AND owner_id=? AND account_id=?`, id, s.ownerID, s.accountID).Scan(&raw)
	if errors.Is(err, sql.ErrNoRows) {
		return o, ErrNotFound
	}
	if err != nil {
		return o, err
	}
	if err = json.Unmarshal(raw, &o); err != nil || o.ID != id || o.AccountID != s.accountID || !o.Complete {
		return BrokerSnapshot{}, errors.New("stored_observation_invalid")
	}
	return o, nil
}

func (s *Store) committedBudget(ctx context.Context, c *sql.Conn, day string) (*big.Rat, error) {
	used := new(big.Rat)
	rows, err := c.QueryContext(ctx, `SELECT reserved_notional FROM learner_intents WHERE account_id=? AND (budget_day=? OR state IN ('reserved','submitting','accepted','partially_filled','reconciliation_required'))`, s.accountID, day)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	for rows.Next() {
		var text string
		if err := rows.Scan(&text); err != nil {
			return nil, err
		}
		n, ok := accountingAmount(text)
		if !ok {
			return nil, errors.New("corrupt_reservation")
		}
		used.Add(used, n)
	}
	return used, rows.Err()
}

// exposure runs on the caller's immediate transaction, so policy checks and
// the next reservation see one serialized journal state. No network I/O here.
func (s *Store) exposure(ctx context.Context, c *sql.Conn, excludeID string) (Exposure, error) {
	e := Exposure{Owned: map[string]string{}, PendingBuy: map[string]string{}, PendingSell: map[string]string{}, KnownOrders: map[string]JournalOrder{}, TotalPendingBuy: "0"}
	rows, err := c.QueryContext(ctx, "SELECT "+recordColumns+" FROM learner_intents WHERE account_id=?", s.accountID)
	if err != nil {
		return e, err
	}
	defer rows.Close()
	owned := map[string]*big.Rat{}
	buys := map[string]*big.Rat{}
	sells := map[string]*big.Rat{}
	total := new(big.Rat)
	add := func(values map[string]*big.Rat, symbol string, n *big.Rat) {
		if values[symbol] == nil {
			values[symbol] = new(big.Rat)
		}
		values[symbol].Add(values[symbol], n)
	}
	for rows.Next() {
		r, err := scanRecord(rows)
		if err != nil {
			return e, err
		}
		i, err := DecodeIntent(r.RawBody)
		if err != nil {
			return e, err
		}
		if i.Order == nil {
			continue
		}
		filled, err := observedDecimal(r.FilledQty)
		if err != nil {
			return e, err
		}
		pending := r.State == "reserved" || r.State == "submitting" || r.State == "accepted" || r.State == "partially_filled" || r.State == "reconciliation_required"
		if i.OwnerID != s.ownerID {
			if pending || filled.Sign() > 0 {
				e.Uncertain = true
			}
			continue
		}
		net := new(big.Rat).Set(filled)
		if i.Order.Side == "sell" {
			net.Neg(net)
		}
		add(owned, i.Symbol, net)
		if r.DecisionID == excludeID {
			continue
		}
		if r.State == "submitting" || r.State == "reconciliation_required" {
			e.Uncertain = true
		}
		if !pending {
			continue
		}
		if r.BrokerOrderID != "" {
			e.KnownOrders[r.ClientOrderID] = JournalOrder{Intent: i, Record: r}
		}
		qty, _ := decimal(i.Order.Qty, qtyPattern)
		remaining := new(big.Rat).Sub(qty, filled)
		if remaining.Sign() < 0 {
			return e, errors.New("overfilled_journal")
		}
		if i.Order.Side == "buy" {
			price, _ := decimal(i.Order.LimitPrice, pricePattern)
			value := new(big.Rat).Mul(remaining, price)
			add(buys, i.Symbol, value)
			total.Add(total, value)
		} else {
			add(sells, i.Symbol, remaining)
		}
	}
	if err = rows.Err(); err != nil {
		return e, err
	}
	for symbol, n := range owned {
		if n.Sign() < 0 {
			e.Uncertain = true
		}
		e.Owned[symbol] = exactDecimal(n)
	}
	for symbol, n := range buys {
		e.PendingBuy[symbol] = exactDecimal(n)
	}
	for symbol, n := range sells {
		e.PendingSell[symbol] = exactDecimal(n)
	}
	e.TotalPendingBuy = exactDecimal(total)
	return e, nil
}
