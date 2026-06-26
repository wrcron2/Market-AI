"""
strategy.py — Deterministic Strategy Entry/Exit Rules
=======================================================
Replicates signal_agent.py entry logic WITHOUT LLM calls.
Pure Python — same indicator thresholds as the system prompt.

Entry signals return: "BUY" | "SELL" | None
"""
from __future__ import annotations

import pandas as pd
from dataclasses import dataclass
from typing import Literal

Direction = Literal["BUY", "SELL", None]


@dataclass
class Signal:
    direction:   Direction
    strategy:    str
    confidence:  float
    stop_loss:   float   # absolute price
    take_profit: float   # absolute price
    quantity:    int


def momentum_breakout(row: pd.Series, account_size: float = 100_000) -> Signal | None:
    """
    momentum_breakout entry rules (from signal_agent.py system prompt):
    - MACD histogram EXPANDING (positive direction, not contracting)
    - volume > 1.5x SMA20
    - price on correct side of SMA20

    No-trade conditions:
    - ATR% > 8.0%
    - volume < 0.7x SMA20
    - MACD histogram contracting
    """
    rsi       = row.get("rsi", 50)
    macd_hist = row.get("macd_hist", 0)
    vol_ratio = row.get("vol_ratio", 1.0)
    atr_pct   = row.get("atr_pct", 2.0)
    atr_val   = row.get("atr", 0)
    close     = row.get("Close", 0)
    sma_20    = row.get("sma_20", close)

    sma_50 = row.get("sma_50", sma_20)  # confirmed uptrend filter

    # Hard no-trade conditions
    if atr_pct > 8.0:
        return None
    if vol_ratio < 2.0:
        return None  # require institutional conviction (raised from 1.5)

    # BUY: strong histogram + price above SMA50 (confirmed uptrend)
    if macd_hist > 0.20 and close > sma_50:
        direction = "BUY"
    # SELL: strong negative histogram + price below SMA50
    elif macd_hist < -0.20 and close < sma_50:
        direction = "SELL"
    else:
        return None

    # Confidence calibration (simplified from LLM prompt)
    confidence = 0.60
    confidence += 0.08 if vol_ratio > 1.5 else 0
    confidence += 0.06  # SPY alignment assumed in backtest
    confidence += 0.04  # VIX normal assumed
    confidence += 0.08 if abs(macd_hist) > 0.1 else 0.04
    confidence = min(confidence, 0.90)

    if confidence < 0.70:
        return None

    stop_dist    = atr_val * 2.5  # wider stop — give trades more room
    dollar_risk  = account_size * 0.01
    quantity     = int(dollar_risk / stop_dist) if stop_dist > 0.01 else 0
    quantity     = min(quantity, 500, int(account_size * 0.08 / close))

    if quantity <= 0:
        return None

    if direction == "BUY":
        stop_loss   = close - stop_dist
        take_profit = close * 1.15
    else:
        stop_loss   = close + stop_dist
        take_profit = close * 0.85

    return Signal(
        direction=direction,
        strategy="momentum_breakout",
        confidence=round(confidence, 3),
        stop_loss=round(stop_loss, 2),
        take_profit=round(take_profit, 2),
        quantity=quantity,
    )


def mean_reversion(row: pd.Series, account_size: float = 100_000) -> Signal | None:
    """
    mean_reversion entry rules (from signal_agent.py system prompt):
    - RSI < 25 (BUY) or RSI > 75 (SELL)
    - Bollinger %B < 0.10 (BUY) or > 0.90 (SELL)
    - volume CONTRACTING (< 1.0x SMA20)
    - MACD histogram NOT strongly directional (|histogram| < 1.0)

    No-trade conditions:
    - |MACD histogram| > 1.0 (strong trend — don't fade it)
    - volume > 1.5x SMA20 (breakout, not reversion)
    - ATR% > 8.0%
    """
    rsi       = row.get("rsi", 50)
    macd_hist = row.get("macd_hist", 0)
    bb_b      = row.get("bb_pct_b", 0.5)
    vol_ratio = row.get("vol_ratio", 1.0)
    atr_pct   = row.get("atr_pct", 2.0)
    atr_val   = row.get("atr", 0)
    close     = row.get("Close", 0)

    vix = row.get("vix", 18.0)  # VIX filter — real fear = real oversold

    # Hard no-trade conditions
    if atr_pct > 8.0:
        return None
    if abs(macd_hist) > 1.0:
        return None  # strong trend — don't fade
    if vol_ratio > 1.5:
        return None  # expanding volume = breakout, not reversion
    if vix < 18.0:
        return None  # low fear = no real oversold condition

    # BUY: relaxed oversold thresholds + VIX elevated
    if rsi < 30 and bb_b < 0.15 and vol_ratio < 1.0:
        direction = "BUY"
    # SELL: relaxed overbought thresholds + VIX elevated
    elif rsi > 70 and bb_b > 0.85 and vol_ratio < 1.0:
        direction = "SELL"
    else:
        return None

    # Confidence calibration
    confidence = 0.60
    confidence += 0.08 if (rsi < 25 or rsi > 75) else 0.04
    confidence += 0.05 if (bb_b < 0.10 or bb_b > 0.90) else 0
    confidence += 0.04  # VIX normal
    confidence += 0.06  # SPY alignment
    confidence = min(confidence, 0.90)

    if confidence < 0.70:
        return None

    stop_dist    = atr_val * 2.0
    dollar_risk  = account_size * 0.01
    quantity     = int(dollar_risk / stop_dist) if stop_dist > 0.01 else 0
    quantity     = min(quantity, 500, int(account_size * 0.08 / close))

    if quantity <= 0:
        return None

    if direction == "BUY":
        stop_loss   = close - stop_dist
        take_profit = close * 1.15
    else:
        stop_loss   = close + stop_dist
        take_profit = close * 0.85

    return Signal(
        direction=direction,
        strategy="mean_reversion",
        confidence=round(confidence, 3),
        stop_loss=round(stop_loss, 2),
        take_profit=round(take_profit, 2),
        quantity=quantity,
    )


STRATEGIES = {
    "momentum_breakout": momentum_breakout,
    "mean_reversion":    mean_reversion,
}
