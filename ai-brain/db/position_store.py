"""
position_store.py — REST client for position records in the Go backend DB.
All position state is owned by the Go backend (SQLite). Python calls these
endpoints to open/close positions and check today's trading limits.
"""
from __future__ import annotations

import time
from typing import Any

import httpx
import structlog

log = structlog.get_logger(__name__)


class PositionStore:
    """HTTP client for the Go backend's position and trading-limits endpoints."""

    def __init__(self, backend_url: str) -> None:
        self._client = httpx.Client(base_url=backend_url, timeout=10)

    def open_position(
        self,
        signal_id: str,
        symbol: str,
        direction: str,         # LONG | SHORT
        quantity: float,
        entry_price: float,
        confidence: float,
        alpaca_order_id: str = "",
        stop_loss_price: float | None = None,
        take_profit_price: float | None = None,
    ) -> bool:
        payload: dict[str, Any] = {
            "id":                signal_id,
            "symbol":            symbol,
            "direction":         direction,
            "quantity":          quantity,
            "entry_price":       entry_price,
            "entry_time":        _now_ms(),
            "confidence":        confidence,
            "alpaca_order_id":   alpaca_order_id,
            "stop_loss_price":   stop_loss_price,
            "take_profit_price": take_profit_price,
        }
        try:
            resp = self._client.post("/api/positions", json=payload)
            resp.raise_for_status()
            log.info("position_store.opened", signal_id=signal_id, symbol=symbol)
            return True
        except Exception as exc:
            log.error("position_store.open_failed", signal_id=signal_id, error=str(exc))
            return False

    def close_position(
        self,
        signal_id: str,
        exit_price: float,
        realized_pnl: float,
        reason: str,
    ) -> bool:
        try:
            resp = self._client.post(
                f"/api/positions/{signal_id}/close",
                json={"exit_price": exit_price, "realized_pnl": realized_pnl, "reason": reason},
            )
            resp.raise_for_status()
            log.info("position_store.closed", signal_id=signal_id, reason=reason, realized_pnl=realized_pnl)
            return True
        except Exception as exc:
            log.error("position_store.close_failed", signal_id=signal_id, error=str(exc))
            return False

    def get_today_limits(self) -> dict[str, Any]:
        try:
            resp = self._client.get("/api/trading/limits")
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            log.error("position_store.limits_failed", error=str(exc))
            return {"is_halted": False, "realized_pnl": 0.0, "trade_count": 0}


def _now_ms() -> int:
    return int(time.time() * 1000)
