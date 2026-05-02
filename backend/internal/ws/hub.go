// Package ws manages WebSocket connections from the React dashboard.
// The hub broadcasts real-time order status updates to all connected clients.
package ws

import (
	"encoding/json"
	"net/http"
	"sync"
	"time"

	"github.com/gorilla/websocket"
	"go.uber.org/zap"
)

// Message is the envelope sent over WebSocket to the dashboard.
type Message struct {
	Type    string `json:"type"`    // "order_staged", "order_approved", "order_rejected", "order_executed"
	Payload any    `json:"payload"` // Any JSON-serialisable value
}

type client struct {
	conn *websocket.Conn
	send chan []byte
}

// Hub maintains active WebSocket connections and broadcasts messages.
type Hub struct {
	mu       sync.RWMutex
	clients  map[*client]bool
	upgrader websocket.Upgrader
	log      *zap.Logger
}

func NewHub(log *zap.Logger) *Hub {
	return &Hub{
		clients: make(map[*client]bool),
		upgrader: websocket.Upgrader{
			CheckOrigin: func(r *http.Request) bool { return true }, // permissive for local dev
			HandshakeTimeout: 5 * time.Second,
		},
		log: log,
	}
}

// Run starts the hub's internal pump loop. Call in a goroutine.
func (h *Hub) Run() {
	// Hub is stateless — clients self-manage their send loops.
	// This method is a hook for future cluster-wide pub/sub.
}

// Broadcast sends a typed message to every connected dashboard client.
func (h *Hub) Broadcast(msgType string, payload any) {
	data, err := json.Marshal(Message{Type: msgType, Payload: payload})
	if err != nil {
		h.log.Error("ws.Broadcast marshal error", zap.Error(err))
		return
	}

	h.mu.RLock()
	defer h.mu.RUnlock()

	for c := range h.clients {
		select {
		case c.send <- data:
		default:
			// Slow client — drop the message rather than blocking the broadcaster.
		}
	}
}

// ServeWS upgrades an HTTP connection to WebSocket and registers the client.
func (h *Hub) ServeWS(w http.ResponseWriter, r *http.Request) {
	conn, err := h.upgrader.Upgrade(w, r, nil)
	if err != nil {
		h.log.Warn("websocket upgrade failed", zap.Error(err))
		return
	}

	c := &client{conn: conn, send: make(chan []byte, 256)}

	h.mu.Lock()
	h.clients[c] = true
	h.mu.Unlock()

	h.log.Info("websocket client connected", zap.String("remote", r.RemoteAddr))

	go c.writePump(h)
	c.readPump(h) // blocks until client disconnects
}

// readPump keeps the connection alive by consuming control frames (ping/pong).
func (c *client) readPump(h *Hub) {
	defer func() {
		h.mu.Lock()
		delete(h.clients, c)
		h.mu.Unlock()
		c.conn.Close()
		h.log.Info("websocket client disconnected")
	}()

	c.conn.SetReadLimit(512)
	c.conn.SetReadDeadline(time.Now().Add(60 * time.Second))
	c.conn.SetPongHandler(func(string) error {
		c.conn.SetReadDeadline(time.Now().Add(60 * time.Second))
		return nil
	})

	for {
		if _, _, err := c.conn.ReadMessage(); err != nil {
			break
		}
	}
}

// writePump sends queued messages and periodic pings.
func (c *client) writePump(h *Hub) {
	ticker := time.NewTicker(30 * time.Second)
	defer func() {
		ticker.Stop()
		c.conn.Close()
	}()

	for {
		select {
		case msg, ok := <-c.send:
			c.conn.SetWriteDeadline(time.Now().Add(10 * time.Second))
			if !ok {
				c.conn.WriteMessage(websocket.CloseMessage, []byte{})
				return
			}
			if err := c.conn.WriteMessage(websocket.TextMessage, msg); err != nil {
				return
			}

		case <-ticker.C:
			c.conn.SetWriteDeadline(time.Now().Add(10 * time.Second))
			if err := c.conn.WriteMessage(websocket.PingMessage, nil); err != nil {
				return
			}
		}
	}
}
