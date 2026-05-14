// Package alpaca proxies read-only Alpaca paper account endpoints to the dashboard.
// The Python brain owns all write operations (orders, closes). This package
// only reads: account info and current positions.
package alpaca

import (
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
)

// Handler proxies dashboard requests to the Alpaca paper API.
type Handler struct {
	apiKey    string
	secretKey string
	baseURL   string
	client    *http.Client
}

// NewHandler reads credentials from environment variables.
func NewHandler() *Handler {
	return &Handler{
		apiKey:    os.Getenv("ALPACA_API_KEY"),
		secretKey: os.Getenv("ALPACA_SECRET_KEY"),
		baseURL:   getenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets"),
		client:    &http.Client{},
	}
}

// Account proxies GET /api/alpaca/account → Alpaca /v2/account.
func (h *Handler) Account(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	h.proxyGet(w, "/v2/account")
}

// Positions proxies GET /api/alpaca/positions → Alpaca /v2/positions.
func (h *Handler) Positions(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	h.proxyGet(w, "/v2/positions")
}

func (h *Handler) proxyGet(w http.ResponseWriter, path string) {
	if h.apiKey == "" || h.secretKey == "" {
		http.Error(w, "Alpaca credentials not configured", http.StatusServiceUnavailable)
		return
	}

	req, err := http.NewRequest(http.MethodGet, h.baseURL+path, nil)
	if err != nil {
		http.Error(w, fmt.Sprintf("request build error: %v", err), http.StatusInternalServerError)
		return
	}
	req.Header.Set("APCA-API-KEY-ID", h.apiKey)
	req.Header.Set("APCA-API-SECRET-KEY", h.secretKey)
	req.Header.Set("Accept", "application/json")

	resp, err := h.client.Do(req)
	if err != nil {
		http.Error(w, fmt.Sprintf("alpaca unreachable: %v", err), http.StatusBadGateway)
		return
	}
	defer resp.Body.Close()

	body, _ := io.ReadAll(resp.Body)
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(resp.StatusCode)
	w.Write(body)
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

func getenv(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}

// WriteJSON is a shared helper for JSON responses within this package.
func WriteJSON(w http.ResponseWriter, v any) {
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(v)
}
