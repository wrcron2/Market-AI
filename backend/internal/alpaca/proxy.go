// Package alpaca proxies Alpaca paper account endpoints to the dashboard
// and places orders on behalf of the Green Light handler.
package alpaca

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"math"
	"net/http"
	"os"
	"strings"
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

// OrderResult is returned by PlaceOrder.
type OrderResult struct {
	ID     string  `json:"id"`
	Status string  `json:"status"`
	Symbol string  `json:"symbol"`
	Qty    float64 `json:"qty,string"`
}

// PlaceOrder submits a market order to Alpaca paper trading.
// direction must be "BUY" or "SELL". qty is rounded to nearest integer.
func (h *Handler) PlaceOrder(symbol, direction string, qty float64) (*OrderResult, error) {
	if h.apiKey == "" || h.secretKey == "" {
		return nil, fmt.Errorf("alpaca credentials not configured")
	}
	side := strings.ToLower(direction)
	if side != "buy" && side != "sell" {
		return nil, fmt.Errorf("unsupported direction: %s", direction)
	}

	body, _ := json.Marshal(map[string]any{
		"symbol":        symbol,
		"qty":           fmt.Sprintf("%d", int(math.Round(qty))),
		"side":          side,
		"type":          "market",
		"time_in_force": "day",
	})

	req, err := http.NewRequest(http.MethodPost, h.baseURL+"/v2/orders", bytes.NewReader(body))
	if err != nil {
		return nil, err
	}
	req.Header.Set("APCA-API-KEY-ID", h.apiKey)
	req.Header.Set("APCA-API-SECRET-KEY", h.secretKey)
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Accept", "application/json")

	resp, err := h.client.Do(req)
	if err != nil {
		return nil, fmt.Errorf("alpaca unreachable: %w", err)
	}
	defer resp.Body.Close()

	respBody, _ := io.ReadAll(resp.Body)
	if resp.StatusCode >= 400 {
		return nil, fmt.Errorf("alpaca order rejected (%d): %s", resp.StatusCode, string(respBody))
	}

	var result OrderResult
	if err := json.Unmarshal(respBody, &result); err != nil {
		return nil, fmt.Errorf("alpaca response parse error: %w", err)
	}
	return &result, nil
}

// CloseResult is returned by ClosePosition.
type CloseResult struct {
	ID        string  `json:"id"`
	Symbol    string  `json:"symbol"`
	FillPrice float64 `json:"fill_price"`
}

// ClosePosition liquidates the full open position for a symbol at market.
func (h *Handler) ClosePosition(symbol string) (*CloseResult, error) {
	if h.apiKey == "" || h.secretKey == "" {
		return nil, fmt.Errorf("alpaca credentials not configured")
	}

	// First get current price from the position
	posReq, _ := http.NewRequest(http.MethodGet, h.baseURL+"/v2/positions/"+symbol, nil)
	posReq.Header.Set("APCA-API-KEY-ID", h.apiKey)
	posReq.Header.Set("APCA-API-SECRET-KEY", h.secretKey)
	posResp, err := h.client.Do(posReq)
	if err != nil {
		return nil, fmt.Errorf("alpaca unreachable: %w", err)
	}
	posBody, _ := io.ReadAll(posResp.Body)
	posResp.Body.Close()

	var posData map[string]any
	_ = json.Unmarshal(posBody, &posData)
	currentPrice := 0.0
	if cp, ok := posData["current_price"].(string); ok {
		fmt.Sscanf(cp, "%f", &currentPrice)
	}

	// Close the position
	req, _ := http.NewRequest(http.MethodDelete, h.baseURL+"/v2/positions/"+symbol, nil)
	req.Header.Set("APCA-API-KEY-ID", h.apiKey)
	req.Header.Set("APCA-API-SECRET-KEY", h.secretKey)

	resp, err := h.client.Do(req)
	if err != nil {
		return nil, fmt.Errorf("alpaca unreachable: %w", err)
	}
	defer resp.Body.Close()

	respBody, _ := io.ReadAll(resp.Body)
	if resp.StatusCode == 204 {
		return &CloseResult{Symbol: symbol, FillPrice: currentPrice}, nil
	}
	if resp.StatusCode >= 400 {
		return nil, fmt.Errorf("alpaca close rejected (%d): %s", resp.StatusCode, string(respBody))
	}

	var order map[string]any
	_ = json.Unmarshal(respBody, &order)
	orderID, _ := order["id"].(string)
	return &CloseResult{ID: orderID, Symbol: symbol, FillPrice: currentPrice}, nil
}

// WriteJSON is a shared helper for JSON responses within this package.
func WriteJSON(w http.ResponseWriter, v any) {
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(v)
}
