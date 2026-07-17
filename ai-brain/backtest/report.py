"""
report.py — Backtest Results and Gate Evaluation
==================================================
Sharpe and drawdown are computed from a DAILY portfolio equity curve (trade
P&L attributed on exit date), not from per-trade returns — the previous
per-trade annualization (sqrt(252/5)) assumed ~5-day holds and inflated
Sharpe for long-hold trend trades.

In/out-of-sample is split by CALENDAR DATE (60/40 of the traded date range),
so both windows are contiguous market periods — never interleaved trades.

Phase 3 gate (CLAUDE.md): 5+ years data, ≥100 trades, out-of-sample ≥ 50%
of in-sample performance. Win rate is REPORTED but not gated: asymmetric
R:R trend strategies run 30–40% win rates by design; expectancy is gated
through Sharpe and Trade Quality Score instead.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List

import pandas as pd

# ── Phase 3 gate thresholds ────────────────────────────────────────────────────
GATE_MIN_TRADES          = 100
GATE_MIN_WIN_RATE        = 0.0    # reported, not gated (see module docstring)
GATE_MIN_SHARPE          = 0.50   # annualized, from daily equity returns
GATE_MIN_TQS             = 0.80   # (avg_win/avg_loss) × win_rate
GATE_MAX_DRAWDOWN        = 25.0   # hard cap on daily-equity drawdown
GATE_OUT_OF_SAMPLE_RATIO = 0.50   # CLAUDE.md: OOS ≥ 50% of in-sample — do not relax


@dataclass
class Trade:
    symbol:      str
    direction:   str
    entry_price: float
    exit_price:  float
    quantity:    int
    entry_date:  str
    exit_date:   str
    exit_reason: str   # "stop_loss" | "take_profit" | "sma20_cross" | "end_of_data"
    commission:  float = 0.0   # dollars, both legs
    pnl:         float = field(init=False)
    return_pct:  float = field(init=False)

    def __post_init__(self):
        if self.direction == "BUY":
            gross = (self.exit_price - self.entry_price) * self.quantity
        else:
            gross = (self.entry_price - self.exit_price) * self.quantity
        self.pnl = gross - self.commission
        cost_basis = self.entry_price * self.quantity
        self.return_pct = (self.pnl / cost_basis * 100) if cost_basis else 0.0


@dataclass
class BacktestResult:
    strategy:              str
    symbols_tested:        int
    total_trades:          int
    win_rate:              float
    avg_return_pct:        float
    max_drawdown_pct:      float
    sharpe_ratio:          float
    in_sample_sharpe:      float
    out_sample_sharpe:     float
    in_sample_win_rate:    float
    out_sample_win_rate:   float
    profit_factor:         float
    boundary_date:         str
    equity_curve:          List[float]
    trades:                List[Trade]
    passed:                bool
    fail_reasons:          List[str]

    def summary(self) -> str:
        status = "✅ PASSED" if self.passed else "❌ FAILED"
        lines = [
            f"\n{'='*60}",
            f"BACKTEST RESULT: {self.strategy.upper()} — {status}",
            f"{'='*60}",
            f"Symbols tested:      {self.symbols_tested}",
            f"Total trades:        {self.total_trades}",
            f"Win rate:            {self.win_rate:.1%}  (reported, not gated)",
            f"Avg return/trade:    {self.avg_return_pct:.2f}%",
            f"Profit factor:       {self.profit_factor:.2f}",
            f"Max drawdown:        {self.max_drawdown_pct:.2f}%",
            f"Sharpe (daily eq.):  {self.sharpe_ratio:.3f}",
            f"IS/OOS boundary:     {self.boundary_date}",
            f"In-sample Sharpe:    {self.in_sample_sharpe:.3f}",
            f"Out-of-sample Sharpe:{self.out_sample_sharpe:.3f}",
            f"In-sample win rate:  {self.in_sample_win_rate:.1%}",
            f"Out-of-sample win:   {self.out_sample_win_rate:.1%}",
        ]
        if not self.passed:
            lines.append(f"\nFail reasons:")
            for r in self.fail_reasons:
                lines.append(f"  ✗ {r}")
        lines.append(f"{'='*60}\n")
        return "\n".join(lines)


def _empty_result(strategy: str, symbols_tested: int, reason: str) -> BacktestResult:
    return BacktestResult(
        strategy=strategy, symbols_tested=symbols_tested, total_trades=0,
        win_rate=0, avg_return_pct=0, max_drawdown_pct=0, sharpe_ratio=0,
        in_sample_sharpe=0, out_sample_sharpe=0,
        in_sample_win_rate=0, out_sample_win_rate=0,
        profit_factor=0, boundary_date="",
        equity_curve=[], trades=[], passed=False,
        fail_reasons=[reason],
    )


def compute_report(
    strategy: str,
    trades: List[Trade],
    symbols_tested: int,
    boundary_date: str,       # ISO date splitting in-sample from out-of-sample
    base_equity: float = 100_000.0,
) -> BacktestResult:
    """Compute full backtest report with a calendar-date in/out-of-sample split."""

    if not trades:
        return _empty_result(strategy, symbols_tested, "No trades generated")

    trades = sorted(trades, key=lambda t: t.entry_date)
    in_sample  = [t for t in trades if t.entry_date < boundary_date]
    out_sample = [t for t in trades if t.entry_date >= boundary_date]

    # ── Daily equity curve: P&L lands on each trade's exit date ───────────────
    pnl_by_date: dict[str, float] = {}
    for t in trades:
        pnl_by_date[t.exit_date] = pnl_by_date.get(t.exit_date, 0.0) + t.pnl

    pnl_series = pd.Series(pnl_by_date)
    pnl_series.index = pd.to_datetime(pnl_series.index)
    idx = pd.bdate_range(min(t.entry_date for t in trades),
                         max(t.exit_date for t in trades)).union(pnl_series.index)
    daily_pnl = pd.Series(0.0, index=idx)
    daily_pnl.loc[pnl_series.index] = pnl_series

    equity_series = base_equity + daily_pnl.cumsum()
    prev = equity_series.shift(1)
    prev.iloc[0] = base_equity
    daily_returns = (equity_series - prev) / prev

    boundary_ts = pd.to_datetime(boundary_date)
    sharpe     = _sharpe_daily(daily_returns)
    in_sharpe  = _sharpe_daily(daily_returns[daily_returns.index < boundary_ts])
    out_sharpe = _sharpe_daily(daily_returns[daily_returns.index >= boundary_ts])
    max_dd     = _max_drawdown([base_equity] + equity_series.tolist())

    # ── Per-trade stats ───────────────────────────────────────────────────────
    total_trades = len(trades)
    win_rate     = sum(1 for t in trades if t.pnl > 0) / total_trades
    avg_return   = sum(t.return_pct for t in trades) / total_trades
    in_wr  = sum(1 for t in in_sample if t.pnl > 0) / len(in_sample) if in_sample else 0
    out_wr = sum(1 for t in out_sample if t.pnl > 0) / len(out_sample) if out_sample else 0

    winning = [t for t in trades if t.pnl > 0]
    losing  = [t for t in trades if t.pnl < 0]
    gross_win  = sum(t.pnl for t in winning)
    gross_loss = abs(sum(t.pnl for t in losing))
    profit_factor = min(gross_win / gross_loss, 999.0) if gross_loss > 0 else 999.0

    avg_win  = (sum(t.return_pct for t in winning) / len(winning)) if winning else 0
    avg_loss = (sum(abs(t.return_pct) for t in losing) / len(losing)) if losing else 1
    tqs = (avg_win / avg_loss) * win_rate if avg_loss > 0 else 0

    # ── Phase 3 gate ──────────────────────────────────────────────────────────
    fail_reasons = []
    if total_trades < GATE_MIN_TRADES:
        fail_reasons.append(f"Insufficient trades: {total_trades} < {GATE_MIN_TRADES}")
    if sharpe < GATE_MIN_SHARPE:
        fail_reasons.append(f"Sharpe too low: {sharpe:.3f} < {GATE_MIN_SHARPE}")
    if tqs < GATE_MIN_TQS:
        fail_reasons.append(f"Trade Quality Score too low: {tqs:.3f} < {GATE_MIN_TQS}")
    if max_dd > GATE_MAX_DRAWDOWN:
        fail_reasons.append(f"Max drawdown too high: {max_dd:.1f}% > {GATE_MAX_DRAWDOWN}%")
    if not out_sample:
        fail_reasons.append("No out-of-sample trades")
    elif in_sharpe <= 0:
        fail_reasons.append(
            f"In-sample Sharpe ≤ 0 ({in_sharpe:.3f}) — no edge to validate out-of-sample")
    else:
        ratio = out_sharpe / in_sharpe
        if ratio < GATE_OUT_OF_SAMPLE_RATIO:
            fail_reasons.append(
                f"Out-of-sample degradation: {ratio:.1%} of in-sample "
                f"(required ≥ {GATE_OUT_OF_SAMPLE_RATIO:.0%})"
            )

    return BacktestResult(
        strategy=strategy,
        symbols_tested=symbols_tested,
        total_trades=total_trades,
        win_rate=win_rate,
        avg_return_pct=avg_return,
        max_drawdown_pct=max_dd,
        sharpe_ratio=sharpe,
        in_sample_sharpe=in_sharpe,
        out_sample_sharpe=out_sharpe,
        in_sample_win_rate=in_wr,
        out_sample_win_rate=out_wr,
        profit_factor=profit_factor,
        boundary_date=boundary_date,
        equity_curve=[round(v, 2) for v in equity_series.tolist()],
        trades=trades,
        passed=len(fail_reasons) == 0,
        fail_reasons=fail_reasons,
    )


def _sharpe_daily(returns: pd.Series, risk_free_daily: float = 0.0) -> float:
    """Annualized Sharpe from daily equity returns."""
    returns = returns.dropna()
    if len(returns) < 20:
        return 0.0
    std = float(returns.std())
    if std == 0 or math.isnan(std):
        return 0.0
    return float((returns.mean() - risk_free_daily) / std) * math.sqrt(252)


def _max_drawdown(equity: list[float]) -> float:
    """Maximum peak-to-trough drawdown as a percentage."""
    if not equity:
        return 0.0
    peak = equity[0]
    max_dd = 0.0
    for v in equity:
        if v > peak:
            peak = v
        dd = (peak - v) / peak * 100
        if dd > max_dd:
            max_dd = dd
    return max_dd
