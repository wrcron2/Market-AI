"""
outcome_checker.py — Signal Postmortem Agent
=============================================
Checks approved signal outcomes at 5-day and 20-day horizons.
Fetches current price via yfinance, computes return, classifies as
TRUE_POSITIVE / FALSE_POSITIVE / REGIME_MISMATCH.

Runs as a background thread in the brain, checking every hour.
"""
from __future__ import annotations

import time
import threading
from typing import Any

import httpx
import structlog
import yfinance as yf

log = structlog.get_logger(__name__)

CHECK_INTERVAL_SECONDS = 3600  # check once per hour


class OutcomeChecker:
    def __init__(self, backend_url: str) -> None:
        self._client = httpx.Client(base_url=backend_url, timeout=15)

    def run_forever(self) -> None:
        log.info("outcome_checker.started", interval=CHECK_INTERVAL_SECONDS)
        while True:
            try:
                self._check_pending()
            except Exception as exc:
                log.error("outcome_checker.error", error=str(exc))
            time.sleep(CHECK_INTERVAL_SECONDS)

    def _check_pending(self) -> None:
        resp = self._client.get("/api/signal-outcomes/pending-checks")
        if resp.status_code != 200:
            return
        outcomes = resp.json().get("outcomes") or []
        if not outcomes:
            return

        log.info("outcome_checker.cycle_start", count=len(outcomes))
        now_ms = int(time.time() * 1000)

        for o in outcomes:
            symbol      = o["symbol"]
            signal_id   = o["signal_id"]
            direction   = o["predicted_direction"]  # BUY | SELL | SHORT | COVER
            entry_price = o.get("entry_price", 0.0)
            check_5d    = o["check_5d_at"]
            check_20d   = o["check_20d_at"]
            outcome_5d  = o.get("outcome_5d")
            outcome_20d = o.get("outcome_20d")

            try:
                current_price = self._fetch_price(symbol)
                if current_price is None or current_price <= 0:
                    continue

                # Sync entry price from positions if missing
                if entry_price <= 0:
                    entry_price = self._get_entry_price(signal_id)
                if entry_price <= 0:
                    continue

                if outcome_5d is None and now_ms >= check_5d:
                    ret = (current_price - entry_price) / entry_price * 100
                    outcome = self._classify(direction, ret)
                    self._post_update(signal_id, "5d", current_price, ret, outcome)
                    log.info("outcome_checker.5d_recorded",
                             symbol=symbol, return_pct=round(ret, 2), outcome=outcome)

                if outcome_20d is None and now_ms >= check_20d:
                    ret = (current_price - entry_price) / entry_price * 100
                    outcome = self._classify(direction, ret)
                    self._post_update(signal_id, "20d", current_price, ret, outcome)
                    log.info("outcome_checker.20d_recorded",
                             symbol=symbol, return_pct=round(ret, 2), outcome=outcome)

            except Exception as exc:
                log.warning("outcome_checker.symbol_error", symbol=symbol, error=str(exc))

    def _fetch_price(self, symbol: str) -> float | None:
        try:
            ticker = yf.Ticker(symbol)
            hist = ticker.history(period="1d")
            if hist.empty:
                return None
            return float(hist["Close"].iloc[-1])
        except Exception:
            return None

    def _get_entry_price(self, signal_id: str) -> float:
        try:
            resp = self._client.get("/api/positions")
            if resp.status_code != 200:
                return 0.0
            for p in resp.json().get("positions", []):
                if p.get("id") == signal_id:
                    return float(p.get("entry_price", 0))
        except Exception:
            pass
        return 0.0

    def _classify(self, direction: str, return_pct: float) -> str:
        bullish = direction in ("BUY", "COVER")
        if bullish and return_pct > 0:
            return "TRUE_POSITIVE"
        if not bullish and return_pct < 0:
            return "TRUE_POSITIVE"
        # Small moves (< 0.5%) in either direction = regime noise, not skill failure
        if abs(return_pct) < 0.5:
            return "REGIME_MISMATCH"
        return "FALSE_POSITIVE"

    def _post_update(self, signal_id: str, horizon: str,
                     price: float, ret: float, outcome: str) -> None:
        self._client.post("/api/signal-outcomes/update", json={
            "signal_id":  signal_id,
            "horizon":    horizon,
            "price":      price,
            "return_pct": ret,
            "outcome":    outcome,
        })
