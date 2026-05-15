"""
alpaca_executor.py — Alpaca Paper Trading Client
=================================================
All order execution, position queries, and account reads go through here.

Safety gate: raises RuntimeError at __init__ if PAPER_TRADING != "true".
Every call is logged via structlog for the audit trail.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

import httpx
import structlog

log = structlog.get_logger(__name__)

_DIRECTION_TO_SIDE = {
    "BUY":   "buy",
    "COVER": "buy",
    "SELL":  "sell",
    "SHORT": "sell",
}


class AlpacaExecutor:
    """Thin wrapper around the Alpaca REST API v2 (paper account only)."""

    def __init__(self) -> None:
        if os.getenv("PAPER_TRADING", "true").lower() != "true":
            raise RuntimeError(
                "AlpacaExecutor requires PAPER_TRADING=true. "
                "Set PAPER_TRADING=true before connecting."
            )

        api_key    = os.getenv("ALPACA_API_KEY", "")
        secret_key = os.getenv("ALPACA_SECRET_KEY", "")
        base_url   = os.getenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")

        if not api_key or not secret_key:
            raise RuntimeError(
                "ALPACA_API_KEY and ALPACA_SECRET_KEY must be set in the environment."
            )

        headers = {
            "APCA-API-KEY-ID":     api_key,
            "APCA-API-SECRET-KEY": secret_key,
            "Accept":              "application/json",
            "Content-Type":        "application/json",
        }
        self._client = httpx.Client(base_url=base_url, headers=headers, timeout=15)
        self._data_client = httpx.Client(
            base_url="https://data.alpaca.markets",
            headers=headers,
            timeout=15,
        )
        log.info("alpaca_executor.init", base_url=base_url)

    # ── Account ────────────────────────────────────────────────────────────────

    def verify_account(self) -> dict[str, Any]:
        """
        Confirm the paper account is reachable. Called once at startup.
        Hard-fails with an exception if the account cannot be reached so the
        process stops before any trading logic begins.
        """
        resp = self._client.get("/v2/account")
        resp.raise_for_status()
        acct = resp.json()
        log.info(
            "alpaca.account_verified",
            account_number=acct.get("account_number"),
            status=acct.get("status"),
            buying_power=acct.get("buying_power"),
            equity=acct.get("equity"),
            portfolio_value=acct.get("portfolio_value"),
            paper=True,
        )
        return acct

    def get_account(self) -> dict[str, Any]:
        resp = self._client.get("/v2/account")
        resp.raise_for_status()
        return resp.json()

    # ── Market clock ───────────────────────────────────────────────────────────

    def is_market_open(self) -> bool:
        resp = self._client.get("/v2/clock")
        resp.raise_for_status()
        return bool(resp.json().get("is_open", False))

    def is_near_market_close(self) -> bool:
        """
        True if the market is within MARKET_CLOSE_BUFFER_MINUTES of close.
        Uses Alpaca's authoritative clock so DST and holidays are handled.
        """
        buffer = int(os.getenv("MARKET_CLOSE_BUFFER_MINUTES", "15"))
        resp = self._client.get("/v2/clock")
        resp.raise_for_status()
        clock = resp.json()
        if not clock.get("is_open"):
            return False
        next_close_str = clock.get("next_close", "")
        if not next_close_str:
            return False
        next_close = datetime.fromisoformat(next_close_str.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        minutes_to_close = (next_close - now).total_seconds() / 60
        return 0 <= minutes_to_close <= buffer

    # ── Orders ─────────────────────────────────────────────────────────────────

    def place_order(
        self,
        symbol: str,
        direction: str,      # BUY | SELL | SHORT | COVER
        quantity: float,
        limit_price: float = 0,
        signal_id: str = "",
    ) -> dict[str, Any]:
        """
        Place an order on the Alpaca paper account and return the order object.
        BUY/COVER → side=buy.  SELL/SHORT → side=sell.
        """
        side       = _DIRECTION_TO_SIDE.get(direction.upper(), "buy")
        order_type = "limit" if limit_price > 0 else "market"

        payload: dict[str, Any] = {
            "symbol":        symbol,
            "qty":           str(int(quantity)),
            "side":          side,
            "type":          order_type,
            "time_in_force": "day",
        }
        if order_type == "limit":
            payload["limit_price"] = str(round(limit_price, 2))

        resp = self._client.post("/v2/orders", json=payload)
        resp.raise_for_status()
        order = resp.json()

        log.info(
            "alpaca.order_placed",
            signal_id=signal_id,
            symbol=symbol,
            direction=direction,
            side=side,
            quantity=int(quantity),
            order_type=order_type,
            alpaca_order_id=order.get("id"),
            status=order.get("status"),
        )
        return order

    # ── Market Data ───────────────────────────────────────────────────────────

    def get_latest_price(self, symbol: str) -> float | None:
        """
        Fetch the latest trade price for a symbol from Alpaca market data API.
        Uses data.alpaca.markets — separate from the trading API.
        Returns None if the symbol is not found or the request fails.
        """
        try:
            resp = self._data_client.get(
                f"/v2/stocks/{symbol}/trades/latest",
                params={"feed": "iex"},
            )
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            price = resp.json().get("trade", {}).get("p")
            return float(price) if price is not None else None
        except Exception as exc:
            log.warning("alpaca.get_latest_price_failed", symbol=symbol, error=str(exc))
            return None

    # ── Positions ──────────────────────────────────────────────────────────────

    def get_position(self, symbol: str) -> dict[str, Any] | None:
        """
        Return the current Alpaca position for symbol, or None if not held.
        Alpaca response includes: current_price, unrealized_pl, unrealized_plpc, qty.
        """
        resp = self._client.get(f"/v2/positions/{symbol}")
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json()

    def get_all_positions(self) -> list[dict[str, Any]]:
        """Return all open Alpaca positions."""
        resp = self._client.get("/v2/positions")
        resp.raise_for_status()
        return resp.json()

    def close_position(self, symbol: str, signal_id: str = "") -> dict[str, Any]:
        """
        Close the full position for symbol at market. Called by stop-loss,
        take-profit, and position monitor SELL decisions.
        """
        resp = self._client.delete(f"/v2/positions/{symbol}")
        if resp.status_code == 204:
            log.info("alpaca.position_not_found_on_close", symbol=symbol)
            return {}
        resp.raise_for_status()
        result = resp.json()
        log.info(
            "alpaca.position_closed",
            signal_id=signal_id,
            symbol=symbol,
            alpaca_order_id=result.get("id"),
        )
        return result
