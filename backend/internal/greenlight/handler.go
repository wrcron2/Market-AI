// Package greenlight exposes the HTTP API for the trader's Green Light gate.
// NO trade can reach IBKR without an explicit Approve call here.
package greenlight

import (
	"encoding/json"
	"net/http"
	"strconv"

	"go.uber.org/zap"

	"github.com/marketflow/backend/internal/db"
	"github.com/marketflow/backend/internal/ibkr"
	"github.com/marketflow/backend/internal/mode"
	"github.com/marketflow/backend/internal/ws"
)

// Handler handles Green Light HTTP requests from the React dashboard.
type Handler struct {
	db      *db.DB
	hub     *ws.Hub
	ibkr    *ibkr.Client
	mode    *mode.Manager
	log     *zap.Logger
}

func NewHandler(database *db.DB, hub *ws.Hub, modeManager *mode.Manager, log *zap.Logger) *Handler {
	return &Handler{
		db:   database,
		hub:  hub,
		ibkr: ibkr.NewClient(log),
		mode: modeManager,
		log:  log,
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

// submitOrder routes the approved order to the correct executor based on
// the current trading mode:
//   - Yahoo mode → simulated execution (no real broker call)
//   - IBKR mode  → real IBKR TWS execution via the air-gapped client
//
// This is the ONLY place where an order leaves the staged state.
func (h *Handler) submitToIBKR(order *db.StagedOrder) {
	if !h.mode.IsLive() {
		// ── Yahoo / simulation mode ────────────────────────────────────────────
		h.log.Info("[SIM] simulating order execution (Yahoo mode)",
			zap.String("signal_id", order.ID),
			zap.String("symbol", order.Symbol),
			zap.String("direction", order.Direction),
		)
		_ = h.db.TransitionStatus(order.ID, db.StatusExecuted, "simulator", "Simulated fill — Yahoo Finance mode")
		h.hub.Broadcast("order_executed", map[string]any{
			"signal_id":    order.ID,
			"ibkr_order_id": nil,
			"simulated":    true,
		})
		return
	}

	// ── IBKR live mode ─────────────────────────────────────────────────────────
	ibkrID, err := h.ibkr.PlaceOrder(order)
	if err != nil {
		h.log.Error("IBKR order placement failed",
			zap.String("signal_id", order.ID),
			zap.Error(err),
		)
		_ = h.db.TransitionStatus(order.ID, db.StatusFailed, "ibkr", err.Error())
		h.hub.Broadcast("order_failed", map[string]string{
			"signal_id": order.ID,
			"error":     err.Error(),
		})
		return
	}

	_ = h.db.SetIBKROrderID(order.ID, ibkrID)
	_ = h.db.TransitionStatus(order.ID, db.StatusExecuted, "ibkr", "Order executed")
	h.hub.Broadcast("order_executed", map[string]any{
		"signal_id":     order.ID,
		"ibkr_order_id": ibkrID,
		"simulated":     false,
	})
	h.log.Info("Order executed via IBKR",
		zap.String("signal_id", order.ID),
		zap.Int64("ibkr_order_id", ibkrID),
	)
}

func writeJSON(w http.ResponseWriter, v any) {
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(v)
}
