"""
orchestrator.py — LangGraph Orchestrator
=========================================
Ties all agents together into a directed graph:

  market_data → signal_agent → debate_agent → risk_agent → grpc_submit

Each node is a LangGraph StateGraph node. The graph runs for every
new market data event (tick, bar close, news event, etc.).

State transitions:
  GENERATE  → DEBATE   (if signal was generated)
  GENERATE  → END      (if no signal generated)
  DEBATE    → RISK
  RISK      → SUBMIT   (if not blocked)
  RISK      → END      (if blocked)
  SUBMIT    → END
"""
from __future__ import annotations

import os
from typing import Any, TypedDict

import httpx
import structlog
from langgraph.graph import END, StateGraph

from .debate_agent import DebateAgent
from .risk_agent import RiskAgent
from .router import Complexity, LLMRouter
from .signal_agent import CandidateSignal, SignalAgent

log = structlog.get_logger(__name__)

_DIRECTION_TO_POSITION_SIDE = {"BUY": "LONG", "SHORT": "SHORT", "SELL": "LONG", "COVER": "SHORT"}

# ─── Graph State ──────────────────────────────────────────────────────────────

class AgentState(TypedDict, total=False):
    market_snapshot: dict[str, Any]
    signal: CandidateSignal | None
    debate_result: Any | None       # DebateResult
    risk_result: Any | None         # RiskAssessment
    submitted: bool
    executed: bool                  # True if Alpaca order was placed
    error: str


# ─── Orchestrator ─────────────────────────────────────────────────────────────

class Orchestrator:
    """
    Builds and runs the LangGraph pipeline that converts market data
    into staged orders on the Go backend.
    """

    def __init__(self, alpaca: Any = None, position_store: Any = None) -> None:
        self.router         = LLMRouter()
        self.signal_agent   = SignalAgent(self.router)
        self.debate_agent   = DebateAgent(self.router)
        self.risk_agent     = RiskAgent(self.router)
        self._alpaca        = alpaca
        self._position_store = position_store

        backend_host = os.getenv("BRAIN_HOST", "127.0.0.1")
        backend_port = os.getenv("GO_SERVER_PORT", "8080")
        self._backend_base = f"http://{backend_host}:{backend_port}"
        self._signals_url  = f"{self._backend_base}/api/signals"

        self._graph = self._build_graph()

    # ── Public API ─────────────────────────────────────────────────────────────

    def run(self, market_snapshot: dict[str, Any]) -> AgentState:
        """
        Process a market data snapshot through the full agent pipeline.
        If AUTO_EXECUTE is true (checked live per run), executes on Alpaca
        after a successful submission to the Go backend.
        """
        initial: AgentState = {
            "market_snapshot": market_snapshot,
            "signal": None,
            "debate_result": None,
            "risk_result": None,
            "submitted": False,
            "executed": False,
        }
        return self._graph.invoke(initial)

    # ── Graph Nodes ────────────────────────────────────────────────────────────

    def _node_generate(self, state: AgentState) -> AgentState:
        """Node 1: Generate a candidate signal from market data."""
        signal = self.signal_agent.generate(state["market_snapshot"])
        return {**state, "signal": signal}

    def _node_debate(self, state: AgentState) -> AgentState:
        """Node 2: Run bull-bear debate on the candidate signal."""
        if not state.get("signal"):
            return state
        try:
            debate = self.debate_agent.debate(state["signal"], state["market_snapshot"])
        except RuntimeError as exc:
            log.warning("orchestrator.debate_failed", symbol=state["signal"].symbol, error=str(exc))
            return {**state, "debate_result": None}
        return {**state, "debate_result": debate}

    def _node_risk(self, state: AgentState) -> AgentState:
        """Node 3: Run risk assessment and position sizing."""
        if not state.get("debate_result"):
            return state
        risk = self.risk_agent.assess(state["signal"], state["debate_result"], state["market_snapshot"])
        return {**state, "risk_result": risk}

    def _node_submit(self, state: AgentState) -> AgentState:
        """Node 4: Submit the approved signal to the Go backend via gRPC."""
        signal = state["signal"]
        debate = state["debate_result"]
        risk   = state["risk_result"]

        if not all([signal, debate, risk]):
            return {**state, "error": "missing state in submit node"}

        # Compose final reasoning from all three agents
        full_reasoning = (
            f"[Signal] {signal.reasoning}\n\n"
            f"[Bull] {debate.bull_argument}\n\n"
            f"[Bear] {debate.bear_argument}\n\n"
            f"[Judge] {debate.judge_reasoning}\n\n"
            f"[Risk] {risk.risk_notes}"
        )

        model_tag = self.router.model_tag(Complexity.LOW)

        # ── REST call to Go backend ────────────────────────────────────────────
        try:
            payload = {
                "signal_id":     signal.signal_id,
                "symbol":        signal.symbol,
                "direction":     debate.consensus_direction,
                "quantity":      risk.adjusted_quantity,
                "limit_price":   signal.limit_price,
                "confidence":    risk.final_confidence,
                "reasoning":     full_reasoning,
                "strategy_name": signal.strategy_name,
                "model_used":    model_tag,
            }
            resp = httpx.post(self._signals_url, json=payload, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            accepted = data.get("accepted", False)
            log.info(
                "orchestrator.submitted",
                signal_id=signal.signal_id,
                accepted=accepted,
                message=data.get("message", ""),
            )
            return {**state, "submitted": accepted}

        except Exception as exc:
            log.error("orchestrator.http_error", error=str(exc))
            return {**state, "error": str(exc), "submitted": False}

    def _node_execute(self, state: AgentState) -> AgentState:
        """
        Node 5: Place the order on Alpaca when AUTO_EXECUTE is enabled.
        Skipped entirely when AUTO_EXECUTE=false (manual Green Light flow).
        Checks today's daily loss limit before executing.
        """
        if self._alpaca is None or not self._is_auto_execute_enabled():
            return state

        # Enforce a higher confidence bar for autonomous execution
        risk = state.get("risk_result")
        auto_min_conf = float(os.getenv("AUTO_EXECUTE_MIN_CONFIDENCE", "0.85"))
        if risk and risk.final_confidence < auto_min_conf:
            log.info("orchestrator.auto_execute_confidence_too_low",
                     symbol=state["signal"].symbol,
                     confidence=risk.final_confidence,
                     required=auto_min_conf)
            return state

        # Never place orders when the market is closed
        if not self._alpaca.is_market_open():
            sym = state["signal"].symbol if state.get("signal") else "?"
            log.info("orchestrator.market_closed_skip", symbol=sym)
            return state

        signal = state["signal"]
        debate = state["debate_result"]
        risk   = state["risk_result"]

        if not all([signal, debate, risk]) or not state.get("submitted"):
            return state

        # Check daily loss limit before placing
        if self._position_store:
            limits = self._position_store.get_today_limits()
            if limits.get("is_halted"):
                log.info("orchestrator.daily_limit_halted", symbol=signal.symbol)
                return {**state, "error": "daily loss limit reached — order skipped"}

        # Guard: skip if Alpaca already holds an open position for this symbol
        existing = self._alpaca.get_position(signal.symbol)
        if existing:
            log.info("orchestrator.duplicate_position_skipped",
                     symbol=signal.symbol,
                     existing_qty=existing.get("qty"))
            return state

        try:
            direction = debate.consensus_direction
            order = self._alpaca.place_order(
                symbol=signal.symbol,
                direction=direction,
                quantity=risk.adjusted_quantity,
                limit_price=signal.limit_price,
                signal_id=signal.signal_id,
            )
            alpaca_order_id = order.get("id", "")
            fill_price      = float(order.get("filled_avg_price") or signal.limit_price or 0)

            # Notify Go backend: PENDING → APPROVED → EXECUTED
            resp = httpx.post(
                f"{self._backend_base}/api/orders/auto-execute",
                json={
                    "signal_id":       signal.signal_id,
                    "alpaca_order_id": alpaca_order_id,
                    "fill_price":      fill_price,
                },
                timeout=10,
            )
            resp.raise_for_status()

            # Record open position in DB
            if self._position_store and direction in ("BUY", "SHORT"):
                entry = fill_price if fill_price > 0 else (signal.limit_price or 0)
                pos_side = _DIRECTION_TO_POSITION_SIDE.get(direction, "LONG")
                self._position_store.open_position(
                    signal_id=signal.signal_id,
                    symbol=signal.symbol,
                    direction=pos_side,
                    quantity=risk.adjusted_quantity,
                    entry_price=entry,
                    confidence=risk.final_confidence,
                    alpaca_order_id=alpaca_order_id,
                )

            log.info("orchestrator.auto_executed",
                     signal_id=signal.signal_id,
                     symbol=signal.symbol,
                     direction=direction,
                     alpaca_order_id=alpaca_order_id)
            return {**state, "executed": True}

        except Exception as exc:
            log.error("orchestrator.execute_error",
                      signal_id=signal.signal_id, error=str(exc))
            return {**state, "error": str(exc), "executed": False}

    def _is_auto_execute_enabled(self) -> bool:
        """Check the dashboard toggle state from the Go backend (live, per run)."""
        try:
            resp = httpx.get(f"{self._backend_base}/api/auto-execute", timeout=3)
            return resp.json().get("enabled", False)
        except Exception:
            return False   # fail safe — never auto-execute if backend unreachable

    # ── Routing conditions ─────────────────────────────────────────────────────

    def _route_after_generate(self, state: AgentState) -> str:
        return "debate" if state.get("signal") else END

    def _route_after_risk(self, state: AgentState) -> str:
        risk = state.get("risk_result")
        if risk and not risk.is_blocked:
            return "submit"
        symbol = state.get("signal", {})
        log.info("orchestrator.signal_blocked",
                 symbol=getattr(symbol, "symbol", "?"),
                 reason=getattr(risk, "block_reason", "unknown"))
        return END

    # ── Graph construction ─────────────────────────────────────────────────────

    def _build_graph(self) -> Any:
        g = StateGraph(AgentState)

        g.add_node("generate", self._node_generate)
        g.add_node("debate",   self._node_debate)
        g.add_node("risk",     self._node_risk)
        g.add_node("submit",   self._node_submit)
        g.add_node("execute",  self._node_execute)

        g.set_entry_point("generate")

        g.add_conditional_edges("generate", self._route_after_generate, {
            "debate": "debate",
            END: END,
        })
        g.add_edge("debate", "risk")
        g.add_conditional_edges("risk", self._route_after_risk, {
            "submit": "submit",
            END: END,
        })
        g.add_edge("submit",  "execute")
        g.add_edge("execute", END)

        return g.compile()
