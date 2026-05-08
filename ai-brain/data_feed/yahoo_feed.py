"""
yahoo_feed.py — Yahoo Finance Market Data Feed
================================================
Pulls REAL market data from Yahoo Finance using the `yfinance` library.

## No API Key Required
yfinance scrapes Yahoo Finance's public endpoints for free.
There is no registration, no rate-limit tier, and no API key.
Just: pip install yfinance

## What this provides
- Last 5-day OHLCV (Open, High, Low, Close, Volume) from actual markets
- Technical indicators computed from price history (RSI, MACD, Bollinger Bands, ATR)
- VIX and SPY trend for macro context
- All formatted as the exact `market_snapshot` dict the orchestrator expects

## Limitations of yfinance vs. professional feeds
| Feature          | yfinance             | Alpaca / Polygon.io  |
|------------------|----------------------|----------------------|
| Cost             | Free                 | Freemium / Paid      |
| Latency          | ~15-min delay        | Real-time            |
| Reliability      | Best-effort scraping | SLA-backed API       |
| Tick data        | No                   | Yes                  |
| Options chain    | Yes (limited)        | Yes                  |
| API key needed   | ❌ No               | ✅ Yes               |

For production HFT you'll want Alpaca or Polygon. For backtesting,
paper trading, and early signal development, yfinance is excellent.
"""
from __future__ import annotations

import time
from typing import Any

import pandas as pd
import structlog
import yfinance as yf

log = structlog.get_logger(__name__)


class YahooFinanceFeed:
    """
    Provides OHLCV + technical indicators for a list of symbols
    using the free yfinance library (no API key required).

    Usage:
        feed = YahooFinanceFeed(["AAPL", "SPY", "NVDA"])
        snapshots = feed.get_snapshots()
        # → list of market_snapshot dicts ready for the orchestrator
    """

    def __init__(
        self,
        symbols: list[str],
        lookback_days: int = 30,   # days of history to compute indicators
        min_volume: int = 500_000, # skip illiquid snapshots
    ) -> None:
        self.symbols = symbols
        self.lookback_days = lookback_days
        self.min_volume = min_volume

    # ── Public API ─────────────────────────────────────────────────────────────

    # Symbols per yf.download() batch — 300 is the sweet spot for reliability
    _CHUNK_SIZE = 300

    def get_snapshots(self) -> list[dict[str, Any]]:
        """
        Download data and return a list of market_snapshot dicts.
        Large symbol lists are split into chunks to avoid Yahoo throttling.
        Symbols with errors or insufficient volume are silently skipped.
        """
        snapshots: list[dict[str, Any]] = []
        period = f"{self.lookback_days + 5}d"

        # Fetch macro context once (shared across all symbols)
        vix_val   = self._fetch_last_close("^VIX")
        spy_trend = self._classify_trend("SPY")

        # Split into chunks and download each
        chunks = [
            self.symbols[i : i + self._CHUNK_SIZE]
            for i in range(0, len(self.symbols), self._CHUNK_SIZE)
        ]
        log.info(
            "yahoo_feed.downloading",
            total_symbols=len(self.symbols),
            chunks=len(chunks),
            period=period,
        )

        for idx, chunk in enumerate(chunks):
            log.debug("yahoo_feed.chunk", index=idx + 1, size=len(chunk))
            chunk_data = self._download_chunk(chunk, period)
            if chunk_data is None:
                continue

            for symbol in chunk:
                try:
                    snapshot = self._build_snapshot(
                        symbol, chunk_data, len(chunk), vix_val, spy_trend
                    )
                    if snapshot:
                        snapshots.append(snapshot)
                except Exception as exc:
                    log.warning("yahoo_feed.symbol_error", symbol=symbol, error=str(exc))

        log.info("yahoo_feed.complete", count=len(snapshots))
        return snapshots

    def _download_chunk(
        self, symbols: list[str], period: str
    ) -> "pd.DataFrame | None":
        """Download one chunk of symbols. Returns None on failure."""
        try:
            return yf.download(
                tickers=symbols,
                period=period,
                interval="1d",
                group_by="ticker",
                auto_adjust=True,
                progress=False,
                threads=True,
            )
        except Exception as exc:
            log.error("yahoo_feed.chunk_download_error", error=str(exc))
            return None

    def get_live_price(self, symbol: str) -> float | None:
        """
        Fetch the most recent closing price for a single symbol.
        Used by the simulated executor to compute fill prices.
        """
        try:
            ticker = yf.Ticker(symbol)
            # .fast_info gives the most recent price without downloading history
            price = ticker.fast_info.get("last_price") or ticker.fast_info.get("previous_close")
            return float(price) if price else None
        except Exception as exc:
            log.warning("yahoo_feed.live_price_error", symbol=symbol, error=str(exc))
            return None

    # ── Private helpers ────────────────────────────────────────────────────────

    def _build_snapshot(
        self,
        symbol: str,
        tickers_data: pd.DataFrame,
        chunk_size: int,
        vix: float,
        spy_trend: str,
    ) -> dict[str, Any] | None:
        """Extract one symbol's data and compute technical indicators."""

        # Handle both single-symbol and multi-symbol download formats
        if chunk_size == 1:
            df = tickers_data
        else:
            df = tickers_data[symbol] if symbol in tickers_data.columns.get_level_values(0) else None

        if df is None or df.empty or len(df) < 14:
            log.debug("yahoo_feed.insufficient_data", symbol=symbol)
            return None

        df = df.dropna()
        if len(df) < 14:
            return None

        latest = df.iloc[-1]
        volume = int(latest.get("Volume", 0))

        if volume < self.min_volume:
            log.debug("yahoo_feed.low_volume", symbol=symbol, volume=volume)
            return None

        close_series = df["Close"].astype(float)
        high_series  = df["High"].astype(float)
        low_series   = df["Low"].astype(float)
        vol_series   = df["Volume"].astype(float)

        return {
            "symbol": symbol,
            "ohlcv": {
                "open":   round(float(latest["Open"]),   2),
                "high":   round(float(latest["High"]),   2),
                "low":    round(float(latest["Low"]),    2),
                "close":  round(float(latest["Close"]),  2),
                "volume": volume,
            },
            "indicators": {
                "rsi_14":       round(self._rsi(close_series, 14), 2),
                "macd":         round(self._macd(close_series), 4),
                "macd_signal":  round(self._macd_signal(close_series), 4),
                "bb_upper":     round(self._bb_upper(close_series, 20), 2),
                "bb_lower":     round(self._bb_lower(close_series, 20), 2),
                "atr_14":       round(self._atr(high_series, low_series, close_series, 14), 2),
                "volume_sma20": int(vol_series.rolling(20).mean().iloc[-1]),
                "sma_20":       round(close_series.rolling(20).mean().iloc[-1], 2),
                "sma_50":       round(close_series.rolling(min(50, len(df))).mean().iloc[-1], 2),
            },
            "market_context": {
                "vix":         round(vix, 2),
                "spy_trend":   spy_trend,
                "sector_flow": self._classify_market_sentiment(vix),
            },
            "_source":    "yfinance",
            "_timestamp": int(time.time()),
        }

    # ── Technical indicators (pure pandas — no TA-Lib dependency) ─────────────

    def _rsi(self, close: pd.Series, period: int = 14) -> float:
        """Relative Strength Index."""
        delta = close.diff()
        gain  = delta.clip(lower=0).rolling(period).mean()
        loss  = (-delta.clip(upper=0)).rolling(period).mean()
        rs    = gain / loss.replace(0, float("inf"))
        rsi   = 100 - (100 / (1 + rs))
        return float(rsi.iloc[-1]) if not rsi.empty else 50.0

    def _ema(self, close: pd.Series, span: int) -> pd.Series:
        return close.ewm(span=span, adjust=False).mean()

    def _macd(self, close: pd.Series) -> float:
        """MACD line (12 EMA − 26 EMA)."""
        return float((self._ema(close, 12) - self._ema(close, 26)).iloc[-1])

    def _macd_signal(self, close: pd.Series) -> float:
        """MACD signal line (9 EMA of MACD)."""
        macd_line = self._ema(close, 12) - self._ema(close, 26)
        return float(self._ema(macd_line, 9).iloc[-1])

    def _bb_upper(self, close: pd.Series, window: int = 20) -> float:
        """Bollinger Band upper (SMA + 2σ)."""
        sma   = close.rolling(window).mean()
        std   = close.rolling(window).std()
        return float((sma + 2 * std).iloc[-1])

    def _bb_lower(self, close: pd.Series, window: int = 20) -> float:
        """Bollinger Band lower (SMA − 2σ)."""
        sma   = close.rolling(window).mean()
        std   = close.rolling(window).std()
        return float((sma - 2 * std).iloc[-1])

    def _atr(self, high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> float:
        """Average True Range."""
        prev_close = close.shift(1)
        tr = pd.concat([
            high - low,
            (high - prev_close).abs(),
            (low  - prev_close).abs(),
        ], axis=1).max(axis=1)
        return float(tr.rolling(period).mean().iloc[-1])

    # ── Macro context ──────────────────────────────────────────────────────────

    def _fetch_last_close(self, ticker: str) -> float:
        """Fetch the last closing price for a ticker (e.g. '^VIX')."""
        try:
            df = yf.download(ticker, period="5d", interval="1d",
                             auto_adjust=True, progress=False)
            if df.empty:
                return 18.0  # neutral VIX fallback
            return float(df["Close"].iloc[-1])
        except Exception:
            return 18.0

    def _classify_trend(self, symbol: str) -> str:
        """Classify a symbol's 20-day trend as uptrend / downtrend / sideways."""
        try:
            df = yf.download(symbol, period="30d", interval="1d",
                             auto_adjust=True, progress=False)
            if len(df) < 5:
                return "sideways"
            close = df["Close"].astype(float)
            sma   = close.rolling(20).mean().iloc[-1]
            last  = close.iloc[-1]
            if last > sma * 1.01:
                return "uptrend"
            elif last < sma * 0.99:
                return "downtrend"
            return "sideways"
        except Exception:
            return "sideways"

    def _classify_market_sentiment(self, vix: float) -> str:
        """Rough risk-on / risk-off classification from VIX level."""
        if vix < 15:
            return "risk-on"
        elif vix > 25:
            return "risk-off"
        return "neutral"
