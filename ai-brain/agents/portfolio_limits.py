"""
portfolio_limits.py — Deterministic Portfolio-Level Hard Limits
================================================================
Enforces the risk-and-portfolio.md portfolio constraints in CODE, not prompts.

Why this module exists: on 2026-07-09 the signal agent's prompt-level 8% cap
was ignored by the LLM and a $80k QQQ position (80% of equity) reached Alpaca.
Prompts are guidance; this module is enforcement. It runs after risk_agent
in the orchestrator and can only shrink or block — never enlarge — a signal.

Rules enforced (risk-and-portfolio.md "Portfolio-Level Constraints"):
- Max single position exposure: 10% of current equity
- Max sector concentration:     30% of current equity
- Max open positions:           10
- Drawdown suspend:             equity below -15% from initial → no new BUYs

Fail-closed: if live portfolio state cannot be fetched, new exposure is
blocked (closing trades are always allowed — reducing risk is always safe).
"""
from __future__ import annotations

import math
import os
from dataclasses import dataclass, field

import structlog

log = structlog.get_logger(__name__)

# Directions that ADD exposure vs. directions that close it.
_OPENING_DIRECTIONS = {"BUY", "SHORT"}
_CLOSING_DIRECTIONS = {"SELL", "COVER"}

# Coarse sector buckets for the ETF universe (unknown symbols form their own
# bucket, so the 30% rule still binds per-symbol at worst).
SECTOR_MAP: dict[str, str] = {
    "QQQ": "tech", "XLK": "tech",
    "SPY": "broad", "DIA": "broad", "IWM": "broad", "MDY": "broad",
    "XLE": "energy", "USO": "energy",
    "TLT": "bonds", "HYG": "bonds", "LQD": "bonds",
    "GLD": "commodity", "SLV": "commodity", "GDX": "commodity",
    "EEM": "intl",
    "XLF": "financials", "XLV": "healthcare", "XLI": "industrials",
    "XLU": "utilities", "XLP": "staples", "XLY": "discretionary",
    "XLB": "materials", "XLRE": "realestate", "XLC": "comms",
}


@dataclass
class LimitVerdict:
    adjusted_quantity: float
    blocked: bool = False
    reason: str = ""
    applied: list[str] = field(default_factory=list)


class PortfolioLimits:
    """Deterministic position/portfolio caps checked against live Alpaca state."""

    def __init__(self, alpaca) -> None:
        self.alpaca            = alpaca
        self.max_position_pct  = float(os.getenv("MAX_POSITION_PCT", "0.10"))
        self.max_sector_pct    = float(os.getenv("MAX_SECTOR_PCT", "0.30"))
        self.max_open          = int(os.getenv("MAX_OPEN_POSITIONS", "10"))
        self.drawdown_suspend  = float(os.getenv("MAX_DRAWDOWN_SUSPEND_PCT", "0.15"))
        self.initial_equity    = float(os.getenv("PORTFOLIO_INITIAL_EQUITY", "100000"))

    def enforce(self, symbol: str, direction: str, quantity: float, price: float) -> LimitVerdict:
        direction = (direction or "").upper()

        # Closing a position always reduces risk — never block it.
        if direction in _CLOSING_DIRECTIONS:
            return LimitVerdict(adjusted_quantity=quantity)

        if direction not in _OPENING_DIRECTIONS:
            return LimitVerdict(adjusted_quantity=quantity)

        if price <= 0:
            return LimitVerdict(
                adjusted_quantity=0, blocked=True,
                reason="portfolio limits: no reference price for sizing — fail-closed",
            )

        try:
            acct      = self.alpaca.get_account()
            equity    = float(acct.get("equity", 0) or 0)
            positions = self.alpaca.get_all_positions() or []
        except Exception as exc:
            log.error("portfolio_limits.state_unavailable", error=str(exc))
            return LimitVerdict(
                adjusted_quantity=0, blocked=True,
                reason="portfolio limits: live portfolio state unavailable — fail-closed",
            )

        if equity <= 0:
            return LimitVerdict(
                adjusted_quantity=0, blocked=True,
                reason="portfolio limits: account equity unavailable — fail-closed",
            )

        # ── Drawdown suspend (BUY only — shorts are the bear-regime tool) ─────
        dd_floor = self.initial_equity * (1.0 - self.drawdown_suspend)
        if direction == "BUY" and equity <= dd_floor:
            return LimitVerdict(
                adjusted_quantity=0, blocked=True,
                reason=(f"portfolio limits: equity ${equity:,.0f} below drawdown floor "
                        f"${dd_floor:,.0f} (-{self.drawdown_suspend:.0%}) — new BUYs suspended"),
            )

        held_value: dict[str, float] = {}
        for p in positions:
            try:
                held_value[p["symbol"]] = abs(float(p.get("market_value", 0) or 0))
            except (TypeError, ValueError):
                continue

        # ── Max open positions (only blocks NEW symbols) ──────────────────────
        if symbol not in held_value and len(held_value) >= self.max_open:
            return LimitVerdict(
                adjusted_quantity=0, blocked=True,
                reason=(f"portfolio limits: {len(held_value)} open positions ≥ max "
                        f"{self.max_open} — no new symbols"),
            )

        applied: list[str] = []
        adjusted = float(quantity)

        # ── Per-symbol 10% cap (existing exposure counts against it) ──────────
        cap_dollars   = equity * self.max_position_pct
        existing      = held_value.get(symbol, 0.0)
        available     = cap_dollars - existing
        if available < price:  # can't even add one share
            return LimitVerdict(
                adjusted_quantity=0, blocked=True,
                reason=(f"portfolio limits: {symbol} exposure ${existing:,.0f} already at/above "
                        f"{self.max_position_pct:.0%} cap (${cap_dollars:,.0f})"),
            )
        max_shares = math.floor(available / price)
        if adjusted > max_shares:
            applied.append(
                f"position cap {self.max_position_pct:.0%}: qty {adjusted:.0f}→{max_shares}"
            )
            adjusted = float(max_shares)

        # ── Sector 30% cap ────────────────────────────────────────────────────
        sector = SECTOR_MAP.get(symbol, symbol)
        sector_exposure = sum(
            v for s, v in held_value.items() if SECTOR_MAP.get(s, s) == sector
        )
        sector_available = equity * self.max_sector_pct - sector_exposure
        if sector_available < price:
            return LimitVerdict(
                adjusted_quantity=0, blocked=True,
                reason=(f"portfolio limits: sector '{sector}' exposure ${sector_exposure:,.0f} "
                        f"at/above {self.max_sector_pct:.0%} cap"),
            )
        sector_max_shares = math.floor(sector_available / price)
        if adjusted > sector_max_shares:
            applied.append(
                f"sector cap {self.max_sector_pct:.0%} ({sector}): qty {adjusted:.0f}→{sector_max_shares}"
            )
            adjusted = float(sector_max_shares)

        if adjusted <= 0:
            return LimitVerdict(
                adjusted_quantity=0, blocked=True,
                reason="portfolio limits: caps reduce quantity to zero",
            )

        verdict = LimitVerdict(
            adjusted_quantity=adjusted,
            blocked=False,
            reason="; ".join(applied),
            applied=applied,
        )
        if applied:
            log.info("portfolio_limits.resized", symbol=symbol,
                     requested=quantity, adjusted=adjusted, caps=applied)
        return verdict
