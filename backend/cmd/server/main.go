package main

import (
	"context"
	"encoding/json"
	"fmt"
	"log"
	"net"
	"net/http"
	"os"
	"os/signal"
	"strconv"
	"sync"
	"syscall"
	"time"

	"github.com/joho/godotenv"
	"go.uber.org/zap"
	"google.golang.org/grpc"

	"github.com/marketflow/backend/internal/alpaca"
	"github.com/marketflow/backend/internal/db"
	"github.com/marketflow/backend/internal/greenlight"
	grpcbridge "github.com/marketflow/backend/internal/grpc"
	"github.com/marketflow/backend/internal/mode"
	"github.com/marketflow/backend/internal/ws"
	proto "github.com/marketflow/backend/proto"
)

func main() {
	// Load .env (ignore error if not present — env vars may be set externally)
	_ = godotenv.Load()

	logger, err := zap.NewProduction()
	if err != nil {
		log.Fatalf("failed to init logger: %v", err)
	}
	defer logger.Sync()

	// ─── Database ─────────────────────────────────────────────────────────────
	dsn := getEnv("DB_DSN", "./infra/db/marketflow.db")
	database, err := db.Open(dsn)
	if err != nil {
		logger.Fatal("failed to open database", zap.Error(err))
	}
	defer database.Close()

	if err := database.Migrate(); err != nil {
		logger.Fatal("database migration failed", zap.Error(err))
	}
	logger.Info("database ready", zap.String("dsn", dsn))

	// ─── Trading Mode Manager (Yahoo simulation ↔ IBKR live) ─────────────────
	modeManager := mode.NewManager(logger)
	logger.Info("trading mode initialised", zap.String("mode", string(modeManager.Get())))

	// ─── WebSocket Hub ────────────────────────────────────────────────────────
	hub := ws.NewHub(logger)
	go hub.Run()

	// ─── Green Light HTTP Handler ─────────────────────────────────────────────
	glHandler    := greenlight.NewHandler(database, hub, modeManager, logger)
	alpacaProxy  := alpaca.NewHandler()

	// ─── Auto-Execute toggle (in-memory, resets to false on restart) ──────────
	var autoExMu   sync.RWMutex
	autoExEnabled := false

	mux := http.NewServeMux()
	mux.HandleFunc("/api/orders/pending", glHandler.ListPending)
	mux.HandleFunc("/api/orders/approve", glHandler.Approve)
	mux.HandleFunc("/api/orders/reject", glHandler.Reject)

	// ─── Auto-Execute: Python calls this after placing an Alpaca order ────────
	// Transitions: PENDING → APPROVED → EXECUTED (bypasses IBKR).
	mux.HandleFunc("/api/orders/auto-execute", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
			return
		}
		var req struct {
			SignalID      string  `json:"signal_id"`
			AlpacaOrderID string  `json:"alpaca_order_id"`
			FillPrice     float64 `json:"fill_price"`
		}
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			http.Error(w, "invalid body", http.StatusBadRequest)
			return
		}
		if err := database.TransitionStatus(req.SignalID, db.StatusApproved, "auto-executor", "Auto-executed via Alpaca"); err != nil {
			logger.Error("auto-execute approve failed", zap.Error(err))
			http.Error(w, err.Error(), http.StatusConflict)
			return
		}
		if req.AlpacaOrderID != "" {
			_ = database.SetIBKROrderID(req.SignalID, 0) // keeps the field non-null
		}
		if err := database.TransitionStatus(req.SignalID, db.StatusExecuted, "auto-executor", fmt.Sprintf("Alpaca order %s filled @ %.4f", req.AlpacaOrderID, req.FillPrice)); err != nil {
			logger.Error("auto-execute execute failed", zap.Error(err))
			http.Error(w, err.Error(), http.StatusConflict)
			return
		}
		hub.Broadcast("order_executed", map[string]any{
			"signal_id":      req.SignalID,
			"alpaca_order_id": req.AlpacaOrderID,
			"auto_executed":  true,
		})
		writeJSON(w, map[string]any{"success": true, "signal_id": req.SignalID})
	})

	// ─── Positions ────────────────────────────────────────────────────────────
	mux.HandleFunc("/api/positions", func(w http.ResponseWriter, r *http.Request) {
		switch r.Method {
		case http.MethodGet:
			positions, err := database.ListOpenPositions()
			if err != nil {
				logger.Error("list positions failed", zap.Error(err))
				http.Error(w, "internal error", http.StatusInternalServerError)
				return
			}
			writeJSON(w, map[string]any{"positions": positions})

		case http.MethodPost:
			var p db.Position
			if err := json.NewDecoder(r.Body).Decode(&p); err != nil {
				http.Error(w, "invalid body", http.StatusBadRequest)
				return
			}
			if err := database.OpenPosition(&p); err != nil {
				logger.Error("open position failed", zap.Error(err))
				http.Error(w, "internal error", http.StatusInternalServerError)
				return
			}
			hub.Broadcast("position_opened", p)
			writeJSON(w, map[string]any{"success": true, "id": p.ID})

		default:
			http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		}
	})

	mux.HandleFunc("/api/positions/{id}/close", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
			return
		}
		id := r.PathValue("id")
		var req struct {
			ExitPrice   float64 `json:"exit_price"`
			RealizedPnl float64 `json:"realized_pnl"`
			Reason      string  `json:"reason"`
		}
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			http.Error(w, "invalid body", http.StatusBadRequest)
			return
		}
		if err := database.ClosePosition(id, req.ExitPrice, req.RealizedPnl, req.Reason); err != nil {
			logger.Error("close position failed", zap.Error(err))
			http.Error(w, "internal error", http.StatusInternalServerError)
			return
		}
		dailyLimit, _ := strconv.ParseFloat(getEnv("DAILY_LOSS_LIMIT_USD", "1000.0"), 64)
		_ = database.UpdateTodayPnl(req.RealizedPnl, dailyLimit)
		hub.Broadcast("position_closed", map[string]any{
			"id":           id,
			"realized_pnl": req.RealizedPnl,
			"reason":       req.Reason,
		})
		writeJSON(w, map[string]any{"success": true})
	})

	// ─── Trading limits ───────────────────────────────────────────────────────
	mux.HandleFunc("/api/trading/limits", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodGet {
			http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
			return
		}
		limits, err := database.GetTodayLimits()
		if err != nil {
			http.Error(w, "internal error", http.StatusInternalServerError)
			return
		}
		writeJSON(w, limits)
	})

	// ─── Alpaca proxy (read-only, for dashboard) ──────────────────────────────
	mux.HandleFunc("/api/alpaca/account",   alpacaProxy.Account)
	mux.HandleFunc("/api/alpaca/positions", alpacaProxy.Positions)

	// ─── Internal broadcast (Python brain → dashboard via WebSocket) ──────────
	// Only the brain calls this. No CORS check needed (same host).
	mux.HandleFunc("/api/ws/broadcast", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
			return
		}
		var msg struct {
			Type    string `json:"type"`
			Payload any    `json:"payload"`
		}
		if err := json.NewDecoder(r.Body).Decode(&msg); err != nil {
			http.Error(w, "invalid body", http.StatusBadRequest)
			return
		}
		hub.Broadcast(msg.Type, msg.Payload)
		writeJSON(w, map[string]any{"ok": true})
	})

	// ─── Auto-Execute toggle ──────────────────────────────────────────────────
	mux.HandleFunc("/api/auto-execute", func(w http.ResponseWriter, r *http.Request) {
		switch r.Method {
		case http.MethodGet:
			autoExMu.RLock()
			enabled := autoExEnabled
			autoExMu.RUnlock()
			writeJSON(w, map[string]any{"enabled": enabled})

		case http.MethodPost:
			var req struct{ Enabled bool `json:"enabled"` }
			if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
				http.Error(w, "invalid body", http.StatusBadRequest)
				return
			}
			autoExMu.Lock()
			autoExEnabled = req.Enabled
			autoExMu.Unlock()
			hub.Broadcast("auto_execute_changed", map[string]any{"enabled": req.Enabled})
			logger.Info("auto-execute toggled", zap.Bool("enabled", req.Enabled))
			writeJSON(w, map[string]any{"enabled": req.Enabled})

		default:
			http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		}
	})
	mux.HandleFunc("/api/mode", func(w http.ResponseWriter, r *http.Request) {
		switch r.Method {
		case http.MethodGet:
			modeManager.GetHandler(w, r)
		case http.MethodPost:
			modeManager.SetHandler(w, r)
			// Broadcast mode change to all dashboard clients via WebSocket
			hub.Broadcast("mode_changed", map[string]string{"mode": string(modeManager.Get())})
		default:
			http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		}
	})
	mux.HandleFunc("/ws", hub.ServeWS)
	mux.HandleFunc("/healthz", func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
		fmt.Fprint(w, "ok")
	})

	// ─── REST Signal Submission (Python AI Brain → Backend) ───────────────────
	// Accepts signals from the Python brain via HTTP/JSON.
	// This is simpler than gRPC for local development and requires no proto stubs.
	mux.HandleFunc("/api/signals", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
			return
		}

		var req struct {
			SignalID     string  `json:"signal_id"`
			Symbol       string  `json:"symbol"`
			Direction    string  `json:"direction"`
			Quantity     float64 `json:"quantity"`
			LimitPrice   float64 `json:"limit_price"`
			Confidence   float64 `json:"confidence"`
			Reasoning    string  `json:"reasoning"`
			StrategyName string  `json:"strategy_name"`
			ModelUsed    string  `json:"model_used"`
		}

		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			http.Error(w, "invalid JSON: "+err.Error(), http.StatusBadRequest)
			return
		}

		// Enforce confidence gate
		minConf := 0.90
		if s := os.Getenv("MIN_SIGNAL_CONFIDENCE"); s != "" {
			if v, err := strconv.ParseFloat(s, 64); err == nil {
				minConf = v
			}
		}

		writeJSON := func(v any) {
			w.Header().Set("Content-Type", "application/json")
			json.NewEncoder(w).Encode(v)
		}

		if req.Confidence < minConf {
			writeJSON(map[string]any{
				"signal_id": req.SignalID,
				"accepted":  false,
				"message":   fmt.Sprintf("confidence %.2f below threshold %.2f", req.Confidence, minConf),
			})
			return
		}

		order := &db.StagedOrder{
			ID:           req.SignalID,
			Symbol:       req.Symbol,
			Direction:    req.Direction,
			Quantity:     req.Quantity,
			LimitPrice:   req.LimitPrice,
			Confidence:   req.Confidence,
			Reasoning:    req.Reasoning,
			StrategyName: req.StrategyName,
			ModelUsed:    req.ModelUsed,
		}

		if err := database.StageOrder(order); err != nil {
			logger.Error("failed to stage order via REST", zap.Error(err))
			http.Error(w, "internal error", http.StatusInternalServerError)
			return
		}

		hub.Broadcast("order_staged", order)
		logger.Info("signal staged via REST", zap.String("signal_id", req.SignalID), zap.String("symbol", req.Symbol))

		writeJSON(map[string]any{
			"signal_id": req.SignalID,
			"accepted":  true,
			"message":   "staged successfully",
		})
	})

	httpPort := getEnv("GO_SERVER_PORT", "8080")
	httpSrv := &http.Server{
		Addr:         ":" + httpPort,
		Handler:      corsMiddleware(mux),
		ReadTimeout:  10 * time.Second,
		WriteTimeout: 30 * time.Second,
	}

	// ─── gRPC Server (Signal ingestion from AI Brain) ─────────────────────────
	grpcPort := getEnv("GO_GRPC_PORT", "50051")
	lis, err := net.Listen("tcp", ":"+grpcPort)
	if err != nil {
		logger.Fatal("failed to listen on gRPC port", zap.Error(err))
	}

	grpcSrv := grpc.NewServer()
	signalSvc := grpcbridge.NewSignalServer(database, hub, logger)
	proto.RegisterSignalServiceServer(grpcSrv, signalSvc)
	proto.RegisterGreenLightServiceServer(grpcSrv, grpcbridge.NewGreenLightServer(database, hub, logger))

	// ─── Start servers ────────────────────────────────────────────────────────
	go func() {
		logger.Info("gRPC server listening", zap.String("port", grpcPort))
		if err := grpcSrv.Serve(lis); err != nil {
			logger.Error("gRPC server error", zap.Error(err))
		}
	}()

	go func() {
		logger.Info("HTTP server listening", zap.String("port", httpPort))
		if err := httpSrv.ListenAndServe(); err != nil && err != http.ErrServerClosed {
			logger.Error("HTTP server error", zap.Error(err))
		}
	}()

	// ─── Graceful shutdown ────────────────────────────────────────────────────
	quit := make(chan os.Signal, 1)
	signal.Notify(quit, syscall.SIGINT, syscall.SIGTERM)
	<-quit
	logger.Info("shutting down...")

	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()

	grpcSrv.GracefulStop()
	if err := httpSrv.Shutdown(ctx); err != nil {
		logger.Error("HTTP shutdown error", zap.Error(err))
	}
	logger.Info("server stopped")
}

func getEnv(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}

func writeJSON(w http.ResponseWriter, v any) {
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(v)
}

// corsMiddleware adds CORS headers for the local React dev server.
func corsMiddleware(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Access-Control-Allow-Origin", "*")
		w.Header().Set("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
		w.Header().Set("Access-Control-Allow-Headers", "Content-Type, Authorization")
		if r.Method == http.MethodOptions {
			w.WriteHeader(http.StatusNoContent)
			return
		}
		next.ServeHTTP(w, r)
	})
}
