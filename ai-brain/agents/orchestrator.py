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
from .portfolio_limits import PortfolioLimits
from .risk_agent import RiskAgent
from .router import Complexity, LLMRouter
from .signal_agent import CandidateSignal, SignalAgent
from .telemetry import emit_activity

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

    def __init__(self, alpaca: Any = None, position_store: Any = None, notifier: Any = None) -> None:
        self.router         = LLMRouter()
        self.signal_agent   = SignalAgent(self.router)
        self.debate_agent   = DebateAgent(self.router)
        self.risk_agent     = RiskAgent(self.router)
        self._alpaca        = alpaca
        self._position_store = position_store
        self._notifier      = notifier
        # Deterministic hard caps — prompts advise, this enforces.
        self._limits        = PortfolioLimits(alpaca) if alpaca is not None else None

        backend_host = os.getenv("BRAIN_HOST", "127.0.0.1")
        backend_port = os.getenv("GO_SERVER_PORT", "8080")
        self._backend_base = f"http://{backend_host}:{backend_port}"
        self._signals_url  = f"{self._backend_base}/api/signals"

        self._graph = self._build_graph()

    # ── Live telemetry ─────────────────────────────────────────────────────────

    def _emit(self, symbol: str, step: str, status: str, detail: str = "") -> None:
        """
        Broadcast one pipeline step to the dashboard's Brain Activity feed.
        step:   scan | signal | debate | risk | stage | execute
        status: ok | skip | blocked | error
        """
        emit_activity(self._backend_base, symbol, step, status, detail)

    # ── Public API ─────────────────────────────────────────────────────────────

    def run(self, market_snapshot: dict[str, Any]) -> AgentState:
        """
        Process a market data snapshot through the full agent pipeline.
        If AUTO_EXECUTE is true (checked live per run), executes on Alpaca
        after a successful submission to the Go backend.

        Reg NMS guard: snapshots with _source="delayed" are blocked from
        reaching the Green Light gate. Paper mode only.
        """
        sym = market_snapshot.get("symbol", "?")

        # Pipeline pause gate: skip processing when cloud LLM is down
        if self._is_pipeline_paused():
            log.warning(
                "orchestrator.pipeline_paused",
                symbol=market_snapshot.get("symbol"),
                note="Cloud LLM fallback active — pipeline paused to avoid 38-min local inference",
            )
            self._emit(sym, "scan", "skip", "pipeline paused — cloud LLM fallback active")
            return {"market_snapshot": market_snapshot, "signal": None,
                    "debate_result": None, "risk_result": None,
                    "submitted": False, "executed": False}

        # Reg NMS guard: only block delayed data in LIVE mode, not paper
        if market_snapshot.get("_source") == "delayed" and \
                os.getenv("TRADING_MODE", "paper").lower() == "live":
            log.info(
                "orchestrator.delayed_data_blocked",
                symbol=market_snapshot.get("symbol"),
                note="Reg NMS guard — delayed data blocked in live mode only",
            )
            self._emit(sym, "scan", "blocked", "Reg NMS guard — delayed data blocked in live mode")
            return {"market_snapshot": market_snapshot, "signal": None,
                    "debate_result": None, "risk_result": None,
                    "submitted": False, "executed": False}
        self._sync_llm_provider()
        self._emit(sym, "scan", "ok", f"analyzing bar (source: {market_snapshot.get('_source', '?')})")
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
        sym = state["market_snapshot"].get("symbol", "?")
        signal = self.signal_agent.generate(state["market_snapshot"])
        if signal is None:
            self._emit(sym, "signal", "skip", "no setup detected — no signal generated")
        else:
            self._emit(sym, "signal", "ok",
                       f"{signal.direction} candidate · {signal.strategy_name} · confidence {signal.initial_confidence:.2f}")
        return {**state, "signal": signal}

    def _node_debate(self, state: AgentState) -> AgentState:
        """Node 2: Run bull-bear debate on the candidate signal."""
        if not state.get("signal"):
            return state
        try:
            debate = self.debate_agent.debate(state["signal"], state["market_snapshot"])
        except RuntimeError as exc:
            symbol = state["signal"].symbol
            log.warning("orchestrator.debate_failed", symbol=symbol, error=str(exc))
            self._emit(symbol, "debate", "error", f"debate failed — signal dropped: {exc}")
            # Broadcast to dashboard so operator knows signal was silently dropped
            try:
                httpx.post(
                    f"{self._backend_base}/api/events/debate-failed",
                    json={"symbol": symbol, "error": str(exc)},
                    timeout=3,
                )
            except Exception:
                pass  # never let broadcast failure kill the pipeline
            return {**state, "debate_result": None}
        self._emit(state["signal"].symbol, "debate", "ok",
                   f"consensus {debate.consensus_direction} · confidence {debate.adjusted_confidence:.2f}")
        return {**state, "debate_result": debate}

    def _node_risk(self, state: AgentState) -> AgentState:
        """Node 3: Run risk assessment and position sizing."""
        if not state.get("debate_result"):
            return state
        risk = self.risk_agent.assess(state["signal"], state["debate_result"], state["market_snapshot"])
        sym = state["signal"].symbol

        # Deterministic portfolio caps — runs after the LLM risk agent and can
        # only shrink or block. An LLM ignoring its prompt cannot bypass this.
        if not risk.is_blocked and self._limits is not None:
            snapshot_close = float(state["market_snapshot"].get("ohlcv", {}).get("close", 0) or 0)
            ref_price = state["signal"].limit_price or snapshot_close
            verdict = self._limits.enforce(
                symbol=sym,
                direction=state["debate_result"].consensus_direction,
                quantity=risk.adjusted_quantity,
                price=ref_price,
            )
            if verdict.blocked:
                risk = risk.model_copy(update={
                    "is_blocked": True,
                    "block_reason": verdict.reason,
                })
            elif verdict.adjusted_quantity < risk.adjusted_quantity:
                risk = risk.model_copy(update={
                    "adjusted_quantity": verdict.adjusted_quantity,
                    "risk_notes": " | ".join(x for x in (risk.risk_notes, verdict.reason) if x),
                })

        if risk.is_blocked:
            self._emit(sym, "risk", "blocked", f"blocked — {risk.block_reason}")
        else:
            self._emit(sym, "risk", "ok",
                       f"approved · qty {risk.adjusted_quantity:.0f} · confidence {risk.final_confidence:.2f} · risk {risk.risk_score:.2f}")
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
            if accepted:
                self._emit(signal.symbol, "stage", "ok",
                           f"order staged PENDING · {debate.consensus_direction} {risk.adjusted_quantity:.0f} — awaiting Green Light / auto-execute")
            else:
                self._emit(signal.symbol, "stage", "skip",
                           f"backend declined: {data.get('message', 'duplicate or filtered')}")
            return {**state, "submitted": accepted}

        except Exception as exc:
            log.error("orchestrator.http_error", error=str(exc))
            self._emit(signal.symbol, "stage", "error", f"submit to backend failed: {exc}")
            return {**state, "error": str(exc), "submitted": False}

    def _node_execute(self, state: AgentState) -> AgentState:
        """
        Node 5: Place the order on Alpaca when AUTO_EXECUTE is enabled.
        Skipped entirely when AUTO_EXECUTE=false (manual Green Light flow).
        Checks today's daily loss limit before executing.
        """
        sym = state["signal"].symbol if state.get("signal") else "?"

        if self._alpaca is None or not self._is_auto_execute_enabled():
            if state.get("submitted"):
                self._emit(sym, "execute", "skip",
                           "auto-execute OFF — order stays PENDING until you approve it (Green Light)")
            return state

        # Block execution for pre/post market watchlist signals
        if state.get("market_snapshot", {}).get("_requires_revalidation"):
            log.info("orchestrator.watchlist_signal_skipped",
                     symbol=state.get("signal", {}).symbol if state.get("signal") else "?",
                     note="pre/post market signal — staged as watchlist, not executed")
            self._emit(sym, "execute", "skip", "pre/post-market signal — staged as watchlist, not executed")
            return state

        # Enforce a higher confidence bar for autonomous execution
        risk = state.get("risk_result")
        auto_min_conf = float(os.getenv("AUTO_EXECUTE_MIN_CONFIDENCE", "0.85"))
        if risk and risk.final_confidence < auto_min_conf:
            log.info("orchestrator.auto_execute_confidence_too_low",
                     symbol=state["signal"].symbol,
                     confidence=risk.final_confidence,
                     required=auto_min_conf)
            self._emit(sym, "execute", "skip",
                       f"confidence {risk.final_confidence:.2f} below auto-execute bar {auto_min_conf:.2f} — needs manual Green Light")
            return state

        # Never place orders when the market is closed
        if not self._alpaca.is_market_open():
            sym = state["signal"].symbol if state.get("signal") else "?"
            log.info("orchestrator.market_closed_skip", symbol=sym)
            self._emit(sym, "execute", "skip", "market closed — no order placed")
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
                self._emit(signal.symbol, "execute", "blocked",
                           f"daily loss limit reached (realized {limits.get('realized_pnl', 0):+.2f}) — halted until tomorrow")
                if self._notifier:
                    self._notifier.critical(
                        "Daily Loss Limit Reached — AUTO_EXECUTE Halted",
                        f"Symbol attempted: {signal.symbol}\nRealized P&L today: ${limits.get('realized_pnl', 0):.2f}\nAll new auto-execute orders halted until tomorrow."
                    )
                return {**state, "error": "daily loss limit reached — order skipped"}

        # Guard: skip if Alpaca already holds an open position for this symbol
        existing = self._alpaca.get_position(signal.symbol)
        if existing:
            log.info("orchestrator.duplicate_position_skipped",
                     symbol=signal.symbol,
                     existing_qty=existing.get("qty"))
            self._emit(signal.symbol, "execute", "skip",
                       f"already holding {existing.get('qty')} shares — duplicate position skipped")
            return state

        # Cash-only guard: Alpaca is a margin venue, but the real IBKR account
        # will be cash-only — never buy with money we don't have, never short.
        allowed, guard_reason = self._alpaca.check_cash_guard(
            debate.consensus_direction, risk.adjusted_quantity,
            signal.limit_price, signal.symbol,
        )
        if not allowed:
            log.info("orchestrator.cash_guard_blocked",
                     symbol=signal.symbol, reason=guard_reason)
            self._emit(signal.symbol, "execute", "blocked", guard_reason)
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
            self._emit(signal.symbol, "execute", "ok",
                       f"{direction} {risk.adjusted_quantity:.0f} auto-executed on Alpaca @ {fill_price:.2f} (order {alpaca_order_id[:8]})")
            return {**state, "executed": True}

        except Exception as exc:
            log.error("orchestrator.execute_error",
                      signal_id=signal.signal_id, error=str(exc))
            self._emit(signal.symbol, "execute", "error", f"Alpaca order failed: {exc}")
            return {**state, "error": str(exc), "executed": False}

    def _is_auto_execute_enabled(self) -> bool:
        """Check the dashboard toggle state from the Go backend (live, per run)."""
        try:
            resp = httpx.get(f"{self._backend_base}/api/auto-execute", timeout=3)
            return resp.json().get("enabled", False)
        except Exception:
            return False   # fail safe — never auto-execute if backend unreachable

    def _is_pipeline_paused(self) -> bool:
        """Check with the Go backend whether the pipeline should pause (cloud LLM fallback active)."""
        try:
            resp = httpx.get(f"{self._backend_base}/api/pipeline-pause", timeout=3)
            return resp.json().get("paused", False)
        except Exception:
            return False

    def _sync_llm_provider(self) -> None:
        """Sync the LLM provider toggle from the Go backend before each pipeline run."""
        try:
            resp = httpx.get(f"{self._backend_base}/api/llm-provider", timeout=3)
            provider = resp.json().get("provider", "aws")
            self.router.use_aws = (provider == "aws")
        except Exception:
            pass  # keep current setting if backend unreachable

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
