// Package mode manages the global trading mode: "yahoo" (simulation) or "ibkr" (live).
//
// Yahoo mode: the Python brain uses yfinance data and simulated order execution.
//             No real money at risk. No IBKR connection required.
// IBKR mode:  the Python brain uses IBKR TWS data. Approved orders are routed
//             to the real broker through the Green Light gate.
//
// The mode is stored in memory (and optionally persisted to DB) so the Python
// brain can poll /api/mode and switch its data source and executor accordingly.
package mode

import (
	"encoding/json"
	"net/http"
	"sync"

	"go.uber.org/zap"
)

// TradingMode represents which data source + execution path is active.
type TradingMode string

const (
	ModeYahoo TradingMode = "yahoo" // yfinance simulation (default, safe)
	ModeIBKR  TradingMode = "ibkr"  // Interactive Brokers live trading
)

// Manager holds the current trading mode with mutex-protected access.
type Manager struct {
	mu      sync.RWMutex
	current TradingMode
	log     *zap.Logger
}

// NewManager creates a Manager starting in Yahoo simulation mode (safest default).
func NewManager(log *zap.Logger) *Manager {
	return &Manager{current: ModeYahoo, log: log}
}

// Get returns the current trading mode (thread-safe).
func (m *Manager) Get() TradingMode {
	m.mu.RLock()
	defer m.mu.RUnlock()
	return m.current
}

// IsLive returns true when IBKR live trading is active.
func (m *Manager) IsLive() bool {
	return m.Get() == ModeIBKR
}

// Set changes the trading mode (thread-safe).
func (m *Manager) Set(mode TradingMode) {
	m.mu.Lock()
	defer m.mu.Unlock()
	if mode != ModeYahoo && mode != ModeIBKR {
		mode = ModeYahoo
	}
	prev := m.current
	m.current = mode
	m.log.Info("trading mode changed",
		zap.String("from", string(prev)),
		zap.String("to", string(mode)),
	)
}

// ─── HTTP Handlers ─────────────────────────────────────────────────────────────

type modeResponse struct {
	Mode    TradingMode `json:"mode"`
	IsLive  bool        `json:"is_live"`
	Message string      `json:"message,omitempty"`
}

// GetHandler handles GET /api/mode — returns the current trading mode.
func (m *Manager) GetHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	current := m.Get()
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(modeResponse{
		Mode:   current,
		IsLive: current == ModeIBKR,
	})
}

// SetHandler handles POST /api/mode — switches the trading mode.
//
// Body: { "mode": "yahoo" | "ibkr" }
//
// Switching to IBKR triggers a warning in the log — no trade executes
// without the trader's Green Light, but this is a meaningful mode change
// that should be intentional.
func (m *Manager) SetHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}

	var req struct {
		Mode TradingMode `json:"mode"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, "invalid request body", http.StatusBadRequest)
		return
	}

	if req.Mode != ModeYahoo && req.Mode != ModeIBKR {
		http.Error(w, `mode must be "yahoo" or "ibkr"`, http.StatusBadRequest)
		return
	}

	m.Set(req.Mode)

	msg := ""
	if req.Mode == ModeIBKR {
		msg = "IBKR live mode activated — Green Light gate still enforced on all orders"
	} else {
		msg = "Yahoo simulation mode activated — no real orders will be sent"
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(modeResponse{
		Mode:    req.Mode,
		IsLive:  req.Mode == ModeIBKR,
		Message: msg,
	})
}
