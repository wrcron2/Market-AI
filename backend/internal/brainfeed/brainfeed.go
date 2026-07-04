// Package brainfeed receives live pipeline-step telemetry from the Python
// brain and serves it to the dashboard: each event is broadcast over the
// WebSocket hub immediately and kept in an in-memory ring buffer so the
// Brain Activity panel can backfill after a page refresh. The audit log
// remains the durable record — this buffer is intentionally ephemeral.
package brainfeed

import (
	"encoding/json"
	"net/http"
	"sync"
	"time"

	"github.com/marketflow/backend/internal/ws"
)

const bufferSize = 200

// Event is one pipeline step reported by the brain.
type Event struct {
	Symbol    string `json:"symbol"`
	Step      string `json:"step"`   // scan | signal | debate | risk | stage | execute
	Status    string `json:"status"` // ok | skip | blocked | error
	Detail    string `json:"detail"`
	Timestamp int64  `json:"timestamp"` // unix ms, set server-side
}

type Handler struct {
	hub *ws.Hub

	mu     sync.RWMutex
	events []Event // ring buffer, newest last
}

func New(hub *ws.Hub) *Handler {
	return &Handler{hub: hub}
}

// Post handles POST /api/brain/activity — called by the brain at each step.
func (h *Handler) Post(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	var ev Event
	if err := json.NewDecoder(r.Body).Decode(&ev); err != nil {
		http.Error(w, "invalid body", http.StatusBadRequest)
		return
	}
	ev.Timestamp = time.Now().UnixMilli()

	h.mu.Lock()
	h.events = append(h.events, ev)
	if len(h.events) > bufferSize {
		h.events = h.events[len(h.events)-bufferSize:]
	}
	h.mu.Unlock()

	if h.hub != nil {
		h.hub.Broadcast("brain_activity", ev)
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]any{"ok": true})
}

// List handles GET /api/brain/activity — newest first, for feed backfill.
func (h *Handler) List(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	h.mu.RLock()
	out := make([]Event, len(h.events))
	for i, ev := range h.events {
		out[len(h.events)-1-i] = ev
	}
	h.mu.RUnlock()

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]any{"events": out})
}
