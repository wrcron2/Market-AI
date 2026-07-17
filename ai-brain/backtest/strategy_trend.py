"""
strategy_trend.py — Pure Trend-Following (Dual Momentum)
=========================================================
Based on Gary Antonacci's Dual Momentum — validated 40+ years, published
Sharpe ratios 0.7-1.2. Simpler than our custom strategies but empirically proven.

Entry: close = 52-week high AND close > SMA50 (absolute momentum)
Exit:  close < SMA20 (trailing stop — exit when momentum fades)
Universe: 5 liquid ETFs only
Hold: indefinitely until SMA20 exit — no fixed hold period

This is the Phase 3 fallback if momentum_breakout and mean_reversion both fail.
"""
from __future__ import annotations

import pandas as pd
from dataclasses import dataclass
from typing import Literal

Direction = Literal["BUY", None]

TREND_UNIVERSE = ["QQQ", "GLD", "TLT", "EEM", "XLE"]

@dataclass
class TrendSignal:
    direction:   str
    strategy:    str
    confidence:  float
    stop_loss:   float
    take_profit: float  # 999 = no fixed TP, exit on SMA20 cross
    quantity:    int


def dual_momentum(row: pd.Series, account_size: float = 100_000) -> TrendSignal | None:
    """
    Entry: close at or near 52-week high AND price > SMA50.
    Exit (in runner): price crosses below SMA20.
    No fixed take-profit — ride the trend until it ends.
    """
    close    = float(row.get("Close", 0))
    sma_20   = float(row.get("sma_20", close))
    sma_50   = float(row.get("sma_50", close))
    high_52w = float(row.get("high_52w", close))
    atr_val  = float(row.get("atr", 0))
    atr_pct  = float(row.get("atr_pct", 2.0))

    if atr_pct > 8.0 or atr_val <= 0:
        return None

    # Entry: new 52-week high proximity (within 2%) AND confirmed uptrend
    near_high = close >= high_52w * 0.98
    uptrend   = close > sma_50

    if not (near_high and uptrend):
        return None

    # Position sizing: 1% account risk / ATR stop
    stop_dist   = atr_val * 2.0
    dollar_risk = account_size * 0.01
    quantity    = int(dollar_risk / stop_dist) if stop_dist > 0.01 else 0
    quantity    = min(quantity, 500, int(account_size * 0.10 / close))  # 10% cap — matches live portfolio_limits

    if quantity <= 0:
        return None

    return TrendSignal(
        direction="BUY",
        strategy="dual_momentum",
        confidence=0.80,
        stop_loss=round(close - stop_dist, 2),
        take_profit=9999.0,  # no fixed TP — exit via SMA20
        quantity=quantity,
    )


STRATEGIES = {
    "dual_momentum": dual_momentum,
}
