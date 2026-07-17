"""
runner.py — Backtest Runner
============================
Downloads 5+ years of historical data via yfinance, applies strategy rules
deterministically, simulates trades, and returns a BacktestResult with a
calendar-date in/out-of-sample split (60/40 of the traded date range).

Honesty model:
- Signals are evaluated on bar close; entries fill at close ± ATR × 0.1
  (adverse slippage).
- Commission is charged on BOTH legs: $0.005/share/leg (Alpaca paper).
- Stop-loss exits fill THROUGH the stop by ATR × 0.1 (gap-adverse);
  take-profits fill at the limit price. SMA20-cross and end-of-data exits
  fill at close ± slippage (adverse).
- Real VIX (^VIX daily close) is merged into every symbol's frame, so the
  mean_reversion VIX filter sees actual history — not a constant.
- Per-position sizing caps at 10% of account to match the live
  portfolio_limits enforcement.

Known limitation (documented, not hidden): symbols simulate independently —
one open trade per symbol, each sized against the full account. Portfolio
concurrency (10-position cap, shared cash) is not modeled here; it is
enforced live by agents/portfolio_limits.py.
"""
from __future__ import annotations

import os
from typing import List

import pandas as pd
import yfinance as yf
import structlog

from .indicators import compute_all
from .strategy import STRATEGIES as STRATEGIES_BASE, Signal
from .strategy_trend import STRATEGIES as STRATEGIES_TREND, TREND_UNIVERSE
from .report import Trade, BacktestResult, compute_report

STRATEGIES = {**STRATEGIES_BASE, **STRATEGIES_TREND}

log = structlog.get_logger(__name__)

# ── Config ─────────────────────────────────────────────────────────────────────
LOOKBACK_YEARS    = 5
ACCOUNT_SIZE      = float(os.getenv("SIM_INITIAL_CASH", "100000"))
COMMISSION        = 0.005   # $/share PER LEG — charged on entry and exit
SLIPPAGE_ATR_MULT = 0.1     # fills move ATR × 0.1 against the trader
IN_SAMPLE_RATIO   = 0.60    # 60% in-sample, 40% out-of-sample (by calendar)
MIN_DATA_DAYS     = 252 * 2 # minimum 2 years of data per symbol


class BacktestRunner:

    def __init__(self, strategy_name: str, symbols: list[str] | None = None) -> None:
        if strategy_name not in STRATEGIES:
            raise ValueError(f"Unknown strategy: {strategy_name}. Choose from {list(STRATEGIES)}")
        self.strategy_name = strategy_name
        self.strategy_fn   = STRATEGIES[strategy_name]
        # dual_momentum uses a fixed 5-ETF universe by design
        if symbols:
            self.symbols = symbols
        elif strategy_name == "dual_momentum":
            self.symbols = TREND_UNIVERSE
        else:
            self.symbols = self._default_symbols()

    def run(self) -> BacktestResult:
        """Run full backtest and return results."""
        log.info("backtest.start", strategy=self.strategy_name, symbols=len(self.symbols))

        period = f"{LOOKBACK_YEARS + 1}y"
        vix    = self._download_vix(period)

        all_trades: list[Trade] = []
        symbols_with_data = 0

        for i, symbol in enumerate(self.symbols):
            if i % 50 == 0:
                log.info("backtest.progress", done=i, total=len(self.symbols),
                         trades=len(all_trades))
            try:
                df = self._download(symbol, period)
                if df is None or len(df) < MIN_DATA_DAYS:
                    continue

                df = compute_all(df, vix=vix)
                df = df.dropna()
                if len(df) < MIN_DATA_DAYS:
                    continue

                symbols_with_data += 1
                trades = self._simulate(df, symbol)
                all_trades.extend(trades)

            except Exception as exc:
                log.debug("backtest.symbol_error", symbol=symbol, error=str(exc))

        # Calendar-date in/out-of-sample boundary over the traded range
        if all_trades:
            d0 = pd.to_datetime(min(t.entry_date for t in all_trades))
            d1 = pd.to_datetime(max(t.entry_date for t in all_trades))
            boundary = (d0 + (d1 - d0) * IN_SAMPLE_RATIO).strftime("%Y-%m-%d")
        else:
            boundary = ""

        log.info("backtest.complete", strategy=self.strategy_name,
                 symbols_with_data=symbols_with_data, total_trades=len(all_trades),
                 boundary=boundary)

        return compute_report(
            strategy=self.strategy_name,
            trades=all_trades,
            symbols_tested=symbols_with_data,
            boundary_date=boundary,
            base_equity=ACCOUNT_SIZE,
        )

    def _download(self, symbol: str, period: str) -> pd.DataFrame | None:
        """Download OHLCV data for symbol."""
        try:
            df = yf.download(
                symbol, period=period, interval="1d",
                auto_adjust=True, progress=False, threads=False,
            )
            if df.empty:
                return None
            # Flatten multi-index if present
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            return df
        except Exception:
            return None

    def _download_vix(self, period: str) -> pd.Series | None:
        """Download real ^VIX closes for the mean_reversion volatility filter."""
        df = self._download("^VIX", period)
        if df is None or "Close" not in df:
            log.warning("backtest.vix_unavailable", note="falling back to neutral 18.5")
            return None
        return df["Close"]

    def _simulate(self, df: pd.DataFrame, symbol: str) -> list[Trade]:
        """Simulate trades on one symbol's full price history."""
        trades: list[Trade] = []
        in_trade = False
        entry_price = 0.0
        entry_date  = ""
        direction   = "BUY"
        stop_loss   = 0.0
        take_profit = 0.0
        quantity    = 0

        for i in range(20, len(df)):  # start after warmup period
            row  = df.iloc[i]
            date = str(df.index[i])[:10]

            if in_trade:
                close   = float(row["Close"])
                atr_now = float(row.get("atr", 0))
                slip    = atr_now * SLIPPAGE_ATR_MULT

                exit_reason = None
                exit_price  = close

                if direction == "BUY":
                    if close <= stop_loss:
                        exit_reason = "stop_loss"
                        exit_price  = stop_loss - slip        # gap through the stop
                    elif take_profit < 999 and close >= take_profit:
                        exit_reason = "take_profit"
                        exit_price  = take_profit             # limit order fills at limit
                    elif take_profit >= 999:
                        # dual_momentum: exit when price crosses below SMA20
                        sma20 = float(row.get("sma_20", close))
                        if close < sma20:
                            exit_reason = "sma20_cross"
                            exit_price  = close - slip
                elif direction == "SELL":
                    if close >= stop_loss:
                        exit_reason = "stop_loss"
                        exit_price  = stop_loss + slip
                    elif close <= take_profit:
                        exit_reason = "take_profit"
                        exit_price  = take_profit

                if exit_reason:
                    trades.append(Trade(
                        symbol=symbol,
                        direction=direction,
                        entry_price=entry_price,
                        exit_price=exit_price,
                        quantity=quantity,
                        entry_date=entry_date,
                        exit_date=date,
                        exit_reason=exit_reason,
                        commission=quantity * COMMISSION * 2,  # both legs
                    ))
                    in_trade = False

            else:
                signal = self.strategy_fn(row, ACCOUNT_SIZE)
                if signal is None:
                    continue

                atr_val = float(row.get("atr", 0))
                slip    = atr_val * SLIPPAGE_ATR_MULT
                close   = float(row["Close"])

                if signal.direction == "BUY":
                    fill = close + slip
                else:
                    fill = close - slip

                in_trade    = True
                entry_price = fill
                entry_date  = date
                direction   = signal.direction
                stop_loss   = signal.stop_loss
                take_profit = signal.take_profit
                quantity    = signal.quantity

        # Close any open position at end of data
        if in_trade and len(df) > 0:
            last_row  = df.iloc[-1]
            last_date = str(df.index[-1])[:10]
            close     = float(last_row["Close"])
            slip      = float(last_row.get("atr", 0)) * SLIPPAGE_ATR_MULT
            trades.append(Trade(
                symbol=symbol,
                direction=direction,
                entry_price=entry_price,
                exit_price=close - slip if direction == "BUY" else close + slip,
                quantity=quantity,
                entry_date=entry_date,
                exit_date=last_date,
                exit_reason="end_of_data",
                commission=quantity * COMMISSION * 2,
            ))

        return trades

    @staticmethod
    def _default_symbols() -> list[str]:
        """Sector and commodity ETFs — cleaner technical signals than individual stocks.
        ETFs move on macro flows and sector rotation, not earnings surprises.
        """
        return [
            # Sector ETFs
            "XLK","XLE","XLF","XLV","XLI","XLU","XLP","XLY","XLB","XLRE","XLC",
            # Broad market ETFs
            "QQQ","IWM","DIA","SPY","MDY",
            # Commodity and bond ETFs
            "GLD","TLT","EEM","GDX","SLV","USO","HYG","LQD",
        ]
