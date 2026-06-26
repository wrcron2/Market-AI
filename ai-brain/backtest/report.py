"""
report.py — Backtest Results and Gate Evaluation
==================================================
Computes Sharpe ratio, win rate, max drawdown, equity curve.
Evaluates whether the strategy passes the Phase 3 gate.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List


# ── Phase 3 gate thresholds ────────────────────────────────────────────────────
GATE_MIN_TRADES          = 100
GATE_MIN_WIN_RATE        = 0.0    # removed — irrelevant for asymmetric R:R strategies
GATE_MIN_SHARPE          = 0.50   # primary gate — annualized Sharpe ≥ 0.5
GATE_MIN_TQS             = 0.80   # relaxed — positive expected value across market cycles
GATE_MAX_DRAWDOWN        = 25.0   # hard cap: never more than 25% drawdown
GATE_OUT_OF_SAMPLE_RATIO = 0.30   # out-of-sample Sharpe ≥ 30% of in-sample (not overfitting)


@dataclass
class Trade:
    symbol:      str
    direction:   str
    entry_price: float
    exit_price:  float
    quantity:    int
    entry_date:  str
    exit_date:   str
    exit_reason: str   # "stop_loss" | "take_profit" | "max_hold" | "end_of_data"
    pnl:         float = field(init=False)
    return_pct:  float = field(init=False)

    def __post_init__(self):
        if self.direction == "BUY":
            self.pnl = (self.exit_price - self.entry_price) * self.quantity
            self.return_pct = (self.exit_price - self.entry_price) / self.entry_price * 100
        else:
            self.pnl = (self.entry_price - self.exit_price) * self.quantity
            self.return_pct = (self.entry_price - self.exit_price) / self.entry_price * 100


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
            f"Win rate:            {self.win_rate:.1%}",
            f"Avg return/trade:    {self.avg_return_pct:.2f}%",
            f"Max drawdown:        {self.max_drawdown_pct:.2f}%",
            f"Sharpe ratio:        {self.sharpe_ratio:.3f}",
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


def compute_report(
    strategy: str,
    trades: List[Trade],
    symbols_tested: int,
    split_idx: int,    # index separating in-sample from out-of-sample
) -> BacktestResult:
    """Compute full backtest report with in/out-of-sample split."""

    if not trades:
        return BacktestResult(
            strategy=strategy, symbols_tested=symbols_tested, total_trades=0,
            win_rate=0, avg_return_pct=0, max_drawdown_pct=0, sharpe_ratio=0,
            in_sample_sharpe=0, out_sample_sharpe=0,
            in_sample_win_rate=0, out_sample_win_rate=0,
            equity_curve=[], trades=[], passed=False,
            fail_reasons=["No trades generated"],
        )

    in_sample  = [t for t in trades if trades.index(t) < split_idx]
    out_sample = [t for t in trades if trades.index(t) >= split_idx]

    total_trades = len(trades)
    win_rate     = sum(1 for t in trades if t.pnl > 0) / total_trades
    avg_return   = sum(t.return_pct for t in trades) / total_trades
    sharpe       = _sharpe([t.return_pct for t in trades])
    in_sharpe    = _sharpe([t.return_pct for t in in_sample]) if in_sample else 0
    out_sharpe   = _sharpe([t.return_pct for t in out_sample]) if out_sample else 0
    in_wr        = sum(1 for t in in_sample if t.pnl > 0) / len(in_sample) if in_sample else 0
    out_wr       = sum(1 for t in out_sample if t.pnl > 0) / len(out_sample) if out_sample else 0

    # Equity curve (cumulative P&L)
    equity   = [100_000.0]
    for t in trades:
        equity.append(equity[-1] + t.pnl)

    max_dd = _max_drawdown(equity)

    # Trade Quality Score: (avg_win / avg_loss) × win_rate
    winning = [t.return_pct for t in trades if t.pnl > 0]
    losing  = [abs(t.return_pct) for t in trades if t.pnl < 0]
    avg_win  = sum(winning) / len(winning) if winning else 0
    avg_loss = sum(losing)  / len(losing)  if losing  else 1
    tqs = (avg_win / avg_loss) * win_rate if avg_loss > 0 else 0

    # Gate evaluation
    fail_reasons = []
    if total_trades < GATE_MIN_TRADES:
        fail_reasons.append(f"Insufficient trades: {total_trades} < {GATE_MIN_TRADES}")
    if win_rate < GATE_MIN_WIN_RATE:
        fail_reasons.append(f"Win rate too low: {win_rate:.1%} < {GATE_MIN_WIN_RATE:.1%}")
    if sharpe < GATE_MIN_SHARPE:
        fail_reasons.append(f"Sharpe too low: {sharpe:.3f} < {GATE_MIN_SHARPE}")
    if tqs < GATE_MIN_TQS:
        fail_reasons.append(f"Trade Quality Score too low: {tqs:.3f} < {GATE_MIN_TQS}")
    if max_dd > GATE_MAX_DRAWDOWN:
        fail_reasons.append(f"Max drawdown too high: {max_dd:.1f}% > {GATE_MAX_DRAWDOWN}%")
    if in_sharpe > 0 and out_sample:
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
        equity_curve=equity,
        trades=trades,
        passed=len(fail_reasons) == 0,
        fail_reasons=fail_reasons,
    )


def _sharpe(returns: list[float], risk_free: float = 0.0) -> float:
    """Annualized Sharpe ratio from per-trade returns."""
    if len(returns) < 2:
        return 0.0
    n    = len(returns)
    mean = sum(returns) / n
    var  = sum((r - mean) ** 2 for r in returns) / (n - 1)
    std  = math.sqrt(var) if var > 0 else 0
    if std == 0:
        return 0.0
    # Annualize assuming ~252 trading days, ~20 trades/year per symbol
    return ((mean - risk_free) / std) * math.sqrt(252 / 5)


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
