// Package grpc provides gRPC service implementations for the Go backend.
package grpc

import (
	"context"
	"fmt"
	"os"
	"strconv"

	"go.uber.org/zap"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"

	"github.com/marketflow/backend/internal/db"
	"github.com/marketflow/backend/internal/ws"
	proto "github.com/marketflow/backend/proto"
)

// SignalServer receives trading signals from the Python AI Brain via gRPC.
type SignalServer struct {
	proto.UnimplementedSignalServiceServer
	db  *db.DB
	hub *ws.Hub
	log *zap.Logger

	minConfidence float64
}

func NewSignalServer(database *db.DB, hub *ws.Hub, log *zap.Logger) *SignalServer {
	minConf, _ := strconv.ParseFloat(os.Getenv("MIN_SIGNAL_CONFIDENCE"), 64)
	if minConf == 0 {
		minConf = 0.90 // default: 90% confidence floor
	}
	return &SignalServer{db: database, hub: hub, log: log, minConfidence: minConf}
}

// SubmitSignal stages an incoming signal from the AI brain.
// Rejects signals below the confidence threshold.
// No order reaches IBKR from here — that requires a Green Light.
func (s *SignalServer) SubmitSignal(ctx context.Context, req *proto.SignalRequest) (*proto.SignalResponse, error) {
	s.log.Info("signal received",
		zap.String("signal_id", req.SignalId),
		zap.String("symbol", req.Symbol),
		zap.Float64("confidence", req.Confidence),
		zap.String("strategy", req.StrategyName),
	)

	// ── Confidence gate ───────────────────────────────────────────────────────
	if req.Confidence < s.minConfidence {
		msg := fmt.Sprintf("confidence %.2f below threshold %.2f", req.Confidence, s.minConfidence)
		s.log.Warn("signal rejected: low confidence", zap.String("reason", msg))
		return &proto.SignalResponse{
			SignalId: req.SignalId,
			Accepted: false,
			Message:  msg,
		}, nil
	}

	// ── Validate direction ────────────────────────────────────────────────────
	dirStr, ok := protoDirectionToString(req.Direction)
	if !ok {
		return nil, status.Errorf(codes.InvalidArgument, "unknown direction: %v", req.Direction)
	}

	// ── Stage the order (PENDING, not yet sent to IBKR) ──────────────────────
	order := &db.StagedOrder{
		ID:           req.SignalId,
		Symbol:       req.Symbol,
		Direction:    dirStr,
		Quantity:     req.Quantity,
		LimitPrice:   req.LimitPrice,
		Confidence:   req.Confidence,
		Reasoning:    req.Reasoning,
		StrategyName: req.StrategyName,
		ModelUsed:    req.ModelUsed,
	}

	if err := s.db.StageOrder(order); err != nil {
		s.log.Error("failed to stage order", zap.Error(err))
		return nil, status.Errorf(codes.Internal, "failed to stage order: %v", err)
	}

	// Notify the dashboard via WebSocket.
	s.hub.Broadcast("order_staged", order)

	s.log.Info("signal staged — awaiting Green Light",
		zap.String("signal_id", req.SignalId),
		zap.String("symbol", req.Symbol),
	)

	return &proto.SignalResponse{
		SignalId: req.SignalId,
		Accepted: true,
		Message:  "staged, awaiting Green Light",
	}, nil
}

// WatchSignalStatus streams status updates back to the AI brain (optional).
func (s *SignalServer) WatchSignalStatus(req *proto.WatchRequest, stream proto.SignalService_WatchSignalStatusServer) error {
	// TODO: implement via a DB change notification or in-memory pub/sub.
	// For now, block until the client disconnects.
	<-stream.Context().Done()
	return nil
}

// ─── GreenLight gRPC server (mirrors the HTTP handler for gRPC clients) ───────

type GreenLightServer struct {
	proto.UnimplementedGreenLightServiceServer
	db  *db.DB
	hub *ws.Hub
	log *zap.Logger
}

func NewGreenLightServer(database *db.DB, hub *ws.Hub, log *zap.Logger) *GreenLightServer {
	return &GreenLightServer{db: database, hub: hub, log: log}
}

func (g *GreenLightServer) Approve(ctx context.Context, req *proto.GreenLightRequest) (*proto.GreenLightResponse, error) {
	if err := g.db.TransitionStatus(req.SignalId, db.StatusApproved, "trader", req.TraderComment); err != nil {
		return &proto.GreenLightResponse{SignalId: req.SignalId, Success: false, Message: err.Error()}, nil
	}
	g.hub.Broadcast("order_approved", map[string]string{"signal_id": req.SignalId})
	return &proto.GreenLightResponse{SignalId: req.SignalId, Success: true}, nil
}

func (g *GreenLightServer) Reject(ctx context.Context, req *proto.GreenLightRequest) (*proto.GreenLightResponse, error) {
	if err := g.db.TransitionStatus(req.SignalId, db.StatusRejected, "trader", req.TraderComment); err != nil {
		return &proto.GreenLightResponse{SignalId: req.SignalId, Success: false, Message: err.Error()}, nil
	}
	g.hub.Broadcast("order_rejected", map[string]string{"signal_id": req.SignalId})
	return &proto.GreenLightResponse{SignalId: req.SignalId, Success: true}, nil
}

func (g *GreenLightServer) ListPending(ctx context.Context, req *proto.ListPendingRequest) (*proto.ListPendingResponse, error) {
	orders, total, err := g.db.ListByStatus(db.StatusPending, int(req.Limit), int(req.Offset))
	if err != nil {
		return nil, status.Errorf(codes.Internal, "list pending: %v", err)
	}

	var protoOrders []*proto.StagedOrder
	for _, o := range orders {
		dir, _ := stringToProtoDirection(o.Direction)
		protoOrders = append(protoOrders, &proto.StagedOrder{
			SignalId:     o.ID,
			Symbol:       o.Symbol,
			Direction:    dir,
			Quantity:     o.Quantity,
			LimitPrice:   o.LimitPrice,
			Confidence:   o.Confidence,
			Reasoning:    o.Reasoning,
			StrategyName: o.StrategyName,
			Status:       proto.OrderStatus_PENDING,
			CreatedAt:    o.CreatedAt,
		})
	}

	return &proto.ListPendingResponse{Orders: protoOrders, Total: int32(total)}, nil
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

func protoDirectionToString(d proto.Direction) (string, bool) {
	switch d {
	case proto.Direction_BUY:
		return "BUY", true
	case proto.Direction_SELL:
		return "SELL", true
	case proto.Direction_SHORT:
		return "SHORT", true
	case proto.Direction_COVER:
		return "COVER", true
	}
	return "", false
}

func stringToProtoDirection(s string) (proto.Direction, bool) {
	switch s {
	case "BUY":
		return proto.Direction_BUY, true
	case "SELL":
		return proto.Direction_SELL, true
	case "SHORT":
		return proto.Direction_SHORT, true
	case "COVER":
		return proto.Direction_COVER, true
	}
	return proto.Direction_DIRECTION_UNKNOWN, false
}
