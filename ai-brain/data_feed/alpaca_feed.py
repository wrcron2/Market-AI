"""
alpaca_feed.py — Alpaca Real-Time Market Data Feed
===================================================
Fetches real-time OHLCV bars from Alpaca's data API and computes
the same technical indicators as YahooFinanceFeed.

Used ONLY when TRADING_MODE=live. Paper mode stays on YahooFinanceFeed.

Data source:
  - Bars:    data.alpaca.markets/v2/stocks/bars (5-min, SIP feed)
  - VIX:     Yahoo Finance (^VIX — Alpaca does not carry index data)
  - SPY:     Alpaca bars (classify trend from SMA20)

Feed comparison:
  TRADING_MODE=paper  → YahooFinanceFeed  (_source: "delayed")
  TRADING_MODE=live   → AlpacaFeed        (_source: "realtime")

Alpaca data plans:
  free  → feed=iex  (IEX only — partial NBBO, ok for paper)
  $9/mo → feed=sip  (full SIP consolidated — required for Reg NMS live)
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
import pandas as pd
import structlog
import yfinance as yf

log = structlog.get_logger(__name__)

_ALPACA_DATA_BASE = "https://data.alpaca.markets"
_CHUNK_SIZE = 200  # symbols per Alpaca multi-bar request


class AlpacaFeed:
    """
    Fetches 5-minute bars from Alpaca and computes RSI, MACD, Bollinger,
    ATR, and volume indicators — identical output format to YahooFinanceFeed.

    Set ALPACA_DATA_FEED=sip for live trading (Reg NMS compliant).
    Set ALPACA_DATA_FEED=iex for paper/dev (free, single exchange).
    """

    def __init__(
        self,
        symbols: list[str],
        lookback_bars: int = 50,   # bars of history for indicator calculation
        min_volume: int = 100_000, # skip illiquid snapshots (lower bar for intraday)
    ) -> None:
        self.symbols = symbols
        self.lookback_bars = lookback_bars
        self.min_volume = min_volume

        api_key    = os.getenv("ALPACA_API_KEY", "")
        secret_key = os.getenv("ALPACA_SECRET_KEY", "")
        self._feed = os.getenv("ALPACA_DATA_FEED", "iex")  # "sip" for live

        if not api_key or not secret_key:
            raise RuntimeError("ALPACA_API_KEY and ALPACA_SECRET_KEY must be set.")

        self._client = httpx.Client(
            base_url=_ALPACA_DATA_BASE,
            headers={
                "APCA-API-KEY-ID":     api_key,
                "APCA-API-SECRET-KEY": secret_key,
                "Accept":              "application/json",
            },
            timeout=20,
        )
        log.info("alpaca_feed.init", feed=self._feed, symbols=len(symbols))

    # ── Public API ─────────────────────────────────────────────────────────────

    def get_snapshots(self) -> list[dict[str, Any]]:
        """
        Fetch bars for all symbols and return market_snapshot dicts.
        Same format as YahooFinanceFeed.get_snapshots().
        """
        snapshots: list[dict[str, Any]] = []

        vix_val   = self._fetch_vix()
        spy_trend = self._classify_spy_trend()

        # Calculate start time — enough bars for indicators
        # 50 5-min bars = ~4 hours of trading (one full session)
        start = (datetime.now(timezone.utc) - timedelta(days=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
        end   = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        chunks = [
            self.symbols[i : i + _CHUNK_SIZE]
            for i in range(0, len(self.symbols), _CHUNK_SIZE)
        ]

        log.info("alpaca_feed.fetching", symbols=len(self.symbols), chunks=len(chunks), feed=self._feed)

        for chunk in chunks:
            bars_by_symbol = self._fetch_bars(chunk, start, end)
            for symbol, df in bars_by_symbol.items():
                try:
                    snapshot = self._build_snapshot(symbol, df, vix_val, spy_trend)
                    if snapshot:
                        snapshots.append(snapshot)
                except Exception as exc:
                    log.warning("alpaca_feed.symbol_error", symbol=symbol, error=str(exc))

        log.info("alpaca_feed.complete", count=len(snapshots))
        return snapshots

    # ── Alpaca REST bar fetch ──────────────────────────────────────────────────

    def _fetch_bars(self, symbols: list[str], start: str, end: str) -> dict[str, pd.DataFrame]:
        """Fetch 5-minute bars for a chunk of symbols. Returns {symbol: DataFrame}."""
        try:
            resp = self._client.get(
                "/v2/stocks/bars",
                params={
                    "symbols":   ",".join(symbols),
                    "timeframe": "5Min",
                    "start":     start,
                    "end":       end,
                    "feed":      self._feed,
                    "limit":     1000,
                    "sort":      "asc",
                },
            )
            resp.raise_for_status()
            data = resp.json().get("bars", {})
        except Exception as exc:
            log.error("alpaca_feed.fetch_error", error=str(exc))
            return {}

        result: dict[str, pd.DataFrame] = {}
        for symbol, bars in data.items():
            if not bars or len(bars) < 14:
                continue
            df = pd.DataFrame(bars)
            df = df.rename(columns={"o": "Open", "h": "High", "l": "Low", "c": "Close", "v": "Volume"})
            df = df[["Open", "High", "Low", "Close", "Volume"]].astype(float)
            result[symbol] = df

        return result

    # ── Snapshot builder ───────────────────────────────────────────────────────

    def _build_snapshot(
        self,
        symbol: str,
        df: pd.DataFrame,
        vix: float,
        spy_trend: str,
    ) -> dict[str, Any] | None:
        if df is None or len(df) < 14:
            return None

        latest = df.iloc[-1]
        volume = int(latest["Volume"])

        if volume < self.min_volume:
            return None

        close_s  = df["Close"]
        high_s   = df["High"]
        low_s    = df["Low"]
        vol_s    = df["Volume"]

        return {
            "symbol": symbol,
            "ohlcv": {
                "open":   round(float(latest["Open"]),  2),
                "high":   round(float(latest["High"]),  2),
                "low":    round(float(latest["Low"]),   2),
                "close":  round(float(latest["Close"]), 2),
                "volume": volume,
            },
            "indicators": {
                "rsi_14":       round(self._rsi(close_s, 14), 2),
                "macd":         round(self._macd(close_s), 4),
                "macd_signal":  round(self._macd_signal(close_s), 4),
                "bb_upper":     round(self._bb_upper(close_s), 2),
                "bb_lower":     round(self._bb_lower(close_s), 2),
                "atr_14":       round(self._atr(high_s, low_s, close_s, 14), 2),
                "volume_sma20": int(vol_s.rolling(min(20, len(df))).mean().iloc[-1]),
                "sma_20":       round(close_s.rolling(min(20, len(df))).mean().iloc[-1], 2),
                "sma_50":       round(close_s.rolling(min(50, len(df))).mean().iloc[-1], 2),
            },
            "market_context": {
                "vix":         round(vix, 2),
                "spy_trend":   spy_trend,
                "sector_flow": self._classify_sentiment(vix),
            },
            "_source":    "realtime",  # distinguishes live from delayed
            "_feed":      self._feed,
            "_timestamp": int(time.time()),
        }

    # ── Technical indicators (same logic as YahooFinanceFeed) ─────────────────

    def _rsi(self, close: pd.Series, period: int = 14) -> float:
        delta = close.diff()
        gain  = delta.clip(lower=0).rolling(period).mean()
        loss  = (-delta.clip(upper=0)).rolling(period).mean()
        rs    = gain / loss.replace(0, float("inf"))
        rsi   = 100 - (100 / (1 + rs))
        return float(rsi.iloc[-1]) if not rsi.empty else 50.0

    def _ema(self, close: pd.Series, span: int) -> pd.Series:
        return close.ewm(span=span, adjust=False).mean()

    def _macd(self, close: pd.Series) -> float:
        return float((self._ema(close, 12) - self._ema(close, 26)).iloc[-1])

    def _macd_signal(self, close: pd.Series) -> float:
        macd_line = self._ema(close, 12) - self._ema(close, 26)
        return float(self._ema(macd_line, 9).iloc[-1])

    def _bb_upper(self, close: pd.Series, window: int = 20) -> float:
        sma = close.rolling(window).mean()
        std = close.rolling(window).std()
        return float((sma + 2 * std).iloc[-1])

    def _bb_lower(self, close: pd.Series, window: int = 20) -> float:
        sma = close.rolling(window).mean()
        std = close.rolling(window).std()
        return float((sma - 2 * std).iloc[-1])

    def _atr(self, high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> float:
        prev_close = close.shift(1)
        tr = pd.concat([
            high - low,
            (high - prev_close).abs(),
            (low  - prev_close).abs(),
        ], axis=1).max(axis=1)
        return float(tr.rolling(period).mean().iloc[-1])

    # ── Macro context (VIX stays on Yahoo — Alpaca has no index data) ─────────

    def _fetch_vix(self) -> float:
        try:
            df = yf.download("^VIX", period="5d", interval="1d", auto_adjust=True, progress=False)
            return float(df["Close"].iloc[-1]) if not df.empty else 18.0
        except Exception:
            return 18.0

    def _classify_spy_trend(self) -> str:
        """Classify SPY trend using Alpaca bars (avoids Yahoo dependency for equities)."""
        try:
            start = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
            end   = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            bars  = self._fetch_bars(["SPY"], start, end)
            df    = bars.get("SPY")
            if df is None or len(df) < 5:
                return "sideways"
            close = df["Close"]
            sma   = close.rolling(min(20, len(df))).mean().iloc[-1]
            last  = close.iloc[-1]
            if last > sma * 1.01:
                return "uptrend"
            elif last < sma * 0.99:
                return "downtrend"
            return "sideways"
        except Exception:
            return "sideways"

    def _classify_sentiment(self, vix: float) -> str:
        if vix < 15:
            return "risk-on"
        elif vix > 25:
            return "risk-off"
        return "neutral"
