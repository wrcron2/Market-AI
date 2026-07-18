package askai

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"strings"
	"sync"
	"time"

	"go.uber.org/zap"

	"github.com/marketflow/backend/internal/db"
	"github.com/marketflow/backend/internal/notify"
	"github.com/marketflow/backend/internal/ws"
)

type Handler struct {
	db       *db.DB
	logger   *zap.Logger
	client   *http.Client
	hub      *ws.Hub
	notifier *notify.Notifier

	getAutoExec func() bool
	getMode     func() string

	mu             sync.RWMutex
	fallbackActive bool
	fallbackSince  time.Time
	failCount      int
	lastFailTime   time.Time
	failedProvider string
	probeStop      chan struct{}
}

const fallbackThreshold = 3
const fallbackWindow = 60 * time.Second

func NewHandler(database *db.DB, logger *zap.Logger, hub *ws.Hub, notifier *notify.Notifier, getAutoExec func() bool, getMode func() string) *Handler {
	h := &Handler{
		db:          database,
		logger:      logger,
		client:      &http.Client{Timeout: 20 * time.Minute},
		hub:         hub,
		notifier:    notifier,
		getAutoExec: getAutoExec,
		getMode:     getMode,
	}
	h.checkMissingKeys()
	return h
}

func (h *Handler) checkMissingKeys() {
	var missing []string
	if os.Getenv("ANTHROPIC_API_KEY") == "" {
		missing = append(missing, "ANTHROPIC_API_KEY")
	}
	if os.Getenv("GROQ_API_KEY") == "" {
		missing = append(missing, "GROQ_API_KEY")
	}
	if len(missing) > 0 {
		title := "Missing API keys: " + strings.Join(missing, ", ")
		body := "Cloud LLM providers unavailable. Ask AI and signal pipeline will fall back to local Ollama (~7-10 min per call on CPU). Add the keys to .env and restart."
		h.logger.Warn("askai.missing_keys", zap.Strings("keys", missing))
		_ = h.db.InsertAlert("HIGH", title, body)
		if h.hub != nil {
			h.hub.Broadcast("alert", map[string]any{"severity": "HIGH", "title": title, "body": body})
		}
		if h.notifier != nil {
			_ = h.notifier.Send("HIGH", title, body)
		}
	}
}

func (h *Handler) IsFallbackActive() bool {
	h.mu.RLock()
	defer h.mu.RUnlock()
	return h.fallbackActive
}

func (h *Handler) recordCloudFailure(provider string, callErr error) {
	h.mu.Lock()
	defer h.mu.Unlock()

	now := time.Now()
	if now.Sub(h.lastFailTime) > fallbackWindow {
		h.failCount = 0
	}
	h.failCount++
	h.lastFailTime = now

	if h.failCount >= fallbackThreshold && !h.fallbackActive {
		h.fallbackActive = true
		h.fallbackSince = now
		h.failedProvider = provider
		title := fmt.Sprintf("LLM Fallback Active — %s unreachable", provider)
		body := fmt.Sprintf("Cloud provider failed %d times in %s. Falling back to local Ollama (~7-10 min/call). Signal pipeline PAUSED. Last error: %s",
			h.failCount, fallbackWindow, callErr)
		h.logger.Error("askai.fallback_activated", zap.String("provider", provider), zap.Error(callErr))
		_ = h.db.InsertAlert("CRITICAL", title, body)
		if h.hub != nil {
			h.hub.Broadcast("alert", map[string]any{"severity": "CRITICAL", "title": title, "body": body})
			h.hub.Broadcast("llm_fallback", map[string]any{"active": true, "provider": provider, "since": now.Format(time.RFC3339)})
		}
		if h.notifier != nil {
			_ = h.notifier.Send("CRITICAL", title, body)
		}
		h.startHealthProbe()
	}
}

func (h *Handler) recordCloudSuccess() {
	h.mu.Lock()
	defer h.mu.Unlock()

	h.failCount = 0
	if h.fallbackActive {
		h.fallbackActive = false
		h.stopHealthProbe()
		title := "LLM Cloud Provider Restored"
		body := fmt.Sprintf("Cloud inference recovered after fallback since %s. Signal pipeline resumed. Pipeline operating at full speed.", h.fallbackSince.Format(time.RFC3339))
		h.logger.Info("askai.fallback_cleared")
		_ = h.db.InsertAlert("INFO", title, body)
		if h.hub != nil {
			h.hub.Broadcast("alert", map[string]any{"severity": "INFO", "title": title, "body": body})
			h.hub.Broadcast("llm_fallback", map[string]any{"active": false})
		}
		if h.notifier != nil {
			_ = h.notifier.Send("INFO", title, body)
		}
	}
}

type contextSnapshot struct {
	PortfolioValue float64        `json:"portfolio_value"`
	TodayPnl       float64        `json:"today_pnl"`
	OpenPositions  []*db.Position `json:"open_positions"`
	PendingSignals int            `json:"pending_signals"`
	AutoExecute    bool           `json:"auto_execute"`
	MarketStatus   string         `json:"market_status"`
	TradingMode    string         `json:"trading_mode"`
	TotalSignals   int            `json:"total_signals"`
	Approved       int            `json:"approved"`
	Rejected       int            `json:"rejected"`
	Executed       int            `json:"executed"`
}

func (h *Handler) ContextSnapshot(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}

	positions, _ := h.db.ListOpenPositions()
	stats, _ := h.db.GetStats()
	limits, _ := h.db.GetTodayLimits()

	_, pendingCount, _ := h.db.ListByStatus(db.StatusPending, 1, 0)

	todayPnl := 0.0
	if limits != nil {
		todayPnl = limits.RealizedPnl
	}

	marketStatus := "closed"
	now := time.Now().In(easternTime())
	hour := now.Hour()
	min := now.Minute()
	mins := hour*60 + min
	if now.Weekday() >= time.Monday && now.Weekday() <= time.Friday && mins >= 570 && mins < 960 {
		marketStatus = "open"
	}

	snap := contextSnapshot{
		TodayPnl:       todayPnl,
		OpenPositions:  positions,
		PendingSignals: pendingCount,
		AutoExecute:    h.getAutoExec(),
		MarketStatus:   marketStatus,
		TradingMode:    h.getMode(),
	}

	if stats != nil {
		snap.TotalSignals = stats.TotalSignals
		snap.Approved = stats.Approved
		snap.Rejected = stats.Rejected
		snap.Executed = stats.Executed
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(snap)
}

var roleSystemPrompts = map[string]string{
	"Chief PM":         `You are the Chief PM of MarketFlow AI, an automated trading system. Answer concisely using the live system data below.`,
	"Engineering":      `You are a senior engineer for MarketFlow AI (React + Go + Python LangGraph + Ollama + Alpaca). Give concrete technical answers.`,
	"Risk Analyst":     `You are a quant risk analyst for MarketFlow AI. Analyze positions, sizing, drawdown. Use numbers from the live data below.`,
	"Strategy Advisor": `You are a trading strategy advisor for MarketFlow AI (momentum breakout + mean reversion). Use the live data to advise.`,
}

var roleSystemPromptsFull = map[string]string{
	"Chief PM": `You are the Chief PM Orchestrator of MarketFlow AI — an Elite Fintech Product Manager, Trading Systems Architect, and Regulatory Compliance Authority. You coordinate all departments and serve as the final decision authority. Speak with authority. Answer in structured PRD format when appropriate. Reference the live system data provided in context.`,

	"Engineering": `You are a senior backend/infrastructure engineer for MarketFlow AI. You understand the full stack: React 19 frontend, Go 1.24 backend, Python 3.12 LangGraph AI brain, Ollama for local inference, Alpaca for execution. Give concrete, actionable technical answers. Reference file paths and code patterns when relevant.`,

	"Risk Analyst": `You are a quantitative risk analyst for MarketFlow AI. You specialize in position sizing (ATR-based, 1% account risk), drawdown analysis, VIX regime detection, and portfolio risk metrics. Analyze the live system data provided and give specific risk assessments. Use numbers, not generalities.`,

	"Strategy Advisor": `You are a trading strategy advisor for MarketFlow AI. You specialize in momentum breakout (MACD + volume + SMA20) and mean reversion (RSI + Bollinger %B) strategies. Evaluate signal quality, backtest interpretation, and entry/exit rules. Reference the live data to give specific recommendations.`,
}

type askRequest struct {
	Role     string `json:"role"`
	Question string `json:"question"`
	Model    string `json:"model"`
}

type askResponse struct {
	Reply    string `json:"reply"`
	Model    string `json:"model"`
	Fallback bool   `json:"fallback,omitempty"`
}

func (h *Handler) Ask(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}

	var req askRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, "invalid body", http.StatusBadRequest)
		return
	}

	if req.Question == "" {
		http.Error(w, "question is required", http.StatusBadRequest)
		return
	}
	if req.Model == "" {
		req.Model = "claude-sonnet"
	}

	snapJSON := h.buildContextJSON()
	sp := roleSystemPromptsFull[req.Role]
	if sp == "" {
		sp = roleSystemPromptsFull["Engineering"]
	}
	fullSystem := sp + "\n\n## Live System State\n```json\n" + snapJSON + "\n```"

	var reply string
	var err error
	usedFallback := false

	switch {
	case strings.HasPrefix(req.Model, "claude"):
		reply, err = h.callClaude(fullSystem, req.Question)
		if err != nil {
			h.recordCloudFailure("Anthropic", err)
		} else {
			h.recordCloudSuccess()
		}

	case req.Model == "deepseek-r1" || req.Model == "qwen3":
		groqKey := os.Getenv("GROQ_API_KEY")
		if groqKey != "" {
			reply, err = h.callGroq(groqKey, "llama-3.3-70b-versatile", fullSystem, req.Question)
			if err != nil {
				h.recordCloudFailure("Groq", err)
				h.logger.Warn("askai.groq_failed_fallback_ollama", zap.Error(err))
				spShort := roleSystemPrompts[req.Role]
				if spShort == "" {
					spShort = roleSystemPrompts["Engineering"]
				}
				shortSystem := spShort + "\n" + h.buildMiniContext()
				reply, err = h.callOllama("deepseek-r1:7b", shortSystem, req.Question)
				usedFallback = true
			} else {
				h.recordCloudSuccess()
			}
		} else {
			spShort := roleSystemPrompts[req.Role]
			if spShort == "" {
				spShort = roleSystemPrompts["Engineering"]
			}
			shortSystem := spShort + "\n" + h.buildMiniContext()
			reply, err = h.callOllama("deepseek-r1:7b", shortSystem, req.Question)
			usedFallback = true
		}

	case req.Model == "glm-5.2":
		nvidiaKey := os.Getenv("NVIDIA_API_KEY")
		if nvidiaKey != "" {
			reply, err = h.callNvidia(nvidiaKey, "z-ai/glm-5.2", fullSystem, req.Question)
			if err != nil {
				h.recordCloudFailure("NVIDIA", err)
				h.logger.Warn("askai.nvidia_failed_fallback_ollama", zap.Error(err))
				spShort := roleSystemPrompts[req.Role]
				if spShort == "" {
					spShort = roleSystemPrompts["Engineering"]
				}
				shortSystem := spShort + "\n" + h.buildMiniContext()
				reply, err = h.callOllama("qwen3:4b", shortSystem, req.Question)
				usedFallback = true
			} else {
				h.recordCloudSuccess()
			}
		} else {
			spShort := roleSystemPrompts[req.Role]
			if spShort == "" {
				spShort = roleSystemPrompts["Engineering"]
			}
			shortSystem := spShort + "\n" + h.buildMiniContext()
			reply, err = h.callOllama("qwen3:4b", shortSystem, req.Question)
			usedFallback = true
		}

	default:
		spShort := roleSystemPrompts[req.Role]
		if spShort == "" {
			spShort = roleSystemPrompts["Engineering"]
		}
		shortSystem := spShort + "\n" + h.buildMiniContext()
		reply, err = h.callOllama("qwen3:4b", shortSystem, req.Question)
	}

	if err != nil {
		h.logger.Error("ask-ai.llm_error", zap.String("model", req.Model), zap.Error(err))
		http.Error(w, "LLM request failed: "+err.Error(), http.StatusBadGateway)
		return
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(askResponse{Reply: reply, Model: req.Model, Fallback: usedFallback})
}

func (h *Handler) buildMiniContext() string {
	positions, _ := h.db.ListOpenPositions()
	stats, _ := h.db.GetStats()
	limits, _ := h.db.GetTodayLimits()

	parts := []string{fmt.Sprintf("Positions: %d open", len(positions))}
	if stats != nil {
		parts = append(parts, fmt.Sprintf("Signals: %d total, %d approved, %d executed", stats.TotalSignals, stats.Approved, stats.Executed))
	}
	if limits != nil {
		parts = append(parts, fmt.Sprintf("PnL today: $%.2f", limits.RealizedPnl))
	}
	parts = append(parts, fmt.Sprintf("Auto-execute: %v, Mode: %s", h.getAutoExec(), h.getMode()))
	return strings.Join(parts, ". ") + "."
}

func (h *Handler) buildContextJSON() string {
	positions, _ := h.db.ListOpenPositions()
	stats, _ := h.db.GetStats()
	limits, _ := h.db.GetTodayLimits()

	snap := map[string]any{
		"auto_execute":   h.getAutoExec(),
		"trading_mode":   h.getMode(),
		"open_positions": len(positions),
	}
	if stats != nil {
		snap["total_signals"] = stats.TotalSignals
		snap["approved"] = stats.Approved
		snap["rejected"] = stats.Rejected
		snap["executed"] = stats.Executed
	}
	if limits != nil {
		snap["today_pnl"] = limits.RealizedPnl
	}

	posNames := make([]string, 0, len(positions))
	for _, p := range positions {
		posNames = append(posNames, fmt.Sprintf("%s (%s, qty %.0f)", p.Symbol, p.Direction, p.Quantity))
	}
	if len(posNames) > 0 {
		snap["positions_detail"] = posNames
	}

	b, _ := json.MarshalIndent(snap, "", "  ")
	return string(b)
}

func (h *Handler) callClaude(system, question string) (string, error) {
	apiKey := os.Getenv("ANTHROPIC_API_KEY")
	if apiKey == "" {
		return "", fmt.Errorf("ANTHROPIC_API_KEY not set — configure it in .env to use Claude")
	}

	body := map[string]any{
		"model":      "claude-sonnet-4-6",
		"max_tokens": 2048,
		"system":     system,
		"messages": []map[string]string{
			{"role": "user", "content": question},
		},
	}
	b, _ := json.Marshal(body)

	req, _ := http.NewRequest("POST", "https://api.anthropic.com/v1/messages", bytes.NewReader(b))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("x-api-key", apiKey)
	req.Header.Set("anthropic-version", "2023-06-01")

	resp, err := h.client.Do(req)
	if err != nil {
		return "", fmt.Errorf("claude request failed: %w", err)
	}
	defer resp.Body.Close()

	respBody, _ := io.ReadAll(resp.Body)
	if resp.StatusCode != 200 {
		return "", fmt.Errorf("claude API error %d: %s", resp.StatusCode, string(respBody))
	}

	var result struct {
		Content []struct {
			Text string `json:"text"`
		} `json:"content"`
	}
	if err := json.Unmarshal(respBody, &result); err != nil {
		return "", fmt.Errorf("failed to parse claude response: %w", err)
	}
	if len(result.Content) == 0 {
		return "", fmt.Errorf("empty claude response")
	}
	return result.Content[0].Text, nil
}

func (h *Handler) callOllama(model, system, question string) (string, error) {
	ollamaHost := os.Getenv("OLLAMA_HOST")
	if ollamaHost == "" {
		ollamaHost = "http://127.0.0.1:11434"
	}
	ollamaHost = strings.TrimRight(ollamaHost, "/")

	body := map[string]any{
		"model":  model,
		"stream": false,
		"options": map[string]any{
			"num_ctx": 8192,
		},
		"messages": []map[string]string{
			{"role": "system", "content": system},
			{"role": "user", "content": question},
		},
	}
	b, _ := json.Marshal(body)

	resp, err := h.client.Post(ollamaHost+"/api/chat", "application/json", bytes.NewReader(b))
	if err != nil {
		return "", fmt.Errorf("ollama request failed: %w", err)
	}
	defer resp.Body.Close()

	respBody, _ := io.ReadAll(resp.Body)
	if resp.StatusCode != 200 {
		return "", fmt.Errorf("ollama error %d: %s", resp.StatusCode, string(respBody))
	}

	var result struct {
		Message struct {
			Content string `json:"content"`
		} `json:"message"`
	}
	if err := json.Unmarshal(respBody, &result); err != nil {
		return "", fmt.Errorf("failed to parse ollama response: %w", err)
	}
	return result.Message.Content, nil
}

func (h *Handler) callGroq(apiKey, model, system, question string) (string, error) {
	body := map[string]any{
		"model":      model,
		"max_tokens": 2048,
		"messages": []map[string]string{
			{"role": "system", "content": system},
			{"role": "user", "content": question},
		},
	}
	b, _ := json.Marshal(body)

	req, _ := http.NewRequest("POST", "https://api.groq.com/openai/v1/chat/completions", bytes.NewReader(b))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer "+apiKey)

	resp, err := h.client.Do(req)
	if err != nil {
		return "", fmt.Errorf("groq request failed: %w", err)
	}
	defer resp.Body.Close()

	respBody, _ := io.ReadAll(resp.Body)
	if resp.StatusCode != 200 {
		return "", fmt.Errorf("groq API error %d: %s", resp.StatusCode, string(respBody))
	}

	var result struct {
		Choices []struct {
			Message struct {
				Content string `json:"content"`
			} `json:"message"`
		} `json:"choices"`
	}
	if err := json.Unmarshal(respBody, &result); err != nil {
		return "", fmt.Errorf("failed to parse groq response: %w", err)
	}
	if len(result.Choices) == 0 {
		return "", fmt.Errorf("empty groq response")
	}
	return result.Choices[0].Message.Content, nil
}

// callNvidia hits NVIDIA's OpenAI-compatible chat completions endpoint
// (integrate.api.nvidia.com) — same request/response shape as Groq.
func (h *Handler) callNvidia(apiKey, model, system, question string) (string, error) {
	body := map[string]any{
		"model":      model,
		"max_tokens": 2048,
		"messages": []map[string]string{
			{"role": "system", "content": system},
			{"role": "user", "content": question},
		},
	}
	b, _ := json.Marshal(body)

	req, _ := http.NewRequest("POST", "https://integrate.api.nvidia.com/v1/chat/completions", bytes.NewReader(b))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer "+apiKey)

	resp, err := h.client.Do(req)
	if err != nil {
		return "", fmt.Errorf("nvidia request failed: %w", err)
	}
	defer resp.Body.Close()

	respBody, _ := io.ReadAll(resp.Body)
	if resp.StatusCode != 200 {
		return "", fmt.Errorf("nvidia API error %d: %s", resp.StatusCode, string(respBody))
	}

	var result struct {
		Choices []struct {
			Message struct {
				Content string `json:"content"`
			} `json:"message"`
		} `json:"choices"`
	}
	if err := json.Unmarshal(respBody, &result); err != nil {
		return "", fmt.Errorf("failed to parse nvidia response: %w", err)
	}
	if len(result.Choices) == 0 {
		return "", fmt.Errorf("empty nvidia response")
	}
	return result.Choices[0].Message.Content, nil
}

// PipelinePause returns whether the signal pipeline should pause.
func (h *Handler) PipelinePause(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	h.mu.RLock()
	active := h.fallbackActive
	since := h.fallbackSince
	provider := h.failedProvider
	h.mu.RUnlock()

	resp := map[string]any{"paused": active}
	if active {
		resp["reason"] = fmt.Sprintf("Cloud provider %s unreachable since %s — local Ollama too slow for live signals", provider, since.Format(time.RFC3339))
		resp["since"] = since.Format(time.RFC3339)
	}
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(resp)
}

// startHealthProbe launches a background goroutine that probes the failed
// cloud provider every 60s. When it responds, fallback auto-clears.
// Must be called with h.mu held.
func (h *Handler) startHealthProbe() {
	if h.probeStop != nil {
		return
	}
	h.probeStop = make(chan struct{})
	provider := h.failedProvider
	h.logger.Info("askai.health_probe_started", zap.String("provider", provider))

	go func() {
		ticker := time.NewTicker(60 * time.Second)
		defer ticker.Stop()
		probeClient := &http.Client{Timeout: 15 * time.Second}

		for {
			select {
			case <-h.probeStop:
				return
			case <-ticker.C:
				if h.probeProvider(probeClient, provider) {
					h.logger.Info("askai.health_probe_recovered", zap.String("provider", provider))
					h.recordCloudSuccess()
					return
				}
				h.logger.Debug("askai.health_probe_still_down", zap.String("provider", provider))
			}
		}
	}()
}

// stopHealthProbe signals the probe goroutine to exit. Must be called with h.mu held.
func (h *Handler) stopHealthProbe() {
	if h.probeStop != nil {
		close(h.probeStop)
		h.probeStop = nil
	}
}

// probeProvider makes a lightweight test call to the given cloud provider.
func (h *Handler) probeProvider(client *http.Client, provider string) bool {
	switch provider {
	case "Groq":
		key := os.Getenv("GROQ_API_KEY")
		if key == "" {
			return false
		}
		body := `{"model":"llama-3.3-70b-versatile","max_tokens":1,"messages":[{"role":"user","content":"hi"}]}`
		req, _ := http.NewRequest("POST", "https://api.groq.com/openai/v1/chat/completions", strings.NewReader(body))
		req.Header.Set("Content-Type", "application/json")
		req.Header.Set("Authorization", "Bearer "+key)
		resp, err := client.Do(req)
		if err != nil {
			return false
		}
		resp.Body.Close()
		return resp.StatusCode == 200

	case "Anthropic":
		key := os.Getenv("ANTHROPIC_API_KEY")
		if key == "" {
			return false
		}
		body := `{"model":"claude-sonnet-4-6","max_tokens":1,"messages":[{"role":"user","content":"hi"}]}`
		req, _ := http.NewRequest("POST", "https://api.anthropic.com/v1/messages", strings.NewReader(body))
		req.Header.Set("Content-Type", "application/json")
		req.Header.Set("x-api-key", key)
		req.Header.Set("anthropic-version", "2023-06-01")
		resp, err := client.Do(req)
		if err != nil {
			return false
		}
		resp.Body.Close()
		return resp.StatusCode == 200

	case "NVIDIA":
		key := os.Getenv("NVIDIA_API_KEY")
		if key == "" {
			return false
		}
		body := `{"model":"z-ai/glm-5.2","max_tokens":1,"messages":[{"role":"user","content":"hi"}]}`
		req, _ := http.NewRequest("POST", "https://integrate.api.nvidia.com/v1/chat/completions", strings.NewReader(body))
		req.Header.Set("Content-Type", "application/json")
		req.Header.Set("Authorization", "Bearer "+key)
		resp, err := client.Do(req)
		if err != nil {
			return false
		}
		resp.Body.Close()
		return resp.StatusCode == 200
	}
	return false
}

func easternTime() *time.Location {
	loc, err := time.LoadLocation("America/New_York")
	if err != nil {
		return time.UTC
	}
	return loc
}
