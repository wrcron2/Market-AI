// Package db manages the staged orders database.
// ALL orders are stored here first. No IBKR execution happens until
// the trader issues a Green Light from the dashboard.
package db

import (
	"database/sql"
	"fmt"
	"time"

	_ "github.com/mattn/go-sqlite3"
)

// DB wraps the standard sql.DB with MarketFlow-specific methods.
type DB struct {
	*sql.DB
}

// Open initialises the database connection and returns a *DB.
func Open(dsn string) (*DB, error) {
	sqlDB, err := sql.Open("sqlite3", dsn+"?_journal_mode=WAL&_foreign_keys=on")
	if err != nil {
		return nil, fmt.Errorf("sql.Open: %w", err)
	}
	if err := sqlDB.Ping(); err != nil {
		return nil, fmt.Errorf("db.Ping: %w", err)
	}
	return &DB{sqlDB}, nil
}

// Migrate runs the embedded SQL schema (idempotent).
func (d *DB) Migrate() error {
	_, err := d.Exec(schema)
	return err
}

// ─── Staged Orders ────────────────────────────────────────────────────────────

type OrderStatus string

const (
	StatusPending  OrderStatus = "PENDING"
	StatusApproved OrderStatus = "APPROVED"
	StatusRejected OrderStatus = "REJECTED"
	StatusExecuted OrderStatus = "EXECUTED"
	StatusFailed   OrderStatus = "FAILED"
)

// StagedOrder mirrors the staged_orders table.
type StagedOrder struct {
	ID            string      `json:"id"`
	Symbol        string      `json:"symbol"`
	Direction     string      `json:"direction"`
	Quantity      float64     `json:"quantity"`
	LimitPrice    float64     `json:"limit_price"`
	Confidence    float64     `json:"confidence"`
	Reasoning     string      `json:"reasoning"`
	StrategyName  string      `json:"strategy_name"`
	ModelUsed     string      `json:"model_used"`
	Status        OrderStatus `json:"status"`
	TraderComment string      `json:"trader_comment,omitempty"`
	IBKROrderID   *int64      `json:"ibkr_order_id,omitempty"`
	CreatedAt     int64       `json:"created_at"`
	UpdatedAt     int64       `json:"updated_at"`
}

// StageOrder inserts a new order with status PENDING.
// This is the ONLY entry point for new orders.
func (d *DB) StageOrder(o *StagedOrder) error {
	now := time.Now().UnixMilli()
	o.CreatedAt = now
	o.UpdatedAt = now
	o.Status = StatusPending

	_, err := d.Exec(`
		INSERT INTO staged_orders
			(id, symbol, direction, quantity, limit_price, confidence,
			 reasoning, strategy_name, model_used, status, created_at, updated_at)
		VALUES (?,?,?,?,?,?,?,?,?,?,?,?)`,
		o.ID, o.Symbol, o.Direction, o.Quantity, o.LimitPrice, o.Confidence,
		o.Reasoning, o.StrategyName, o.ModelUsed, string(o.Status), o.CreatedAt, o.UpdatedAt,
	)
	if err != nil {
		return fmt.Errorf("StageOrder: %w", err)
	}

	return d.appendAuditLog(o.ID, "", string(StatusPending), "system", "Order staged by AI brain")
}

// GetOrder fetches a single order by ID.
func (d *DB) GetOrder(id string) (*StagedOrder, error) {
	row := d.QueryRow(`
		SELECT id, symbol, direction, quantity, limit_price, confidence,
		       reasoning, strategy_name, model_used, status,
		       COALESCE(trader_comment,''), ibkr_order_id, created_at, updated_at
		FROM staged_orders WHERE id = ?`, id)
	return scanOrder(row)
}

// ListByStatus returns orders filtered by status, newest first.
func (d *DB) ListByStatus(status OrderStatus, limit, offset int) ([]*StagedOrder, int, error) {
	if limit <= 0 {
		limit = 50
	}
	rows, err := d.Query(`
		SELECT id, symbol, direction, quantity, limit_price, confidence,
		       reasoning, strategy_name, model_used, status,
		       COALESCE(trader_comment,''), ibkr_order_id, created_at, updated_at
		FROM staged_orders
		WHERE status = ?
		ORDER BY created_at DESC
		LIMIT ? OFFSET ?`, string(status), limit, offset)
	if err != nil {
		return nil, 0, err
	}
	defer rows.Close()

	var orders []*StagedOrder
	for rows.Next() {
		o, err := scanOrder(rows)
		if err != nil {
			return nil, 0, err
		}
		orders = append(orders, o)
	}

	var total int
	_ = d.QueryRow(`SELECT COUNT(*) FROM staged_orders WHERE status = ?`, string(status)).Scan(&total)
	return orders, total, nil
}

// TransitionStatus updates the status of an order atomically.
// Enforces valid transitions to prevent accidental state corruption.
func (d *DB) TransitionStatus(id string, to OrderStatus, actor, comment string) error {
	order, err := d.GetOrder(id)
	if err != nil {
		return fmt.Errorf("order not found: %w", err)
	}

	if !validTransition(order.Status, to) {
		return fmt.Errorf("invalid transition %s → %s", order.Status, to)
	}

	now := time.Now().UnixMilli()
	_, err = d.Exec(`
		UPDATE staged_orders
		SET status = ?, trader_comment = ?, updated_at = ?
		WHERE id = ?`, string(to), comment, now, id)
	if err != nil {
		return fmt.Errorf("TransitionStatus: %w", err)
	}

	return d.appendAuditLog(id, string(order.Status), string(to), actor, comment)
}

// SetIBKROrderID records the order ID returned by IBKR after execution.
func (d *DB) SetIBKROrderID(signalID string, ibkrOrderID int64) error {
	_, err := d.Exec(
		`UPDATE staged_orders SET ibkr_order_id = ?, updated_at = ? WHERE id = ?`,
		ibkrOrderID, time.Now().UnixMilli(), signalID,
	)
	return err
}

// ─── Audit Log ────────────────────────────────────────────────────────────────

func (d *DB) appendAuditLog(signalID, from, to, actor, message string) error {
	_, err := d.Exec(`
		INSERT INTO order_audit_log (signal_id, from_status, to_status, actor, message, timestamp)
		VALUES (?,?,?,?,?,?)`,
		signalID, from, to, actor, message, time.Now().UnixMilli(),
	)
	return err
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

type scanner interface {
	Scan(dest ...any) error
}

func scanOrder(s scanner) (*StagedOrder, error) {
	o := &StagedOrder{}
	return o, s.Scan(
		&o.ID, &o.Symbol, &o.Direction, &o.Quantity, &o.LimitPrice, &o.Confidence,
		&o.Reasoning, &o.StrategyName, &o.ModelUsed, &o.Status,
		&o.TraderComment, &o.IBKROrderID, &o.CreatedAt, &o.UpdatedAt,
	)
}

func validTransition(from, to OrderStatus) bool {
	allowed := map[OrderStatus][]OrderStatus{
		StatusPending:  {StatusApproved, StatusRejected},
		StatusApproved: {StatusExecuted, StatusFailed},
	}
	for _, next := range allowed[from] {
		if next == to {
			return true
		}
	}
	return false
}

// ─── Embedded Schema ──────────────────────────────────────────────────────────

const schema = `
CREATE TABLE IF NOT EXISTS staged_orders (
    id             TEXT PRIMARY KEY,
    symbol         TEXT NOT NULL,
    direction      TEXT NOT NULL CHECK (direction IN ('BUY','SELL','SHORT','COVER')),
    quantity       REAL NOT NULL CHECK (quantity > 0),
    limit_price    REAL NOT NULL DEFAULT 0,
    confidence     REAL NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
    reasoning      TEXT NOT NULL DEFAULT '',
    strategy_name  TEXT NOT NULL DEFAULT '',
    model_used     TEXT NOT NULL DEFAULT '',
    status         TEXT NOT NULL DEFAULT 'PENDING'
                       CHECK (status IN ('PENDING','APPROVED','REJECTED','EXECUTED','FAILED')),
    trader_comment TEXT,
    ibkr_order_id  INTEGER,
    created_at     INTEGER NOT NULL,
    updated_at     INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_staged_orders_status_created
    ON staged_orders (status, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_staged_orders_symbol
    ON staged_orders (symbol);

CREATE TABLE IF NOT EXISTS order_audit_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id   TEXT NOT NULL REFERENCES staged_orders(id),
    from_status TEXT,
    to_status   TEXT NOT NULL,
    actor       TEXT NOT NULL DEFAULT 'system',
    message     TEXT,
    timestamp   INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_audit_signal
    ON order_audit_log (signal_id, timestamp DESC);
`
