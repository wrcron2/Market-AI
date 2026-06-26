"""
runner.py — Backtest Runner
============================
Downloads 5+ years of historical data via yfinance, applies strategy rules
deterministically, simulates trades with ATR-based stops and take-profits,
and returns a BacktestResult with in/out-of-sample split.

Walk-forward split: 60% in-sample, 40% out-of-sample (time-ordered, never shuffled).
Slippage: fill at close + ATR × 0.1 for BUY, close - ATR × 0.1 for SELL.
Commission: $0.005/share (Alpaca paper) or $0.0035/share (IBKR live).
"""
from __future__ import annotations

import os
import time
from typing import List

import pandas as pd
import yfinance as yf
import structlog

from .indicators import compute_all
from .strategy import STRATEGIES, Signal
from .report import Trade, BacktestResult, compute_report

log = structlog.get_logger(__name__)

# ── Config ─────────────────────────────────────────────────────────────────────
LOOKBACK_YEARS    = 5
STOP_LOSS_PCT     = float(os.getenv("STOP_LOSS_PCT",    "5.0"))
TAKE_PROFIT_PCT   = float(os.getenv("TAKE_PROFIT_PCT",  "15.0"))
MAX_HOLD_DAYS     = int(os.getenv("MAX_HOLD_DAYS",      "5"))
ACCOUNT_SIZE      = float(os.getenv("SIM_INITIAL_CASH", "100000"))
COMMISSION        = 0.005   # $0.005/share
SLIPPAGE_ATR_MULT = 0.1     # fill at close ± ATR × 0.1
IN_SAMPLE_RATIO   = 0.60    # 60% in-sample, 40% out-of-sample
MIN_DATA_DAYS     = 252 * 2 # minimum 2 years of data per symbol


class BacktestRunner:

    def __init__(self, strategy_name: str, symbols: list[str] | None = None) -> None:
        if strategy_name not in STRATEGIES:
            raise ValueError(f"Unknown strategy: {strategy_name}. Choose from {list(STRATEGIES)}")
        self.strategy_name = strategy_name
        self.strategy_fn   = STRATEGIES[strategy_name]
        self.symbols       = symbols or self._default_symbols()

    def run(self) -> BacktestResult:
        """Run full backtest and return results."""
        log.info("backtest.start", strategy=self.strategy_name, symbols=len(self.symbols))

        all_trades: list[Trade] = []
        symbols_with_data = 0
        split_trade_idx = 0  # will track in-sample / out-of-sample boundary

        period = f"{LOOKBACK_YEARS + 1}y"

        for i, symbol in enumerate(self.symbols):
            if i % 50 == 0:
                log.info("backtest.progress", done=i, total=len(self.symbols),
                         trades=len(all_trades))
            try:
                df = self._download(symbol, period)
                if df is None or len(df) < MIN_DATA_DAYS:
                    continue

                df = compute_all(df)
                df = df.dropna()
                if len(df) < MIN_DATA_DAYS:
                    continue

                symbols_with_data += 1
                trades = self._simulate(df, symbol)
                all_trades.extend(trades)

            except Exception as exc:
                log.debug("backtest.symbol_error", symbol=symbol, error=str(exc))

        # Sort trades chronologically and find in/out split
        all_trades.sort(key=lambda t: t.entry_date)
        split_trade_idx = int(len(all_trades) * IN_SAMPLE_RATIO)

        log.info("backtest.complete", strategy=self.strategy_name,
                 symbols_with_data=symbols_with_data, total_trades=len(all_trades))

        return compute_report(
            strategy=self.strategy_name,
            trades=all_trades,
            symbols_tested=symbols_with_data,
            split_idx=split_trade_idx,
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
        days_held   = 0

        for i in range(20, len(df)):  # start after warmup period
            row  = df.iloc[i]
            date = str(df.index[i])[:10]

            if in_trade:
                days_held += 1
                close = float(row["Close"])

                exit_reason = None
                exit_price  = close

                if direction == "BUY":
                    if close <= stop_loss:
                        exit_reason = "stop_loss"
                        exit_price  = stop_loss
                    elif close >= take_profit:
                        exit_reason = "take_profit"
                        exit_price  = take_profit
                elif direction == "SELL":
                    if close >= stop_loss:
                        exit_reason = "stop_loss"
                        exit_price  = stop_loss
                    elif close <= take_profit:
                        exit_reason = "take_profit"
                        exit_price  = take_profit

                if exit_reason is None and days_held >= MAX_HOLD_DAYS:
                    exit_reason = "max_hold"

                if exit_reason:
                    commission = quantity * COMMISSION
                    trade = Trade(
                        symbol=symbol,
                        direction=direction,
                        entry_price=entry_price,
                        exit_price=exit_price - commission / quantity,
                        quantity=quantity,
                        entry_date=entry_date,
                        exit_date=date,
                        exit_reason=exit_reason,
                    )
                    trades.append(trade)
                    in_trade = False

            else:
                signal = self.strategy_fn(row, ACCOUNT_SIZE)
                if signal is None:
                    continue

                atr_val    = float(row.get("atr", 0))
                slip       = atr_val * SLIPPAGE_ATR_MULT
                close      = float(row["Close"])

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
                days_held   = 0

        # Close any open position at end of data
        if in_trade and len(df) > 0:
            last_row  = df.iloc[-1]
            last_date = str(df.index[-1])[:10]
            trade = Trade(
                symbol=symbol,
                direction=direction,
                entry_price=entry_price,
                exit_price=float(last_row["Close"]),
                quantity=quantity,
                entry_date=entry_date,
                exit_date=last_date,
                exit_reason="end_of_data",
            )
            trades.append(trade)

        return trades

    @staticmethod
    def _default_symbols() -> list[str]:
        """S&P 500 representative sample — enough for 100+ trades."""
        return [
            "AAPL","MSFT","NVDA","AMZN","GOOGL","META","TSLA","BRK-B","LLY","JPM",
            "V","UNH","XOM","MA","AVGO","HD","PG","COST","JNJ","MRK","ABBV","BAC",
            "CRM","NFLX","CVX","AMD","KO","PEP","TMO","MCD","CSCO","ABT","DHR","WMT",
            "ACN","LIN","TXN","NEE","PM","ORCL","ADBE","QCOM","RTX","HON","MS","GE",
            "AMAT","AMGN","ISRG","CAT","IBM","SPGI","GS","AXP","BLK","SYK","MDT",
            "DE","GILD","PLD","ELV","REGN","NOW","ADI","MU","LRCX","ADP","ZTS","BSX",
            "CI","SCHW","TJX","MMC","CB","SO","DUK","CL","CME","HUM","VRTX","FI",
            "EOG","PGR","SLB","WM","NOC","GD","FDX","APD","ECL","MCHP","KLAC","SNPS",
            "CDNS","ROP","FTNT","DXCM","IDXX","MTD","CTAS","VRSK","BR","ANSS","TT",
        ]
