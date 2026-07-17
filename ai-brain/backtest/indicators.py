"""
indicators.py — Technical Indicator Library
============================================
Exact same math as yahoo_feed.py — no duplication, same results.
All functions operate on pandas Series and return scalar floats.
"""
from __future__ import annotations

import pandas as pd


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain  = delta.clip(lower=0).rolling(period).mean()
    loss  = (-delta.clip(upper=0)).rolling(period).mean()
    rs    = gain / loss.replace(0, float("inf"))
    return 100 - (100 / (1 + rs))


def ema(close: pd.Series, span: int) -> pd.Series:
    return close.ewm(span=span, adjust=False).mean()


def macd(close: pd.Series) -> pd.Series:
    return ema(close, 12) - ema(close, 26)


def macd_signal(close: pd.Series) -> pd.Series:
    return ema(macd(close), 9)


def macd_histogram(close: pd.Series) -> pd.Series:
    return macd(close) - macd_signal(close)


def bb_upper(close: pd.Series, window: int = 20) -> pd.Series:
    sma = close.rolling(window).mean()
    std = close.rolling(window).std()
    return sma + 2 * std


def bb_lower(close: pd.Series, window: int = 20) -> pd.Series:
    sma = close.rolling(window).mean()
    std = close.rolling(window).std()
    return sma - 2 * std


def bb_pct_b(close: pd.Series, window: int = 20) -> pd.Series:
    upper = bb_upper(close, window)
    lower = bb_lower(close, window)
    denom = (upper - lower).replace(0, float("nan"))
    return (close - lower) / denom


def atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low  - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def volume_sma(volume: pd.Series, window: int = 20) -> pd.Series:
    return volume.rolling(window).mean()


def sma(close: pd.Series, window: int = 20) -> pd.Series:
    return close.rolling(window).mean()


def compute_all(df: pd.DataFrame, vix: pd.Series | None = None) -> pd.DataFrame:
    """
    Add all indicator columns to a OHLCV DataFrame.
    Input columns: Open, High, Low, Close, Volume
    Returns df with added columns: rsi, macd_hist, bb_pct_b, atr, vol_ratio, sma_20

    vix: real ^VIX daily closes aligned by date. Falls back to a neutral 18.5
    only when the series is unavailable (offline run).
    """
    df = df.copy()
    df["rsi"]       = rsi(df["Close"])
    df["macd_hist"] = macd_histogram(df["Close"])
    df["bb_pct_b"]  = bb_pct_b(df["Close"])
    df["atr"]       = atr(df["High"], df["Low"], df["Close"])
    df["vol_sma20"] = volume_sma(df["Volume"])
    df["vol_ratio"] = df["Volume"] / df["vol_sma20"].replace(0, float("nan"))
    df["sma_20"]    = sma(df["Close"], 20)
    df["sma_50"]    = sma(df["Close"], 50)
    df["atr_pct"]   = df["atr"] / df["Close"] * 100
    df["high_52w"]  = df["Close"].rolling(252).max()
    if vix is not None:
        df["vix"] = vix.reindex(df.index).ffill().fillna(18.5)
    else:
        df["vix"] = 18.5
    return df
