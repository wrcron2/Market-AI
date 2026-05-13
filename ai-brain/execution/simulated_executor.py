"""
simulated_executor.py — Virtual Portfolio Simulator
=====================================================
Tracks a simulated portfolio when the system is in Yahoo Finance mode.

Unlike the IBKR executor (which sends real orders to a broker), this
executor maintains an in-memory ledger of virtual positions and P&L.
It uses the latest yfinance price to calculate realistic fill prices
(with a small configurable slippage model).

The Green Light gate still runs in simulation mode — you approve or
reject signals the same way. The only difference is that execution
writes to the virtual ledger instead of IBKR.

This lets you validate the full pipeline end-to-end with real market
data before going live with real money.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import structlog

log = structlog.get_logger(__name__)


@dataclass
class Position:
    symbol: str
    direction: str        # BUY or SHORT
    quantity: float
    entry_price: float
    entry_time: int       # Unix ms
    signal_id: str


@dataclass
class Fill:
    fill_id: str
    signal_id: str
    symbol: str
    direction: str
    quantity: float
    fill_price: float
    slippage: float       # $ slippage applied
    pnl: float            # Unrealised P&L at fill time
    timestamp: int


@dataclass
class SimulatedPortfolio:
    initial_cash: float = 100_000.0
    cash: float = field(init=False)
    positions: dict[str, Position] = field(default_factory=dict)
    fills: list[Fill] = field(default_factory=list)
    slippage_bps: float = 5.0   # 5 basis points per fill (realistic for mid-cap stocks)

    def __post_init__(self) -> None:
        self.cash = self.initial_cash

    @property
    def position_cost_basis(self) -> float:
        """Total cost basis of all open positions (entry price × quantity)."""
        return sum(p.entry_price * p.quantity for p in self.positions.values())

    @property
    def total_value(self) -> float:
        """Cash + cost basis of open positions. Use mark_to_market() for live P&L."""
        return self.cash + self.position_cost_basis

    @property
    def total_pnl(self) -> float:
        return self.total_value - self.initial_cash

    def mark_to_market(self, prices: dict[str, float]) -> float:
        """
        Compute total portfolio value using live prices.
        Call with {symbol: current_price} at end of each bar for accurate P&L.
        """
        live_value = self.cash
        for symbol, pos in self.positions.items():
            price = prices.get(symbol, pos.entry_price)
            if pos.direction == "LONG":
                live_value += price * pos.quantity
            else:  # SHORT: profit when price falls
                live_value += pos.entry_price * pos.quantity  # proceeds already in cash
                live_value -= price * pos.quantity             # cost to cover
        return round(live_value, 2)

    def summary(self) -> dict[str, Any]:
        return {
            "cash": round(self.cash, 2),
            "position_cost_basis": round(self.position_cost_basis, 2),
            "total_value": round(self.total_value, 2),
            "total_pnl": round(self.total_pnl, 2),
            "open_positions": len(self.positions),
            "fills": len(self.fills),
        }


class SimulatedExecutor:
    """
    Processes approved signals against a virtual portfolio.

    In Yahoo mode, the Go backend still marks orders as EXECUTED in its DB,
    but the actual "fill" is recorded here in the Python process's memory.

    In a production simulation you'd want to persist this to a database.
    """

    def __init__(
        self,
        initial_cash: float = 100_000.0,
        slippage_bps: float = 5.0,
    ) -> None:
        self.portfolio = SimulatedPortfolio(
            initial_cash=initial_cash,
            slippage_bps=slippage_bps,
        )
        log.info(
            "simulated_executor.init",
            initial_cash=initial_cash,
            slippage_bps=slippage_bps,
        )

    def execute(
        self,
        signal_id: str,
        symbol: str,
        direction: str,
        quantity: float,
        limit_price: float,
        market_price: float | None,
    ) -> Fill:
        """
        Simulate a fill and update the virtual portfolio.

        Args:
            signal_id:    UUID of the signal (matches Go backend DB)
            symbol:       Ticker symbol
            direction:    BUY | SELL | SHORT | COVER
            quantity:     Number of shares
            limit_price:  Requested limit price (0 = market)
            market_price: Latest price from yfinance (used for market orders)

        Returns:
            A Fill record describing the simulated execution.
        """
        # ── Determine fill price ───────────────────────────────────────────────
        base_price = limit_price if limit_price > 0 else (market_price or 0)
        if base_price <= 0:
            log.warning("simulated_executor.no_price", symbol=symbol)
            base_price = 1.0   # fallback — shouldn't happen in practice

        slippage_per_share = base_price * (self.portfolio.slippage_bps / 10_000)
        # Slippage direction: adverse for the trader
        if direction in ("BUY", "COVER"):
            fill_price = base_price + slippage_per_share
        else:
            fill_price = base_price - slippage_per_share

        fill_price = round(fill_price, 4)
        cost = fill_price * quantity

        # ── Update cash / positions ────────────────────────────────────────────
        pnl = 0.0
        if direction == "BUY":
            if symbol in self.portfolio.positions:
                existing = self.portfolio.positions[symbol]
                log.warning(
                    "simulated_executor.overwrite_position",
                    symbol=symbol,
                    existing_qty=existing.quantity,
                    existing_entry=existing.entry_price,
                )
            self.portfolio.cash -= cost
            self.portfolio.positions[symbol] = Position(
                symbol=symbol, direction="LONG", quantity=quantity,
                entry_price=fill_price, entry_time=_now_ms(), signal_id=signal_id,
            )
        elif direction == "SELL":
            pos = self.portfolio.positions.pop(symbol, None)
            self.portfolio.cash += fill_price * quantity
            if pos:
                pnl = (fill_price - pos.entry_price) * quantity
        elif direction == "SHORT":
            self.portfolio.cash += fill_price * quantity   # proceeds
            self.portfolio.positions[symbol] = Position(
                symbol=symbol, direction="SHORT", quantity=quantity,
                entry_price=fill_price, entry_time=_now_ms(), signal_id=signal_id,
            )
        elif direction == "COVER":
            pos = self.portfolio.positions.pop(symbol, None)
            self.portfolio.cash -= fill_price * quantity
            if pos:
                pnl = (pos.entry_price - fill_price) * quantity  # short P&L

        fill = Fill(
            fill_id=str(uuid.uuid4()),
            signal_id=signal_id,
            symbol=symbol,
            direction=direction,
            quantity=quantity,
            fill_price=fill_price,
            slippage=round(slippage_per_share * quantity, 4),
            pnl=round(pnl, 4),
            timestamp=_now_ms(),
        )
        self.portfolio.fills.append(fill)

        log.info(
            "simulated_executor.fill",
            signal_id=signal_id,
            symbol=symbol,
            direction=direction,
            quantity=quantity,
            fill_price=fill_price,
            pnl=pnl,
            cash_remaining=round(self.portfolio.cash, 2),
        )
        return fill

    def get_portfolio_summary(self) -> dict[str, Any]:
        return self.portfolio.summary()

    def get_open_positions(self) -> list[dict[str, Any]]:
        return [
            {
                "symbol":      p.symbol,
                "direction":   p.direction,
                "quantity":    p.quantity,
                "entry_price": p.entry_price,
                "signal_id":   p.signal_id,
            }
            for p in self.portfolio.positions.values()
        ]


def _now_ms() -> int:
    return int(time.time() * 1000)
