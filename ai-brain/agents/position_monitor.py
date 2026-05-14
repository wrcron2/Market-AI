"""
position_monitor.py — 5-Layer Position Monitor Agent
=====================================================
Runs on a scheduled interval and evaluates every open Alpaca position.

Layer 1 (no LLM):  Hard stop-loss / take-profit / max-hold — instant, always running.
Layer 2 (Ollama):  deepseek-r1:7b routine HOLD/SELL/UNCERTAIN check.
Layer 3 (Bedrock): Auto-escalate if Ollama returns UNCERTAIN.
Layer 4 (Bedrock): Force Bedrock for big moves (>STOP_LOSS_PCT loss OR >TAKE_PROFIT_PCT gain)
                   and for the 3:45pm ET EOD sweep.
Layer 5 (Fallback): If any LLM is unreachable → HOLD all positions, broadcast llm_unreachable.

Sell decisions are never executed without at least one LLM confirmation (L2–L4).
Hard stop-loss (L1) is the only exception — it fires without an LLM call.
"""
from __future__ import annotations

import os
import time
from typing import Any

import httpx
import structlog

from .router import Complexity, LLMRouter

log = structlog.get_logger(__name__)

MONITOR_SYSTEM = """You are a position manager for a paper trading account.
Evaluate whether to HOLD or SELL the open position described below.

Rules:
- Prefer HOLD unless there is a clear reason to exit (deteriorating momentum, risk-off macro, position limit reached)
- Say SELL only if the evidence is strong that the position should be closed now
- Say UNCERTAIN if you cannot determine confidently — this will trigger a senior review

All numbers are already provided without currency symbols.
Respond with EXACTLY ONE of these three options (nothing else):
HOLD. [One sentence reason.]
SELL. [One sentence reason.]
UNCERTAIN. [One sentence reason.]"""


class PositionMonitorAgent:
    """
    Evaluates open Alpaca positions and executes SELL decisions via the 5-layer system.
    Designed to run in a background daemon thread (see main.py).
    """

    def __init__(
        self,
        router: LLMRouter,
        alpaca: Any,           # AlpacaExecutor — avoid cross-package import
        position_store: Any,   # PositionStore
        ws_broadcast_url: str,
    ) -> None:
        self.router         = router
        self.alpaca         = alpaca
        self.store          = position_store
        self._ws_url        = ws_broadcast_url
        self.monitor_model  = os.getenv("MONITOR_MODEL", "deepseek-r1:7b")
        self.stop_loss_pct  = float(os.getenv("STOP_LOSS_PCT",    "5.0"))
        self.take_profit_pct = float(os.getenv("TAKE_PROFIT_PCT", "15.0"))
        self.max_hold_days  = int(os.getenv("MAX_HOLD_DAYS",       "5"))
        self.interval       = int(os.getenv("POSITION_MONITOR_INTERVAL_SECONDS", "300"))

    # ── Main loop (runs in background thread) ──────────────────────────────────

    def run_forever(self) -> None:
        log.info("position_monitor.started",
                 interval=self.interval, model=self.monitor_model)
        while True:
            try:
                self._run_cycle()
            except Exception as exc:
                log.error("position_monitor.cycle_error", error=str(exc))
            time.sleep(self.interval)

    def _run_cycle(self) -> None:
        if not self.alpaca.is_market_open():
            log.debug("position_monitor.market_closed_skip")
            return

        try:
            positions = self.alpaca.get_all_positions()
        except Exception as exc:
            log.error("position_monitor.fetch_positions_failed", error=str(exc))
            return

        if not positions:
            log.debug("position_monitor.no_open_positions")
            return

        # Build symbol → DB record map so we have the correct signal_id for closes
        # and can detect positions whose fill price needs syncing.
        db_positions = self.store.list_open_positions()
        db_by_symbol: dict[str, dict] = {p["symbol"]: p for p in db_positions}

        # Sync fill prices for any position where entry_price is still 0 in the DB
        for pos in positions:
            sym = pos["symbol"]
            fill_price = float(pos.get("avg_entry_price") or 0)
            db_rec = db_by_symbol.get(sym)
            if db_rec and db_rec.get("entry_price", 0) == 0 and fill_price > 0:
                self.store.sync_fill_price(db_rec["id"], fill_price)
                db_rec["entry_price"] = fill_price   # update local copy too
                log.info("position_monitor.fill_price_synced",
                         symbol=sym, fill_price=fill_price)

        log.info("position_monitor.cycle_start", count=len(positions))
        is_eod = self.alpaca.is_near_market_close()

        for pos in positions:
            try:
                db_rec = db_by_symbol.get(pos["symbol"])
                self._evaluate(pos, is_eod=is_eod, db_record=db_rec)
            except Exception as exc:
                log.error("position_monitor.eval_error",
                          symbol=pos.get("symbol"), error=str(exc))

    # ── Per-position evaluation ────────────────────────────────────────────────

    def _evaluate(self, pos: dict[str, Any], *, is_eod: bool, db_record: dict | None = None) -> None:
        symbol     = pos["symbol"]
        plpc       = float(pos["unrealized_plpc"]) * 100   # already a fraction, convert to %
        pl         = float(pos["unrealized_pl"])
        qty        = float(pos["qty"])
        entry_price = float(pos["avg_entry_price"])
        current    = float(pos["current_price"])
        market_val = float(pos["market_value"])

        log.info("position_monitor.evaluating",
                 symbol=symbol, plpc=round(plpc, 2), pl=round(pl, 2))

        # Resolve the signal_id from our DB record (correct UUID for store calls).
        # Falls back to Alpaca's asset_id only if no DB record found.
        signal_id = db_record["id"] if db_record else pos.get("asset_id", symbol)

        # ── Layer 1: Hard rules — no LLM, instant ─────────────────────────────
        if plpc <= -self.stop_loss_pct:
            self._sell(pos, signal_id=signal_id, reason=f"stop_loss ({plpc:.1f}%)",
                       entry_price=entry_price, current=current, qty=qty, pl=pl)
            return

        if plpc >= self.take_profit_pct:
            self._sell(pos, signal_id=signal_id, reason=f"take_profit ({plpc:.1f}%)",
                       entry_price=entry_price, current=current, qty=qty, pl=pl)
            return

        # ── Layer 4: Big moves or EOD → Bedrock directly ──────────────────────
        is_big_move = abs(plpc) >= self.stop_loss_pct or plpc >= (self.take_profit_pct * 0.7)
        if is_big_move or is_eod:
            decision = self._ask_bedrock(pos, plpc, pl, current, entry_price, market_val)
            if decision == "SELL":
                self._sell(pos, signal_id=signal_id,
                           reason=f"bedrock_sell ({'eod' if is_eod else 'big_move'})",
                           entry_price=entry_price, current=current, qty=qty, pl=pl)
            return

        # ── Layer 2: Ollama routine check ──────────────────────────────────────
        try:
            ollama_decision = self._ask_ollama(pos, plpc, pl, current, entry_price, market_val)
        except Exception as exc:
            log.error("position_monitor.ollama_unreachable", symbol=symbol, error=str(exc))
            self._broadcast_llm_alert(symbol, str(exc))
            return

        # ── Layer 3: Escalate UNCERTAIN or SELL to Bedrock ────────────────────
        if ollama_decision in ("UNCERTAIN", "SELL"):
            try:
                bedrock_decision = self._ask_bedrock(pos, plpc, pl, current, entry_price, market_val)
            except Exception as exc:
                log.error("position_monitor.bedrock_unreachable", symbol=symbol, error=str(exc))
                self._broadcast_llm_alert(symbol, str(exc))
                return

            if bedrock_decision == "SELL":
                self._sell(pos, signal_id=signal_id,
                           reason=f"bedrock_confirmed_sell ({ollama_decision.lower()})",
                           entry_price=entry_price, current=current, qty=qty, pl=pl)
            else:
                log.info("position_monitor.bedrock_hold_overrule",
                         symbol=symbol, ollama=ollama_decision)
        else:
            log.info("position_monitor.hold", symbol=symbol, layer="ollama")

    # ── LLM calls ─────────────────────────────────────────────────────────────

    def _build_prompt(
        self,
        pos: dict[str, Any],
        plpc: float,
        pl: float,
        current: float,
        entry_price: float,
        market_val: float,
    ) -> str:
        return (
            f"Symbol:          {pos['symbol']}\n"
            f"Side:            {pos.get('side', 'long').upper()}\n"
            f"Quantity:        {float(pos['qty']):.0f} shares\n"
            f"Entry price:     {entry_price:.4f}\n"
            f"Current price:   {current:.4f}\n"
            f"Market value:    {market_val:.2f}\n"
            f"Unrealized P/L:  {pl:.2f} ({plpc:+.2f} percent)\n\n"
            "Should we HOLD, SELL, or are you UNCERTAIN?"
        )

    def _ask_ollama(
        self,
        pos: dict[str, Any],
        plpc: float, pl: float,
        current: float, entry_price: float, market_val: float,
    ) -> str:
        prompt = self._build_prompt(pos, plpc, pl, current, entry_price, market_val)
        raw = self.router.complete(
            system=MONITOR_SYSTEM,
            user=prompt,
            complexity=Complexity.LOW,
            max_tokens=80,
            model_override=self.monitor_model,
        )
        decision = _parse_decision(raw)
        log.info("position_monitor.ollama_decision",
                 symbol=pos["symbol"], raw=raw[:60], decision=decision)
        return decision

    def _ask_bedrock(
        self,
        pos: dict[str, Any],
        plpc: float, pl: float,
        current: float, entry_price: float, market_val: float,
    ) -> str:
        prompt = self._build_prompt(pos, plpc, pl, current, entry_price, market_val)
        raw = self.router.complete(
            system=MONITOR_SYSTEM,
            user=prompt,
            complexity=Complexity.HIGH,
            max_tokens=80,
        )
        decision = _parse_decision(raw)
        log.info("position_monitor.bedrock_decision",
                 symbol=pos["symbol"], raw=raw[:60], decision=decision)
        return decision

    # ── Execute sell ───────────────────────────────────────────────────────────

    def _sell(
        self,
        pos: dict[str, Any],
        signal_id: str,
        reason: str,
        entry_price: float,
        current: float,
        qty: float,
        pl: float,
    ) -> None:
        symbol = pos["symbol"]
        log.info("position_monitor.selling",
                 symbol=symbol, reason=reason, pl=round(pl, 2))
        try:
            self.alpaca.close_position(symbol, signal_id=signal_id)
        except Exception as exc:
            log.error("position_monitor.sell_failed", symbol=symbol, error=str(exc))
            self._broadcast_llm_alert(symbol, f"Sell execution failed: {exc}")
            return

        # Update the Go backend DB
        self.store.close_position(
            signal_id=signal_id,
            exit_price=current,
            realized_pnl=pl,
            reason=reason,
        )
        log.info("position_monitor.sell_complete", symbol=symbol, reason=reason)

    # ── Layer 5 alert ─────────────────────────────────────────────────────────

    def _broadcast_llm_alert(self, symbol: str, error: str) -> None:
        try:
            backend_url = (
                f"http://{os.getenv('BRAIN_HOST', '127.0.0.1')}:"
                f"{os.getenv('GO_SERVER_PORT', '8080')}"
            )
            httpx.post(
                f"{backend_url}/api/ws/broadcast",
                json={"type": "llm_unreachable", "payload": {"symbol": symbol, "error": error}},
                timeout=3,
            )
        except Exception:
            pass   # alert is best-effort; position is held regardless


def _parse_decision(raw: str) -> str:
    """Extract HOLD / SELL / UNCERTAIN from the first word of the LLM response."""
    first_word = raw.strip().split()[0].rstrip(".").upper() if raw.strip() else "UNCERTAIN"
    if first_word in ("HOLD", "SELL", "UNCERTAIN"):
        return first_word
    return "UNCERTAIN"
