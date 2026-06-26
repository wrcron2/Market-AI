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

func nowMs() int64 { return time.Now().UnixMilli() }

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

// ListRecentOrders returns the most recent N orders across all statuses, newest first.
func (d *DB) ListRecentOrders(limit int) ([]*StagedOrder, error) {
	if limit <= 0 {
		limit = 100
	}
	rows, err := d.Query(`
		SELECT id, symbol, direction, quantity, limit_price, confidence,
		       reasoning, strategy_name, model_used, status,
		       COALESCE(trader_comment,''), ibkr_order_id, created_at, updated_at
		FROM staged_orders
		ORDER BY created_at DESC
		LIMIT ?`, limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []*StagedOrder
	for rows.Next() {
		o, err := scanOrder(rows)
		if err != nil {
			return nil, err
		}
		out = append(out, o)
	}
	return out, nil
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

// ExpirePendingSignals transitions all PENDING signals to EXPIRED at market close.
// Returns the number of signals expired.
func (d *DB) ExpirePendingSignals(actor, message string) (int, error) {
	rows, err := d.Query(`SELECT id FROM staged_orders WHERE status = 'PENDING'`)
	if err != nil {
		return 0, err
	}
	defer rows.Close()

	var ids []string
	for rows.Next() {
		var id string
		if err := rows.Scan(&id); err == nil {
			ids = append(ids, id)
		}
	}

	count := 0
	for _, id := range ids {
		_, err := d.Exec(
			`UPDATE staged_orders SET status = 'EXPIRED', updated_at = ? WHERE id = ?`,
			time.Now().UnixMilli(), id,
		)
		if err != nil {
			continue
		}
		_ = d.appendAuditLog(id, "PENDING", "EXPIRED", actor, message)
		count++
	}
	return count, nil
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

// AuditLogEntry mirrors a row from order_audit_log.
type AuditLogEntry struct {
	ID         int64  `json:"id"`
	SignalID   string `json:"signal_id"`
	FromStatus string `json:"from_status"`
	ToStatus   string `json:"to_status"`
	Actor      string `json:"actor"`
	Message    string `json:"message"`
	Timestamp  int64  `json:"timestamp"`
}

// ListAuditLog returns the most recent N audit log entries across all orders.
func (d *DB) ListAuditLog(limit int) ([]*AuditLogEntry, error) {
	if limit <= 0 {
		limit = 200
	}
	rows, err := d.Query(`
		SELECT id, signal_id, COALESCE(from_status,''), to_status,
		       actor, COALESCE(message,''), timestamp
		FROM order_audit_log
		ORDER BY timestamp DESC
		LIMIT ?`, limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []*AuditLogEntry
	for rows.Next() {
		e := &AuditLogEntry{}
		if err := rows.Scan(&e.ID, &e.SignalID, &e.FromStatus, &e.ToStatus,
			&e.Actor, &e.Message, &e.Timestamp); err != nil {
			return nil, err
		}
		out = append(out, e)
	}
	return out, nil
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

// ─── Stats ────────────────────────────────────────────────────────────────────

type OrderStats struct {
	TotalSignals  int     `json:"totalSignals"`
	Approved      int     `json:"approved"`
	Rejected      int     `json:"rejected"`
	Executed      int     `json:"executed"`
	AvgConfidence float64 `json:"avgConfidence"`
}

// GetStats returns aggregate counts from the staged_orders table.
func (d *DB) GetStats() (*OrderStats, error) {
	row := d.QueryRow(`
		SELECT
			COUNT(*),
			SUM(CASE WHEN status IN ('APPROVED','EXECUTED') THEN 1 ELSE 0 END),
			SUM(CASE WHEN status = 'REJECTED' THEN 1 ELSE 0 END),
			SUM(CASE WHEN status = 'EXECUTED' THEN 1 ELSE 0 END),
			COALESCE(AVG(confidence), 0)
		FROM staged_orders`)
	var s OrderStats
	return &s, row.Scan(&s.TotalSignals, &s.Approved, &s.Rejected, &s.Executed, &s.AvgConfidence)
}

// ─── Signal Outcomes ──────────────────────────────────────────────────────────

type SignalOutcome struct {
	SignalID           string   `json:"signal_id"`
	Symbol             string   `json:"symbol"`
	PredictedDirection string   `json:"predicted_direction"`
	StrategyName       string   `json:"strategy_name"`
	Confidence         float64  `json:"confidence"`
	VIXAtSignal        float64  `json:"vix_at_signal"`
	EntryPrice         float64  `json:"entry_price"`
	Check5dAt          int64    `json:"check_5d_at"`
	Check20dAt         int64    `json:"check_20d_at"`
	Price5d            *float64 `json:"price_5d"`
	Return5d           *float64 `json:"return_5d"`
	Outcome5d          *string  `json:"outcome_5d"`
	Price20d           *float64 `json:"price_20d"`
	Return20d          *float64 `json:"return_20d"`
	Outcome20d         *string  `json:"outcome_20d"`
	CreatedAt          int64    `json:"created_at"`
}

func (d *DB) CreateSignalOutcome(o SignalOutcome) error {
	now := nowMs()
	fiveDays := int64(5 * 24 * 60 * 60 * 1000)
	twentyDays := int64(20 * 24 * 60 * 60 * 1000)
	_, err := d.Exec(`
		INSERT OR IGNORE INTO signal_outcomes
			(signal_id, symbol, predicted_direction, strategy_name, confidence,
			 vix_at_signal, entry_price, check_5d_at, check_20d_at, created_at)
		VALUES (?,?,?,?,?,?,?,?,?,?)`,
		o.SignalID, o.Symbol, o.PredictedDirection, o.StrategyName, o.Confidence,
		o.VIXAtSignal, o.EntryPrice, now+fiveDays, now+twentyDays, now,
	)
	return err
}

func (d *DB) UpdateOutcome5d(signalID string, price, ret float64, outcome string) error {
	_, err := d.Exec(`
		UPDATE signal_outcomes
		SET price_5d=?, return_5d=?, outcome_5d=?, checked_5d_at=?
		WHERE signal_id=?`,
		price, ret, outcome, nowMs(), signalID,
	)
	return err
}

func (d *DB) UpdateOutcome20d(signalID string, price, ret float64, outcome string) error {
	_, err := d.Exec(`
		UPDATE signal_outcomes
		SET price_20d=?, return_20d=?, outcome_20d=?, checked_20d_at=?
		WHERE signal_id=?`,
		price, ret, outcome, nowMs(), signalID,
	)
	return err
}

type OutcomeStats struct {
	Total5d        int     `json:"total_5d"`
	TruePositive5d int     `json:"true_positive_5d"`
	FalsePositive5d int    `json:"false_positive_5d"`
	Accuracy5d     float64 `json:"accuracy_5d"`
	AvgReturn5d    float64 `json:"avg_return_5d"`
	Total20d       int     `json:"total_20d"`
	TruePositive20d int    `json:"true_positive_20d"`
	Accuracy20d    float64 `json:"accuracy_20d"`
	AvgReturn20d   float64 `json:"avg_return_20d"`
}

func (d *DB) GetOutcomeStats() (*OutcomeStats, error) {
	s := &OutcomeStats{}
	row := d.QueryRow(`
		SELECT
			COUNT(outcome_5d),
			SUM(CASE WHEN outcome_5d='TRUE_POSITIVE' THEN 1 ELSE 0 END),
			SUM(CASE WHEN outcome_5d='FALSE_POSITIVE' THEN 1 ELSE 0 END),
			COALESCE(AVG(return_5d), 0),
			COUNT(outcome_20d),
			SUM(CASE WHEN outcome_20d='TRUE_POSITIVE' THEN 1 ELSE 0 END),
			COALESCE(AVG(return_20d), 0)
		FROM signal_outcomes WHERE outcome_5d IS NOT NULL OR outcome_20d IS NOT NULL`)
	err := row.Scan(
		&s.Total5d, &s.TruePositive5d, &s.FalsePositive5d, &s.AvgReturn5d,
		&s.Total20d, &s.TruePositive20d, &s.AvgReturn20d,
	)
	if s.Total5d > 0 {
		s.Accuracy5d = float64(s.TruePositive5d) / float64(s.Total5d) * 100
	}
	if s.Total20d > 0 {
		s.Accuracy20d = float64(s.TruePositive20d) / float64(s.Total20d) * 100
	}
	return s, err
}

func (d *DB) GetPendingOutcomeChecks(nowMs int64) ([]SignalOutcome, error) {
	rows, err := d.Query(`
		SELECT signal_id, symbol, predicted_direction, strategy_name,
		       confidence, vix_at_signal, entry_price, check_5d_at, check_20d_at
		FROM signal_outcomes
		WHERE (outcome_5d IS NULL AND check_5d_at <= ?)
		   OR (outcome_20d IS NULL AND check_20d_at <= ?)`,
		nowMs, nowMs,
	)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []SignalOutcome
	for rows.Next() {
		var o SignalOutcome
		if err := rows.Scan(&o.SignalID, &o.Symbol, &o.PredictedDirection, &o.StrategyName,
			&o.Confidence, &o.VIXAtSignal, &o.EntryPrice, &o.Check5dAt, &o.Check20dAt); err != nil {
			continue
		}
		out = append(out, o)
	}
	return out, nil
}

// ─── Positions ────────────────────────────────────────────────────────────────

type PositionStatus string

const (
	PositionOpen   PositionStatus = "OPEN"
	PositionClosed PositionStatus = "CLOSED"
)

// Position mirrors the positions table.
type Position struct {
	ID              string         `json:"id"`
	Symbol          string         `json:"symbol"`
	Direction       string         `json:"direction"`     // LONG | SHORT
	Quantity        float64        `json:"quantity"`
	EntryPrice      float64        `json:"entry_price"`
	EntryTime       int64          `json:"entry_time"`
	Confidence      float64        `json:"confidence"`
	AlpacaOrderID   string         `json:"alpaca_order_id,omitempty"`
	Status          PositionStatus `json:"status"`
	ExitPrice       *float64       `json:"exit_price,omitempty"`
	ExitTime        *int64         `json:"exit_time,omitempty"`
	RealizedPnl     *float64       `json:"realized_pnl,omitempty"`
	StopLossPrice   *float64       `json:"stop_loss_price,omitempty"`
	TakeProfitPrice *float64       `json:"take_profit_price,omitempty"`
	CloseReason     string         `json:"close_reason,omitempty"`
	CreatedAt       int64          `json:"created_at"`
	UpdatedAt       int64          `json:"updated_at"`
}

// TradingLimits mirrors the trading_limits table (one row per calendar day).
type TradingLimits struct {
	Date        string  `json:"date"`
	RealizedPnl float64 `json:"realized_pnl"`
	TradeCount  int     `json:"trade_count"`
	IsHalted    bool    `json:"is_halted"`
}

// OpenPosition inserts a new open position record.
func (d *DB) OpenPosition(p *Position) error {
	now := time.Now().UnixMilli()
	p.CreatedAt = now
	p.UpdatedAt = now
	p.Status = PositionOpen
	_, err := d.Exec(`
		INSERT INTO positions
			(id, symbol, direction, quantity, entry_price, entry_time, confidence,
			 alpaca_order_id, status, stop_loss_price, take_profit_price, created_at, updated_at)
		VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)`,
		p.ID, p.Symbol, p.Direction, p.Quantity, p.EntryPrice, p.EntryTime, p.Confidence,
		p.AlpacaOrderID, string(p.Status), p.StopLossPrice, p.TakeProfitPrice, p.CreatedAt, p.UpdatedAt,
	)
	return err
}

// GetPosition fetches a single position by ID.
func (d *DB) GetPosition(id string) (*Position, error) {
	row := d.QueryRow(`
		SELECT id, symbol, direction, quantity, entry_price, entry_time, confidence,
		       COALESCE(alpaca_order_id,''), status,
		       exit_price, exit_time, realized_pnl,
		       stop_loss_price, take_profit_price, COALESCE(close_reason,''),
		       created_at, updated_at
		FROM positions WHERE id = ?`, id)
	return scanPosition(row)
}

// ListOpenPositions returns all positions with status OPEN.
func (d *DB) ListOpenPositions() ([]*Position, error) {
	rows, err := d.Query(`
		SELECT id, symbol, direction, quantity, entry_price, entry_time, confidence,
		       COALESCE(alpaca_order_id,''), status,
		       exit_price, exit_time, realized_pnl,
		       stop_loss_price, take_profit_price, COALESCE(close_reason,''),
		       created_at, updated_at
		FROM positions WHERE status = 'OPEN' ORDER BY entry_time DESC`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []*Position
	for rows.Next() {
		p, err := scanPosition(rows)
		if err != nil {
			return nil, err
		}
		out = append(out, p)
	}
	return out, nil
}

// GetOpenPositionBySymbol returns the most recent OPEN position for a given symbol.
func (d *DB) GetOpenPositionBySymbol(symbol string) (*Position, error) {
	row := d.QueryRow(`
		SELECT id, symbol, direction, quantity, entry_price, entry_time, confidence,
		       COALESCE(alpaca_order_id,''), status,
		       exit_price, exit_time, realized_pnl,
		       stop_loss_price, take_profit_price, COALESCE(close_reason,''),
		       created_at, updated_at
		FROM positions WHERE status = 'OPEN' AND symbol = ? ORDER BY entry_time DESC LIMIT 1`, symbol)
	return scanPosition(row)
}

// ListFailedOrders returns all staged orders with status FAILED, newest first.
func (d *DB) ListFailedOrders() ([]*StagedOrder, error) {
	rows, err := d.Query(`
		SELECT id, symbol, direction, quantity, limit_price, confidence,
		       reasoning, strategy_name, model_used, status,
		       COALESCE(trader_comment,''), COALESCE(ibkr_order_id,0),
		       created_at, updated_at
		FROM staged_orders WHERE status = 'FAILED' ORDER BY updated_at DESC`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []*StagedOrder
	for rows.Next() {
		o := &StagedOrder{}
		var status string
		err := rows.Scan(
			&o.ID, &o.Symbol, &o.Direction, &o.Quantity, &o.LimitPrice, &o.Confidence,
			&o.Reasoning, &o.StrategyName, &o.ModelUsed, &status,
			&o.TraderComment, &o.IBKROrderID, &o.CreatedAt, &o.UpdatedAt,
		)
		if err != nil {
			return nil, err
		}
		o.Status = OrderStatus(status)
		out = append(out, o)
	}
	return out, nil
}

// ResetToRetry transitions a FAILED order back to PENDING so it can be re-approved.
func (d *DB) ResetToRetry(id string) error {
	_, err := d.Exec(`
		UPDATE staged_orders SET status = 'PENDING', trader_comment = 'retried', updated_at = ?
		WHERE id = ? AND status = 'FAILED'`, nowMs(), id)
	return err
}

// ListAllPositions returns all positions (OPEN + CLOSED) ordered by entry_time DESC.
func (d *DB) ListAllPositions() ([]*Position, error) {
	rows, err := d.Query(`
		SELECT id, symbol, direction, quantity, entry_price, entry_time, confidence,
		       COALESCE(alpaca_order_id,''), status,
		       exit_price, exit_time, realized_pnl,
		       stop_loss_price, take_profit_price, COALESCE(close_reason,''),
		       created_at, updated_at
		FROM positions ORDER BY entry_time DESC`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []*Position
	for rows.Next() {
		p, err := scanPosition(rows)
		if err != nil {
			return nil, err
		}
		out = append(out, p)
	}
	return out, nil
}

// GetAllTimePnl returns the sum of realized_pnl across all trading days.
// Uses trading_limits (updated on every close, regardless of DB position record)
// rather than the positions table, which may be missing rows for positions placed
// before DB recording was deployed.
func (d *DB) GetAllTimePnl() (float64, error) {
	var total float64
	row := d.QueryRow(`SELECT COALESCE(SUM(realized_pnl), 0) FROM trading_limits`)
	err := row.Scan(&total)
	return total, err
}

// SyncFillPrice updates entry_price once an Alpaca market order actually fills.
func (d *DB) SyncFillPrice(id string, fillPrice float64) error {
	_, err := d.Exec(
		`UPDATE positions SET entry_price = ?, updated_at = ? WHERE id = ? AND entry_price = 0`,
		fillPrice, time.Now().UnixMilli(), id,
	)
	return err
}

// ClosePosition records exit details and transitions status to CLOSED.
func (d *DB) ClosePosition(id string, exitPrice, realizedPnl float64, reason string) error {
	now := time.Now().UnixMilli()
	_, err := d.Exec(`
		UPDATE positions
		SET status = 'CLOSED', exit_price = ?, exit_time = ?,
		    realized_pnl = ?, close_reason = ?, updated_at = ?
		WHERE id = ?`,
		exitPrice, now, realizedPnl, reason, now, id,
	)
	return err
}

// GetTodayLimits returns today's trading limits row, creating it if absent.
func (d *DB) GetTodayLimits() (*TradingLimits, error) {
	today := time.Now().Format("2006-01-02")
	_, _ = d.Exec(`
		INSERT OR IGNORE INTO trading_limits (date, realized_pnl, trade_count, is_halted)
		VALUES (?, 0.0, 0, 0)`, today)
	row := d.QueryRow(`
		SELECT date, realized_pnl, trade_count, is_halted
		FROM trading_limits WHERE date = ?`, today)
	var l TradingLimits
	var halted int
	if err := row.Scan(&l.Date, &l.RealizedPnl, &l.TradeCount, &halted); err != nil {
		return nil, err
	}
	l.IsHalted = halted == 1
	return &l, nil
}

// UpdateTodayPnl increments today's realized P&L and trade count.
// Sets is_halted=1 if the cumulative loss exceeds limitUSD.
func (d *DB) UpdateTodayPnl(addedPnl float64, limitUSD float64) error {
	today := time.Now().Format("2006-01-02")
	_, _ = d.Exec(`
		INSERT OR IGNORE INTO trading_limits (date, realized_pnl, trade_count, is_halted)
		VALUES (?, 0.0, 0, 0)`, today)
	_, err := d.Exec(`
		UPDATE trading_limits
		SET realized_pnl  = realized_pnl + ?,
		    trade_count   = trade_count + 1,
		    is_halted     = CASE WHEN realized_pnl + ? < ? THEN 1 ELSE is_halted END
		WHERE date = ?`,
		addedPnl, addedPnl, -limitUSD, today,
	)
	return err
}

func scanPosition(s scanner) (*Position, error) {
	p := &Position{}
	var status string
	err := s.Scan(
		&p.ID, &p.Symbol, &p.Direction, &p.Quantity, &p.EntryPrice, &p.EntryTime, &p.Confidence,
		&p.AlpacaOrderID, &status,
		&p.ExitPrice, &p.ExitTime, &p.RealizedPnl,
		&p.StopLossPrice, &p.TakeProfitPrice, &p.CloseReason,
		&p.CreatedAt, &p.UpdatedAt,
	)
	if err != nil {
		return nil, err
	}
	p.Status = PositionStatus(status)
	return p, nil
}

// ─── Alerts ───────────────────────────────────────────────────────────────────

type Alert struct {
	ID        int64  `json:"id"`
	Severity  string `json:"severity"`
	Title     string `json:"title"`
	Body      string `json:"body"`
	CreatedAt int64  `json:"created_at"`
}

func (d *DB) InsertAlert(severity, title, body string) error {
	_, err := d.Exec(
		`INSERT INTO alerts (severity, title, body, created_at) VALUES (?,?,?,?)`,
		severity, title, body, time.Now().UnixMilli(),
	)
	return err
}

func (d *DB) ListAlerts(limit int) ([]*Alert, error) {
	if limit <= 0 {
		limit = 100
	}
	rows, err := d.Query(
		`SELECT id, severity, title, body, created_at FROM alerts ORDER BY created_at DESC LIMIT ?`, limit,
	)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var alerts []*Alert
	for rows.Next() {
		a := &Alert{}
		if err := rows.Scan(&a.ID, &a.Severity, &a.Title, &a.Body, &a.CreatedAt); err == nil {
			alerts = append(alerts, a)
		}
	}
	return alerts, nil
}

// ─── Threshold Store ──────────────────────────────────────────────────────────

// ThresholdEntry is one calibrated confidence threshold for a strategy/bucket/regime.
type ThresholdEntry struct {
	StrategyName     string  `json:"strategy_name"`
	ConfidenceBucket string  `json:"confidence_bucket"`
	SpyTrend         string  `json:"spy_trend"`
	SampleCount      int     `json:"sample_count"`
	WinRate          float64 `json:"win_rate"`
	MinConfidence    float64 `json:"min_confidence"`
	UpdatedAt        int64   `json:"updated_at"`
}

// UpsertThreshold inserts or updates a calibrated threshold entry.
func (d *DB) UpsertThreshold(t ThresholdEntry) error {
	_, err := d.Exec(`
		INSERT INTO threshold_store
			(strategy_name, confidence_bucket, spy_trend, sample_count, win_rate, min_confidence, updated_at)
		VALUES (?,?,?,?,?,?,?)
		ON CONFLICT(strategy_name, confidence_bucket, spy_trend)
		DO UPDATE SET
			sample_count   = excluded.sample_count,
			win_rate       = excluded.win_rate,
			min_confidence = excluded.min_confidence,
			updated_at     = excluded.updated_at`,
		t.StrategyName, t.ConfidenceBucket, t.SpyTrend,
		t.SampleCount, t.WinRate, t.MinConfidence, time.Now().UnixMilli(),
	)
	return err
}

// GetThreshold returns the calibrated min_confidence for a strategy/bucket/regime.
// Returns the fallback value if no entry exists or sample count < minSamples.
func (d *DB) GetThreshold(strategyName, confidenceBucket, spyTrend string, fallback float64, minSamples int) float64 {
	var sampleCount int
	var minConf float64
	err := d.QueryRow(`
		SELECT sample_count, min_confidence FROM threshold_store
		WHERE strategy_name=? AND confidence_bucket=? AND spy_trend=?`,
		strategyName, confidenceBucket, spyTrend,
	).Scan(&sampleCount, &minConf)
	if err != nil || sampleCount < minSamples {
		return fallback
	}
	return minConf
}

// ListThresholds returns all calibrated threshold entries.
func (d *DB) ListThresholds() ([]*ThresholdEntry, error) {
	rows, err := d.Query(`
		SELECT strategy_name, confidence_bucket, spy_trend, sample_count, win_rate, min_confidence, updated_at
		FROM threshold_store ORDER BY strategy_name, confidence_bucket, spy_trend`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var entries []*ThresholdEntry
	for rows.Next() {
		t := &ThresholdEntry{}
		if err := rows.Scan(&t.StrategyName, &t.ConfidenceBucket, &t.SpyTrend,
			&t.SampleCount, &t.WinRate, &t.MinConfidence, &t.UpdatedAt); err == nil {
			entries = append(entries, t)
		}
	}
	return entries, nil
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

CREATE TABLE IF NOT EXISTS positions (
    id               TEXT PRIMARY KEY,
    symbol           TEXT NOT NULL,
    direction        TEXT NOT NULL CHECK (direction IN ('LONG','SHORT')),
    quantity         REAL NOT NULL CHECK (quantity > 0),
    entry_price      REAL NOT NULL,
    entry_time       INTEGER NOT NULL,
    confidence       REAL NOT NULL,
    alpaca_order_id  TEXT,
    status           TEXT NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN','CLOSED')),
    exit_price       REAL,
    exit_time        INTEGER,
    realized_pnl     REAL,
    stop_loss_price  REAL,
    take_profit_price REAL,
    close_reason     TEXT,
    created_at       INTEGER NOT NULL,
    updated_at       INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_positions_status
    ON positions (status, entry_time DESC);

CREATE TABLE IF NOT EXISTS trading_limits (
    date         TEXT PRIMARY KEY,
    realized_pnl REAL NOT NULL DEFAULT 0.0,
    trade_count  INTEGER NOT NULL DEFAULT 0,
    is_halted    INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS signal_outcomes (
    signal_id          TEXT PRIMARY KEY REFERENCES staged_orders(id),
    symbol             TEXT NOT NULL,
    predicted_direction TEXT NOT NULL,
    strategy_name      TEXT NOT NULL DEFAULT '',
    confidence         REAL NOT NULL,
    vix_at_signal      REAL NOT NULL DEFAULT 0,
    entry_price        REAL NOT NULL DEFAULT 0,
    check_5d_at        INTEGER NOT NULL,
    check_20d_at       INTEGER NOT NULL,
    price_5d           REAL,
    return_5d          REAL,
    outcome_5d         TEXT CHECK (outcome_5d IN ('TRUE_POSITIVE','FALSE_POSITIVE','REGIME_MISMATCH')),
    price_20d          REAL,
    return_20d         REAL,
    outcome_20d        TEXT CHECK (outcome_20d IN ('TRUE_POSITIVE','FALSE_POSITIVE','REGIME_MISMATCH')),
    checked_5d_at      INTEGER,
    checked_20d_at     INTEGER,
    created_at         INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_signal_outcomes_check
    ON signal_outcomes (check_5d_at, outcome_5d);

CREATE TABLE IF NOT EXISTS alerts (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    severity   TEXT NOT NULL CHECK (severity IN ('CRITICAL','HIGH','MEDIUM','INFO')),
    title      TEXT NOT NULL,
    body       TEXT NOT NULL DEFAULT '',
    created_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_alerts_created
    ON alerts (created_at DESC);

CREATE TABLE IF NOT EXISTS threshold_store (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy_name    TEXT NOT NULL,
    confidence_bucket TEXT NOT NULL,  -- e.g. "0.70-0.75", "0.75-0.80"
    spy_trend        TEXT NOT NULL,   -- "uptrend" | "downtrend" | "sideways"
    sample_count     INTEGER NOT NULL DEFAULT 0,
    win_rate         REAL NOT NULL DEFAULT 0.0,
    min_confidence   REAL NOT NULL DEFAULT 0.70,
    updated_at       INTEGER NOT NULL,
    UNIQUE(strategy_name, confidence_bucket, spy_trend)
);
`
