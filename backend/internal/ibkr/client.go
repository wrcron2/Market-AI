// Package ibkr wraps the Interactive Brokers TWS/Gateway API.
// This is the ONLY package that communicates with the broker.
// It is called exclusively from greenlight.Handler.submitToIBKR,
// which itself only runs after a trader Green Light.
package ibkr

import (
	"fmt"
	"math/rand"
	"os"
	"strconv"
	"time"

	"go.uber.org/zap"

	"github.com/marketflow/backend/internal/db"
)

// Client manages the connection to IBKR TWS/Gateway.
type Client struct {
	host       string
	port       int
	clientID   int
	paperMode  bool
	log        *zap.Logger
}

// NewClient creates a new IBKR client from environment variables.
// Keys NEVER leave local hardware — they are read from the local .env file.
func NewClient(log *zap.Logger) *Client {
	port, _ := strconv.Atoi(getEnv("IBKR_PORT", "7497")) // 7497 = paper
	clientID, _ := strconv.Atoi(getEnv("IBKR_CLIENT_ID", "1"))
	paperMode := getEnv("PAPER_TRADING", "true") == "true"

	return &Client{
		host:      getEnv("IBKR_HOST", "127.0.0.1"),
		port:      port,
		clientID:  clientID,
		paperMode: paperMode,
		log:       log,
	}
}

// PlaceOrder submits an approved order to IBKR and returns the IBKR order ID.
// This method is only callable after the Green Light gate has been passed.
//
// NOTE: In this scaffold, the actual IBKR TWS API call is stubbed.
// Replace the stub body with your chosen IBKR Go client library
// (e.g., github.com/hadrianl/ibapi or the official Java IB API via CGo).
func (c *Client) PlaceOrder(order *db.StagedOrder) (int64, error) {
	if c.paperMode {
		c.log.Info("[PAPER MODE] simulating IBKR order placement",
			zap.String("symbol", order.Symbol),
			zap.String("direction", order.Direction),
			zap.Float64("quantity", order.Quantity),
		)
	}

	// ── TODO: Replace this stub with real IBKR TWS API call ──────────────────
	//
	// Example using hadrianl/ibapi:
	//
	//   ic := ibapi.NewIbClient(nil)
	//   ic.Connect(c.host, c.port, c.clientID)
	//   contract := ibapi.NewContract()
	//   contract.Symbol = order.Symbol
	//   contract.SecType = "STK"
	//   contract.Currency = "USD"
	//   contract.Exchange = "SMART"
	//
	//   ibOrder := ibapi.NewOrder()
	//   ibOrder.Action = order.Direction   // "BUY" or "SELL"
	//   ibOrder.TotalQuantity = order.Quantity
	//   if order.LimitPrice > 0 {
	//       ibOrder.OrderType = "LMT"
	//       ibOrder.LmtPrice = order.LimitPrice
	//   } else {
	//       ibOrder.OrderType = "MKT"
	//   }
	//   ibOrderID := ic.PlaceOrder(ic.NextOrderID(), contract, ibOrder)
	//   return int64(ibOrderID), nil
	//
	// ─────────────────────────────────────────────────────────────────────────

	// Stub: simulate network latency and return a fake order ID.
	time.Sleep(200 * time.Millisecond)

	if c.paperMode && rand.Float64() < 0.05 { // 5% simulated failure in paper mode
		return 0, fmt.Errorf("IBKR stub: simulated connection timeout")
	}

	fakeOrderID := int64(rand.Intn(999999) + 100000)
	c.log.Info("IBKR order placed (stub)",
		zap.String("signal_id", order.ID),
		zap.Int64("ibkr_order_id", fakeOrderID),
	)
	return fakeOrderID, nil
}

func getEnv(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}
