// Package greenlight exposes the HTTP API for the trader's Green Light gate.
// Green Light → Alpaca paper order (market order, day TIF).
package greenlight

import (
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"strconv"
	"strings"
	"time"

	"go.uber.org/zap"

	"github.com/marketflow/backend/internal/alpaca"
	"github.com/marketflow/backend/internal/db"
	"github.com/marketflow/backend/internal/ibkr"
	"github.com/marketflow/backend/internal/mode"
	"github.com/marketflow/backend/internal/ws"
)

// cashOnlyEnabled reports whether cash-account discipline is enforced.
// Default ON — Alpaca is a margin venue, but the production IBKR account
// will be cash-only, so paper must behave the same way.
func cashOnlyEnabled() bool {
	v := strings.ToLower(os.Getenv("CASH_ONLY_MODE"))
	return v != "false" && v != "0" && v != "off"
}

// CashGuardBlocks returns (reason, true) when executing this order would
// require borrowing. Sells pass — they raise cash. Buys must fit inside
// settled cash (negative cash counts as zero). Orders without a usable
// price are blocked, never waved through (fail closed). Exported so every
// execution path (Green Light approve, order retry) applies the same rule.
func CashGuardBlocks(a *alpaca.Handler, order *db.StagedOrder) (string, bool) {
	if !cashOnlyEnabled() {
		return "", false
	}
	dir := strings.ToUpper(order.Direction)
	if dir == "SHORT" {
		return "cash-only guard: short selling is borrowing — blocked", true
	}
	if dir != "BUY" && dir != "COVER" {
		return "", false // SELL raises cash
	}
	if order.LimitPrice <= 0 {
		return "cash-only guard: order has no price — cannot verify cost, blocked", true
	}
	cash, err := a.SettledCash()
	if err != nil {
		return "cash-only guard: cannot read account cash (" + err.Error() + ") — blocked", true
	}
	cost := order.Quantity * order.LimitPrice
	if cost > cash {
		return fmt.Sprintf(
			"cash-only guard: need $%.2f for %.0f %s @ $%.2f, settled cash available $%.2f — no borrowing",
			cost, order.Quantity, order.Symbol, order.LimitPrice, cash,
		), true
	}
	return "", false
}

func (h *Handler) cashGuardBlocks(order *db.StagedOrder) (string, bool) {
	return CashGuardBlocks(h.alpaca, order)
}

// Handler handles Green Light HTTP requests from the React dashboard.
type Handler struct {
	db      *db.DB
	hub     *ws.Hub
	ibkr    *ibkr.Client
	alpaca  *alpaca.Handler
	mode    *mode.Manager
	log     *zap.Logger
}

func NewHandler(database *db.DB, hub *ws.Hub, modeManager *mode.Manager, log *zap.Logger) *Handler {
	return &Handler{
		db:     database,
		hub:    hub,
		ibkr:   ibkr.NewClient(log),
		alpaca: alpaca.NewHandler(),
		mode:   modeManager,
		log:    log,
	}
}

// ListPending returns all orders with status PENDING.
// GET /api/orders/pending?limit=50&offset=0
func (h *Handler) ListPending(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}

	limit, _ := strconv.Atoi(r.URL.Query().Get("limit"))
	offset, _ := strconv.Atoi(r.URL.Query().Get("offset"))

	orders, total, err := h.db.ListByStatus(db.StatusPending, limit, offset)
	if err != nil {
		h.log.Error("ListPending failed", zap.Error(err))
		http.Error(w, "internal error", http.StatusInternalServerError)
		return
	}

	writeJSON(w, map[string]any{"orders": orders, "total": total})
}

// Approve grants a Green Light for a staged order and forwards it to IBKR.
// POST /api/orders/approve
func (h *Handler) Approve(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}

	var req struct {
		SignalID string `json:"signal_id"`
		Comment  string `json:"comment"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, "invalid request body", http.StatusBadRequest)
		return
	}
	if req.SignalID == "" {
		http.Error(w, "signal_id is required", http.StatusBadRequest)
		return
	}

	// Fetch the order before transitioning so we have its details for IBKR.
	order, err := h.db.GetOrder(req.SignalID)
	if err != nil {
		http.Error(w, "order not found", http.StatusNotFound)
		return
	}

	// Cash-only guard: Alpaca accounts are always margin, but MarketFlow
	// must behave like the future IBKR CASH account — never buy with money
	// we don't have, never short. Runs BEFORE any state transition so a
	// blocked order simply stays PENDING with a clear reason for the trader.
	if reason, blocked := h.cashGuardBlocks(order); blocked {
		h.log.Warn("green light blocked by cash-only guard",
			zap.String("signal_id", order.ID),
			zap.String("symbol", order.Symbol),
			zap.String("reason", reason),
		)
		http.Error(w, reason, http.StatusConflict)
		return
	}

	// Transition to APPROVED in the DB.
	if err := h.db.TransitionStatus(req.SignalID, db.StatusApproved, "trader", req.Comment); err != nil {
		h.log.Error("approve transition failed", zap.Error(err))
		http.Error(w, err.Error(), http.StatusConflict)
		return
	}

	h.hub.Broadcast("order_approved", order)
	h.log.Info("Green Light granted",
		zap.String("signal_id", req.SignalID),
		zap.String("symbol", order.Symbol),
	)

	// Record signal outcome for postmortem tracking (entry_price synced later by brain).
	go func() {
		if err := h.db.CreateSignalOutcome(db.SignalOutcome{
			SignalID:           order.ID,
			Symbol:             order.Symbol,
			PredictedDirection: order.Direction,
			StrategyName:       order.StrategyName,
			Confidence:         order.Confidence,
		}); err != nil {
			h.log.Warn("signal outcome record failed", zap.String("signal_id", order.ID), zap.Error(err))
		}
	}()

	// Submit to IBKR asynchronously — the dashboard tracks the result via WebSocket.
	go h.submitToIBKR(order)

	writeJSON(w, map[string]any{"success": true, "signal_id": req.SignalID})
}

// Reject marks a staged order as REJECTED. No IBKR call is made.
// POST /api/orders/reject
func (h *Handler) Reject(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}

	var req struct {
		SignalID string `json:"signal_id"`
		Comment  string `json:"comment"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, "invalid request body", http.StatusBadRequest)
		return
	}
	if req.SignalID == "" {
		http.Error(w, "signal_id is required", http.StatusBadRequest)
		return
	}

	if err := h.db.TransitionStatus(req.SignalID, db.StatusRejected, "trader", req.Comment); err != nil {
		h.log.Error("reject transition failed", zap.Error(err))
		http.Error(w, err.Error(), http.StatusConflict)
		return
	}

	h.hub.Broadcast("order_rejected", map[string]string{"signal_id": req.SignalID})
	h.log.Info("Order rejected", zap.String("signal_id", req.SignalID))

	writeJSON(w, map[string]any{"success": true, "signal_id": req.SignalID})
}

// submitToAlpaca places a market order on Alpaca paper trading and updates DB.
// This is the ONLY place where a manually approved order reaches the broker.
func (h *Handler) submitToIBKR(order *db.StagedOrder) {
	result, err := h.alpaca.PlaceOrder(order.Symbol, order.Direction, order.Quantity)
	if err != nil {
		h.log.Error("alpaca order placement failed",
			zap.String("signal_id", order.ID),
			zap.String("symbol", order.Symbol),
			zap.Error(err),
		)
		_ = h.db.TransitionStatus(order.ID, db.StatusFailed, "alpaca", err.Error())
		h.hub.Broadcast("order_failed", map[string]string{
			"signal_id": order.ID,
			"error":     err.Error(),
		})
		return
	}

	note := "Alpaca paper order " + result.ID
	_ = h.db.TransitionStatus(order.ID, db.StatusExecuted, "alpaca", note)

	// Fetch the real fill price from Alpaca (polls up to 3x with fallback).
	fillPrice := h.alpaca.FetchFillPrice(result.ID, order.Symbol)
	if fillPrice == 0 && order.LimitPrice > 0 {
		fillPrice = order.LimitPrice // last resort: use requested limit price
	}
	h.log.Info("fill price synced",
		zap.String("signal_id", order.ID),
		zap.String("symbol", order.Symbol),
		zap.Float64("fill_price", fillPrice),
	)

	// Record in positions table for history/charting.
	direction := "LONG"
	if order.Direction == "SELL" || order.Direction == "SHORT" || order.Direction == "COVER" {
		direction = "SHORT"
	}
	pos := &db.Position{
		ID:            order.ID,
		Symbol:        order.Symbol,
		Direction:     direction,
		Quantity:      order.Quantity,
		EntryPrice:    fillPrice,
		EntryTime:     time.Now().UnixMilli(),
		Confidence:    order.Confidence,
		AlpacaOrderID: result.ID,
	}
	if err := h.db.OpenPosition(pos); err != nil {
		h.log.Warn("failed to record position in DB", zap.String("signal_id", order.ID), zap.Error(err))
	}

	h.hub.Broadcast("order_executed", map[string]any{
		"signal_id":       order.ID,
		"alpaca_order_id": result.ID,
		"alpaca_status":   result.Status,
		"simulated":       false,
	})
	h.log.Info("order placed on Alpaca paper",
		zap.String("signal_id", order.ID),
		zap.String("symbol", order.Symbol),
		zap.String("alpaca_order_id", result.ID),
		zap.String("status", result.Status),
	)
}

func writeJSON(w http.ResponseWriter, v any) {
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(v)
}
