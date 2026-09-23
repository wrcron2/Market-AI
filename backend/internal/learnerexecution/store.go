package learnerexecution

import (
	"bytes"
	"context"
	"crypto/sha256"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"math/big"
	"time"
)

var (
	ErrConflict = errors.New("decision_id_body_conflict")
	ErrScope    = errors.New("intent_scope_mismatch")
	ErrNotFound = errors.New("intent_not_found")
)

// ExecutionRecord preserves the original signed bytes. Financial strings are
// exact rational values internally; no binary floating point enters accounting.
type ExecutionRecord struct {
	DecisionID       string          `json:"decisionId"`
	BodyHash         string          `json:"bodyHash"`
	RawBody          []byte          `json:"-"`
	ClientOrderID    string          `json:"clientOrderId"`
	State            string          `json:"state"`
	ReasonCode       string          `json:"reasonCode"`
	ReservedNotional string          `json:"reservedNotional"`
	BrokerOrderID    string          `json:"brokerOrderId"`
	BrokerState      string          `json:"brokerState"`
	BrokerUpdatedAt  string          `json:"brokerUpdatedAt"`
	FilledQty        string          `json:"filledQty"`
	FillNotional     *string         `json:"fillNotional"`
	Safety           json.RawMessage `json:"safety"`
	brokerHash       string
}

type ExecutionEvent struct {
	ID         int64           `json:"id"`
	State      string          `json:"state"`
	ReasonCode string          `json:"reasonCode"`
	ObservedAt string          `json:"observedAt"`
	Evidence   json.RawMessage `json:"evidence,omitempty"`
}

type Store struct {
	db                 *sql.DB
	ownerID, accountID string
}

func NewStore(db *sql.DB, ownerID, accountID string) (*Store, error) {
	if db == nil || !idPattern.MatchString(ownerID) || !idPattern.MatchString(accountID) {
		return nil, ErrScope
	}
	// Additive, package-owned schema. No legacy table is read or migrated.
	_, err := db.Exec(`CREATE TABLE IF NOT EXISTS learner_intents (
	 decision_id TEXT PRIMARY KEY,
	 owner_id TEXT NOT NULL, account_id TEXT NOT NULL,
	 body_hash TEXT NOT NULL, raw_body BLOB NOT NULL CHECK(length(raw_body)<=65536),
	 client_order_id TEXT NOT NULL UNIQUE,
	 state TEXT NOT NULL, reason_code TEXT NOT NULL,
	 reserved_notional TEXT NOT NULL, budget_day TEXT NOT NULL,
	 created_at TEXT NOT NULL,
	 broker_order_id TEXT NOT NULL DEFAULT '', broker_state TEXT NOT NULL DEFAULT '',
	 broker_updated_at TEXT NOT NULL DEFAULT '', broker_hash TEXT NOT NULL DEFAULT '',
	 filled_qty TEXT NOT NULL DEFAULT '0', fill_notional TEXT,
	 safety_result BLOB CHECK(length(safety_result)<=65536)
	);
	CREATE INDEX IF NOT EXISTS learner_intents_scope ON learner_intents(account_id,owner_id);
	CREATE UNIQUE INDEX IF NOT EXISTS learner_unique_broker_order ON learner_intents(account_id,broker_order_id) WHERE broker_order_id<>'';
	CREATE TABLE IF NOT EXISTS learner_execution_events (
	 id INTEGER PRIMARY KEY AUTOINCREMENT,
	 decision_id TEXT NOT NULL,
	 state TEXT NOT NULL, reason_code TEXT NOT NULL CHECK(length(reason_code)<=128),
	 observed_at TEXT NOT NULL, evidence BLOB CHECK(length(evidence)<=65536)
	);
	CREATE INDEX IF NOT EXISTS learner_events_decision ON learner_execution_events(decision_id,id);
	CREATE TABLE IF NOT EXISTS learner_observations (
	 id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, account_id TEXT NOT NULL,
	 snapshot BLOB NOT NULL CHECK(length(snapshot)<=65536)
	);`)
	if err != nil {
		return nil, err
	}
	return &Store{db: db, ownerID: ownerID, accountID: accountID}, nil
}

// immediate serializes the read-check-reserve operation across processes and
// independent DB handles. It never encloses broker/network work. An explicit
// connection avoids relying on a caller's sqlite DSN transaction-lock setting.
func (s *Store) immediate(ctx context.Context, fn func(*sql.Conn) error) error {
	c, err := s.db.Conn(ctx)
	if err != nil {
		return err
	}
	defer c.Close()
	if _, err = c.ExecContext(ctx, "BEGIN IMMEDIATE"); err != nil {
		return err
	}
	defer func() {
		cleanup, cancel := context.WithTimeout(context.Background(), 2*time.Second)
		defer cancel()
		_, _ = c.ExecContext(cleanup, "ROLLBACK")
	}()
	if err = fn(c); err != nil {
		return err
	}
	_, err = c.ExecContext(ctx, "COMMIT")
	return err
}

type rowScanner interface{ Scan(...any) error }

const recordColumns = "decision_id,body_hash,raw_body,client_order_id,state,reason_code,reserved_notional,broker_order_id,broker_state,broker_updated_at,broker_hash,filled_qty,fill_notional,safety_result"

func scanRecord(row rowScanner) (ExecutionRecord, error) {
	var r ExecutionRecord
	var safety []byte
	err := row.Scan(&r.DecisionID, &r.BodyHash, &r.RawBody, &r.ClientOrderID, &r.State, &r.ReasonCode, &r.ReservedNotional, &r.BrokerOrderID, &r.BrokerState, &r.BrokerUpdatedAt, &r.brokerHash, &r.FilledQty, &r.FillNotional, &safety)
	r.Safety = safety
	if errors.Is(err, sql.ErrNoRows) {
		err = ErrNotFound
	}
	return r, err
}
func (s *Store) Get(ctx context.Context, id string) (ExecutionRecord, error) {
	return scanRecord(s.db.QueryRowContext(ctx, "SELECT "+recordColumns+" FROM learner_intents WHERE decision_id=? AND owner_id=? AND account_id=?", id, s.ownerID, s.accountID))
}
func appendEvent(ctx context.Context, c *sql.Conn, id, state, reason string, now time.Time) error {
	_, err := c.ExecContext(ctx, `INSERT INTO learner_execution_events(decision_id,state,reason_code,observed_at) VALUES(?,?,?,?)`, id, state, reason, now.UTC().Format(timestampLayout))
	return err
}
func clientOrderID(accountID, decisionID string) string {
	// NUL cannot occur in either validated identifier; concatenation is unambiguous.
	h := sha256.Sum256([]byte(accountID + "\x00" + decisionID))
	return fmt.Sprintf("kl-%x", h[:20])
}

// reserve is an internal durability primitive, not permission to trade. The
// service must supply the mechanical-safety denial before attempting a claim.
// dailyCap bounds gross committed limit notional, including pending prior days.
func (s *Store) reserve(ctx context.Context, raw []byte, now time.Time, dailyCap, denial string, decisions ...SafetyDecision) (r ExecutionRecord, created bool, err error) {
	i, err := DecodeIntent(raw)
	if err != nil {
		return r, false, err
	}
	if i.OwnerID != s.ownerID || i.AccountID != s.accountID {
		return r, false, ErrScope
	}
	if len(denial) > 128 {
		return r, false, errors.New("invalid_reason_code")
	}
	h := sha256.Sum256(raw)
	err = s.immediate(ctx, func(c *sql.Conn) error {
		existing, e := scanRecord(c.QueryRowContext(ctx, "SELECT "+recordColumns+" FROM learner_intents WHERE decision_id=?", i.DecisionID))
		if e == nil {
			if !bytes.Equal(existing.RawBody, raw) {
				return ErrConflict
			}
			r = existing
			return nil
		}
		if !errors.Is(e, ErrNotFound) {
			return e
		}
		if len(decisions) > 0 && decisions[0].checkReservation != nil {
			checked, e := decisions[0].checkReservation(ctx, c)
			if e != nil {
				return e
			}
			decisions[0] = checked
			dailyCap = checked.DailyLimit
			denial = ""
			if !checked.Allowed {
				denial = checked.ReasonCode
				if denial == "" {
					denial = "mechanical_safety_rejected"
				}
			}
		}
		r = ExecutionRecord{DecisionID: i.DecisionID, BodyHash: fmt.Sprintf("%x", h), RawBody: bytes.Clone(raw), ClientOrderID: clientOrderID(i.AccountID, i.DecisionID), State: "rejected", ReasonCode: denial, ReservedNotional: "0", FilledQty: "0"}
		day := now.UTC().Format("2006-01-02")
		switch {
		case i.ValidateFresh(now) != nil:
			r.ReasonCode = "intent_expired_or_future"
		case denial != "": // The mechanical rejection is preserved verbatim.
		case i.Action == "HOLD":
			r.State = "hold_recorded"
		default:
			cap, e := decimal(dailyCap, pricePattern)
			if e != nil {
				r.ReasonCode = "limits_not_configured"
				break
			}
			qty, _ := decimal(i.Order.Qty, qtyPattern)
			price, _ := decimal(i.Order.LimitPrice, pricePattern)
			amount := new(big.Rat).Mul(qty, price)
			used, e := s.committedBudget(ctx, c, day)
			if e != nil {
				return e
			}
			if len(decisions) > 0 {
				decisions[0].measure("dailyCommittedNotionalBefore", exactDecimal(used))
				decisions[0].measure("dailyProjectedNotional", exactDecimal(new(big.Rat).Add(used, amount)))
			}
			if new(big.Rat).Add(used, amount).Cmp(cap) > 0 {
				r.ReasonCode = "daily_cap_exceeded"
				break
			}
			r.State = "reserved"
			r.ReservedNotional = exactDecimal(amount)
		}
		_, e = c.ExecContext(ctx, `INSERT INTO learner_intents(decision_id,owner_id,account_id,body_hash,raw_body,client_order_id,state,reason_code,reserved_notional,budget_day,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)`, r.DecisionID, s.ownerID, s.accountID, r.BodyHash, r.RawBody, r.ClientOrderID, r.State, r.ReasonCode, r.ReservedNotional, day, now.UTC().Format(timestampLayout))
		if e != nil {
			return e
		}
		if len(decisions) > 0 {
			decision := decisions[0]
			if r.State == "rejected" {
				decision.Allowed = false
				decision.ReasonCode = r.ReasonCode
			}
			r.Safety, e = json.Marshal(decision)
			if e != nil {
				return e
			}
			if _, e = c.ExecContext(ctx, `UPDATE learner_intents SET safety_result=? WHERE decision_id=?`, []byte(r.Safety), r.DecisionID); e != nil {
				return e
			}
		}
		if e = appendEvent(ctx, c, r.DecisionID, "validated", "", now); e != nil {
			return e
		}
		if e = appendEvent(ctx, c, r.DecisionID, r.State, r.ReasonCode, now); e != nil {
			return e
		}
		created = true
		return nil
	})
	if err != nil {
		return ExecutionRecord{}, false, err
	}
	return r, created, nil
}

// Used only before broker submission. It cannot overwrite any durable submit
// claim, so a competing request cannot reject an order already being sent.
func (s *Store) rejectUnsent(ctx context.Context, id, reason string, now time.Time) error {
	return s.immediate(ctx, func(c *sql.Conn) error {
		r, err := scanRecord(c.QueryRowContext(ctx, "SELECT "+recordColumns+" FROM learner_intents WHERE decision_id=? AND owner_id=? AND account_id=?", id, s.ownerID, s.accountID))
		if err != nil {
			return err
		}
		if r.State != "reserved" {
			return nil
		}
		var safety SafetyDecision
		if len(r.Safety) > 0 {
			if err = json.Unmarshal(r.Safety, &safety); err != nil {
				return err
			}
		}
		safety.Allowed = false
		safety.ReasonCode = reason
		safety.ValidUntil = nil
		encoded, err := json.Marshal(safety)
		if err != nil {
			return err
		}
		result, err := c.ExecContext(ctx, `UPDATE learner_intents SET state='rejected',reason_code=?,reserved_notional='0',safety_result=? WHERE decision_id=? AND owner_id=? AND account_id=? AND state='reserved'`, reason, encoded, id, s.ownerID, s.accountID)
		if err != nil {
			return err
		}
		n, err := result.RowsAffected()
		if err != nil || n == 0 {
			return err
		}
		return appendEvent(ctx, c, id, "rejected", reason, now)
	})
}

// View reads state and the latest event window in one transaction, so a
// concurrent reconciliation cannot attach newer evidence to an older state.
func (s *Store) View(ctx context.Context, id string) (view ExecutionView, err error) {
	err = s.immediate(ctx, func(c *sql.Conn) error {
		var e error
		view.Execution, e = scanRecord(c.QueryRowContext(ctx, "SELECT "+recordColumns+" FROM learner_intents WHERE decision_id=? AND owner_id=? AND account_id=?", id, s.ownerID, s.accountID))
		if e != nil {
			return e
		}
		view.Intent, e = DecodeIntent(view.Execution.RawBody)
		if e != nil {
			return e
		}
		rows, e := c.QueryContext(ctx, `SELECT id,state,reason_code,observed_at,evidence FROM learner_execution_events WHERE decision_id=? ORDER BY id DESC LIMIT 100`, id)
		if e != nil {
			return e
		}
		defer rows.Close()
		view.Events = []ExecutionEvent{}
		for rows.Next() {
			var event ExecutionEvent
			var evidence []byte
			if e = rows.Scan(&event.ID, &event.State, &event.ReasonCode, &event.ObservedAt, &evidence); e != nil {
				return e
			}
			event.Evidence = evidence
			view.Events = append(view.Events, event)
		}
		for left, right := 0, len(view.Events)-1; left < right; left, right = left+1, right-1 {
			view.Events[left], view.Events[right] = view.Events[right], view.Events[left]
		}
		return rows.Err()
	})
	return view, err
}

// Only the successful caller may issue a single submit. A crash after this
// commit, even before the network call, is resolved by lookup, never by replay.
func (s *Store) claim(ctx context.Context, id string, now time.Time, decisions ...SafetyDecision) (won bool, err error) {
	err = s.immediate(ctx, func(c *sql.Conn) error {
		r, e := scanRecord(c.QueryRowContext(ctx, "SELECT "+recordColumns+" FROM learner_intents WHERE decision_id=? AND owner_id=? AND account_id=?", id, s.ownerID, s.accountID))
		if e != nil {
			return e
		}
		if r.State != "reserved" {
			return nil
		}
		i, e := DecodeIntent(r.RawBody)
		if e != nil {
			return e
		}
		reject := func(reason string) error {
			decision := SafetyDecision{PolicyVersion: i.PolicyVersion, ReasonCode: reason}
			if len(r.Safety) > 0 {
				if e := json.Unmarshal(r.Safety, &decision); e != nil {
					return e
				}
			}
			decision.Allowed = false
			decision.ReasonCode = reason
			raw, e := json.Marshal(decision)
			if e != nil {
				return e
			}
			if _, e = c.ExecContext(ctx, `UPDATE learner_intents SET state='rejected',reason_code=?,reserved_notional='0',safety_result=? WHERE decision_id=?`, reason, raw, id); e != nil {
				return e
			}
			return appendEvent(ctx, c, id, "rejected", reason, now)
		}
		if i.ValidateFresh(now) != nil {
			return reject("intent_expired_or_future")
		}
		if len(decisions) > 0 && decisions[0].checkReservation != nil {
			checked, e := decisions[0].checkReservation(ctx, c)
			if e != nil {
				return e
			}
			decisions[0] = checked
			raw, e := json.Marshal(checked)
			if e != nil {
				return e
			}
			r.Safety = raw
			if !checked.Allowed {
				return reject(checked.ReasonCode)
			}
			if _, e = c.ExecContext(ctx, `UPDATE learner_intents SET safety_result=? WHERE decision_id=?`, raw, id); e != nil {
				return e
			}
		}
		if len(decisions) > 0 {
			cap, e := decimal(decisions[0].DailyLimit, pricePattern)
			if e != nil {
				return reject("limits_not_configured")
			}
			used, e := s.committedBudget(ctx, c, now.UTC().Format("2006-01-02"))
			if e != nil {
				return e
			}
			measured := decisions[0]
			if len(r.Safety) > 0 {
				if e = json.Unmarshal(r.Safety, &measured); e != nil {
					return e
				}
			}
			measured.DailyLimit = decisions[0].DailyLimit
			measured.measure("dailyCommittedNotional", exactDecimal(used))
			r.Safety, e = json.Marshal(measured)
			if e != nil {
				return e
			}
			if _, e = c.ExecContext(ctx, `UPDATE learner_intents SET safety_result=? WHERE decision_id=?`, []byte(r.Safety), id); e != nil {
				return e
			}
			if used.Cmp(cap) > 0 {
				return reject("daily_cap_exceeded")
			}
		}
		result, e := c.ExecContext(ctx, `UPDATE learner_intents SET state='submitting' WHERE decision_id=? AND owner_id=? AND account_id=? AND state='reserved'`, id, s.ownerID, s.accountID)
		if e != nil {
			return e
		}
		n, e := result.RowsAffected()
		if e != nil {
			return e
		}
		if n == 0 {
			return nil
		}
		won = true
		return appendEvent(ctx, c, id, "submitting", "", now)
	})
	return won && err == nil, err
}
func (s *Store) uncertain(ctx context.Context, id string, now time.Time) error {
	return s.immediate(ctx, func(c *sql.Conn) error {
		result, err := c.ExecContext(ctx, `UPDATE learner_intents SET state='reconciliation_required',reason_code='broker_outcome_unknown' WHERE decision_id=? AND owner_id=? AND account_id=? AND state IN ('submitting','accepted','partially_filled')`, id, s.ownerID, s.accountID)
		if err != nil {
			return err
		}
		n, err := result.RowsAffected()
		if err != nil || n == 0 {
			return err
		}
		return appendEvent(ctx, c, id, "reconciliation_required", "broker_outcome_unknown", now)
	})
}
func (s *Store) Events(ctx context.Context, id string, after int64, limit int) ([]ExecutionEvent, error) {
	if after < 0 || limit < 1 || limit > 100 {
		return nil, errors.New("invalid_event_page")
	}
	if _, err := s.Get(ctx, id); err != nil {
		return nil, err
	}
	rows, err := s.db.QueryContext(ctx, `SELECT id,state,reason_code,observed_at,evidence FROM learner_execution_events WHERE decision_id=? AND id>? ORDER BY id LIMIT ?`, id, after, limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	result := []ExecutionEvent{}
	for rows.Next() {
		var e ExecutionEvent
		var evidence []byte
		if err := rows.Scan(&e.ID, &e.State, &e.ReasonCode, &e.ObservedAt, &evidence); err != nil {
			return nil, err
		}
		e.Evidence = evidence
		result = append(result, e)
	}
	return result, rows.Err()
}
