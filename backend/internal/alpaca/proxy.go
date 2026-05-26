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
	"sync"
	"time"
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

// PeriodResult holds portfolio P&L for a single time period.
type PeriodResult struct {
	Label      string  `json:"label"`
	Period     string  `json:"period"`
	StartValue float64 `json:"start_value"`
	EndValue   float64 `json:"end_value"`
	PnL        float64 `json:"pnl"`
	PnLPct     float64 `json:"pnl_pct"`
}

// PortfolioHistory fetches equity-curve P&L for 1D / 5D / 3M / 6M / 1Y in
// parallel and returns all five results in one response.
func (h *Handler) PortfolioHistory(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	if h.apiKey == "" || h.secretKey == "" {
		http.Error(w, "Alpaca credentials not configured", http.StatusServiceUnavailable)
		return
	}

	type spec struct {
		label     string
		period    string
		timeframe string
	}
	specs := []spec{
		{"1 Year", "1A", "1D"},
		{"6 Months", "6M", "1D"},
		{"3 Months", "3M", "1D"},
		{"5 Days", "5D", "1D"},
		{"1 Day", "1D", "5Min"},
	}

	results := make([]PeriodResult, len(specs))
	var wg sync.WaitGroup

	for i, s := range specs {
		wg.Add(1)
		go func(idx int, s spec) {
			defer wg.Done()
			url := fmt.Sprintf(
				"%s/v2/account/portfolio/history?period=%s&timeframe=%s&extended_hours=false",
				h.baseURL, s.period, s.timeframe,
			)
			req, _ := http.NewRequest(http.MethodGet, url, nil)
			req.Header.Set("APCA-API-KEY-ID", h.apiKey)
			req.Header.Set("APCA-API-SECRET-KEY", h.secretKey)
			req.Header.Set("Accept", "application/json")

			resp, err := h.client.Do(req)
			if err != nil {
				return
			}
			defer resp.Body.Close()
			body, _ := io.ReadAll(resp.Body)

			var data struct {
				Equity []any `json:"equity"`
			}
			if err := json.Unmarshal(body, &data); err != nil {
				return
			}

			var equities []float64
			for _, e := range data.Equity {
				if e == nil {
					continue
				}
				v, ok := e.(float64)
				if ok && v > 0 {
					equities = append(equities, v)
				}
			}
			if len(equities) < 2 {
				return
			}

			start := equities[0]
			end := equities[len(equities)-1]
			pnl := end - start
			pct := 0.0
			if start > 0 {
				pct = (pnl / start) * 100
			}
			results[idx] = PeriodResult{
				Label:      s.label,
				Period:     s.period,
				StartValue: start,
				EndValue:   end,
				PnL:        pnl,
				PnLPct:     pct,
			}
		}(i, s)
	}

	wg.Wait()
	WriteJSON(w, map[string]any{"periods": results})
}

// SyncedPosition is an open Alpaca position enriched with a fill timestamp.
type SyncedPosition struct {
	Symbol        string  `json:"symbol"`
	Side          string  `json:"side"`
	Qty           float64 `json:"qty"`
	AvgEntryPrice float64 `json:"avg_entry_price"`
	EntryTimeMs   int64   `json:"entry_time_ms"` // Unix ms from fill activity; 0 if unknown
	AlpacaOrderID string  `json:"alpaca_order_id"`
}

// FetchOpenPositionsWithFills returns all open Alpaca positions enriched with
// their fill timestamp from the account activities API.
func (h *Handler) FetchOpenPositionsWithFills() ([]SyncedPosition, error) {
	if h.apiKey == "" || h.secretKey == "" {
		return nil, fmt.Errorf("alpaca credentials not configured")
	}

	// 1. Fetch open positions
	posReq, _ := http.NewRequest(http.MethodGet, h.baseURL+"/v2/positions", nil)
	posReq.Header.Set("APCA-API-KEY-ID", h.apiKey)
	posReq.Header.Set("APCA-API-SECRET-KEY", h.secretKey)
	posResp, err := h.client.Do(posReq)
	if err != nil {
		return nil, fmt.Errorf("alpaca unreachable: %w", err)
	}
	defer posResp.Body.Close()
	posBody, _ := io.ReadAll(posResp.Body)

	var rawPositions []map[string]any
	if err := json.Unmarshal(posBody, &rawPositions); err != nil || len(rawPositions) == 0 {
		return nil, nil
	}

	// 2. Fetch fill activities (up to 200 most recent) for timestamps
	actURL := h.baseURL + "/v2/account/activities?activity_type=FILL&page_size=200"
	actReq, _ := http.NewRequest(http.MethodGet, actURL, nil)
	actReq.Header.Set("APCA-API-KEY-ID", h.apiKey)
	actReq.Header.Set("APCA-API-SECRET-KEY", h.secretKey)
	actResp, err := h.client.Do(actReq)
	if err != nil {
		return nil, fmt.Errorf("alpaca activities unreachable: %w", err)
	}
	defer actResp.Body.Close()
	actBody, _ := io.ReadAll(actResp.Body)

	var activities []map[string]any
	_ = json.Unmarshal(actBody, &activities)

	// Build symbol → earliest fill timestamp map (oldest fill = position open)
	fillTimes := map[string]int64{}
	for _, act := range activities {
		sym, _ := act["symbol"].(string)
		side, _ := act["side"].(string)
		if sym == "" || strings.ToLower(side) != "buy" {
			continue
		}
		ts, _ := act["transaction_time"].(string)
		if ts == "" {
			continue
		}
		t, err := time.Parse(time.RFC3339Nano, ts)
		if err != nil {
			t, err = time.Parse("2006-01-02T15:04:05Z", ts)
			if err != nil {
				continue
			}
		}
		ms := t.UnixMilli()
		if existing, ok := fillTimes[sym]; !ok || ms < existing {
			fillTimes[sym] = ms
		}
	}

	// 3. Merge
	var result []SyncedPosition
	for _, p := range rawPositions {
		sym, _ := p["symbol"].(string)
		side, _ := p["side"].(string)
		var qty, entry float64
		fmt.Sscanf(fmt.Sprintf("%v", p["qty"]), "%f", &qty)
		fmt.Sscanf(fmt.Sprintf("%v", p["avg_entry_price"]), "%f", &entry)
		orderID, _ := p["asset_id"].(string)
		result = append(result, SyncedPosition{
			Symbol:        sym,
			Side:          side,
			Qty:           qty,
			AvgEntryPrice: entry,
			EntryTimeMs:   fillTimes[sym],
			AlpacaOrderID: orderID,
		})
	}
	return result, nil
}

// EquityPoint is one data point in the portfolio equity curve.
type EquityPoint struct {
	Timestamp int64   `json:"timestamp"` // Unix ms
	Equity    float64 `json:"equity"`
}

// EquityHistory returns a time-series of portfolio equity values.
// GET /api/alpaca/equity-history?period=1M&timeframe=1D
func (h *Handler) EquityHistory(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	if h.apiKey == "" || h.secretKey == "" {
		http.Error(w, "Alpaca credentials not configured", http.StatusServiceUnavailable)
		return
	}

	period := r.URL.Query().Get("period")
	if period == "" {
		period = "1M"
	}
	timeframe := r.URL.Query().Get("timeframe")
	if timeframe == "" {
		timeframe = "1D"
	}

	url := fmt.Sprintf(
		"%s/v2/account/portfolio/history?period=%s&timeframe=%s&extended_hours=false",
		h.baseURL, period, timeframe,
	)
	req, err := http.NewRequest(http.MethodGet, url, nil)
	if err != nil {
		http.Error(w, "request build error", http.StatusInternalServerError)
		return
	}
	req.Header.Set("APCA-API-KEY-ID", h.apiKey)
	req.Header.Set("APCA-API-SECRET-KEY", h.secretKey)
	req.Header.Set("Accept", "application/json")

	resp, err := h.client.Do(req)
	if err != nil {
		http.Error(w, "alpaca unreachable", http.StatusBadGateway)
		return
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(resp.Body)

	var raw struct {
		Timestamp []int64 `json:"timestamp"`
		Equity    []any   `json:"equity"`
		BaseValue float64 `json:"base_value"`
		Timeframe string  `json:"timeframe"`
	}
	if err := json.Unmarshal(body, &raw); err != nil {
		http.Error(w, "parse error", http.StatusInternalServerError)
		return
	}

	var points []EquityPoint
	for i, ts := range raw.Timestamp {
		if i >= len(raw.Equity) {
			break
		}
		eq := raw.Equity[i]
		if eq == nil {
			continue
		}
		v, ok := eq.(float64)
		if !ok || v <= 0 {
			continue
		}
		points = append(points, EquityPoint{Timestamp: ts * 1000, Equity: v})
	}

	WriteJSON(w, map[string]any{
		"points":     points,
		"base_value": raw.BaseValue,
	})
}

// WriteJSON is a shared helper for JSON responses within this package.
func WriteJSON(w http.ResponseWriter, v any) {
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(v)
}
