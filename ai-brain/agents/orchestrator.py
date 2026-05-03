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

# ─── Graph State ──────────────────────────────────────────────────────────────

class AgentState(TypedDict, total=False):
    market_snapshot: dict[str, Any]
    signal: CandidateSignal | None
    debate_result: Any | None       # DebateResult
    risk_result: Any | None         # RiskAssessment
    submitted: bool
    error: str


# ─── Orchestrator ─────────────────────────────────────────────────────────────

class Orchestrator:
    """
    Builds and runs the LangGraph pipeline that converts market data
    into staged orders on the Go backend.
    """

    def __init__(self) -> None:
        self.router       = LLMRouter()
        self.signal_agent = SignalAgent(self.router)
        self.debate_agent = DebateAgent(self.router)
        self.risk_agent   = RiskAgent(self.router)

        # REST endpoint on the Go backend
        backend_host = os.getenv("BRAIN_HOST", "127.0.0.1")
        backend_port = os.getenv("GO_SERVER_PORT", "8080")
        self._signals_url = f"http://{backend_host}:{backend_port}/api/signals"

        self._graph = self._build_graph()

    # ── Public API ─────────────────────────────────────────────────────────────

    def run(self, market_snapshot: dict[str, Any]) -> AgentState:
        """
        Process a market data snapshot through the full agent pipeline.

        Args:
            market_snapshot: Dict with keys 'symbol', 'ohlcv', 'indicators', etc.

        Returns:
            The final AgentState after all nodes have run.
        """
        initial: AgentState = {
            "market_snapshot": market_snapshot,
            "signal": None,
            "debate_result": None,
            "risk_result": None,
            "submitted": False,
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
        debate = self.debate_agent.debate(state["signal"])
        return {**state, "debate_result": debate}

    def _node_risk(self, state: AgentState) -> AgentState:
        """Node 3: Run risk assessment and position sizing."""
        if not state.get("debate_result"):
            return state
        risk = self.risk_agent.assess(state["signal"], state["debate_result"])
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
        g.add_edge("submit", END)

        return g.compile()


def _now_ms() -> int:
    import time
    return int(time.time() * 1000)
