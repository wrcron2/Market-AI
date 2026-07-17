"""
cli.py — Backtest Command Line Interface
=========================================
Usage:
  python -m backtest run --strategy momentum_breakout
  python -m backtest run --strategy mean_reversion
  python -m backtest run --strategy all
  python -m backtest run --strategy momentum_breakout --symbols AAPL,MSFT,NVDA

Output: prints summary report + saves JSON results to backtest_results/
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime

import structlog

log = structlog.get_logger(__name__)

RESULTS_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "backtest_results")


def run_strategy(strategy_name: str, symbols: list[str] | None = None) -> dict:
    from backtest.runner import BacktestRunner
    from backtest.report import (
        GATE_MIN_TRADES, GATE_MIN_SHARPE, GATE_OUT_OF_SAMPLE_RATIO,
    )

    print(f"\n🔍 Running backtest: {strategy_name}")
    print(f"   Symbols: {len(symbols) if symbols else 'default (24-ETF universe / 5-ETF for dual_momentum)'}")
    print(f"   Gate: ≥{GATE_MIN_TRADES} trades, Sharpe ≥{GATE_MIN_SHARPE}, OOS ≥{GATE_OUT_OF_SAMPLE_RATIO:.0%} of in-sample\n")

    start = time.time()
    runner = BacktestRunner(strategy_name, symbols)
    result = runner.run()
    elapsed = time.time() - start

    print(result.summary())
    print(f"   Completed in {elapsed:.1f}s")

    # Save results
    os.makedirs(RESULTS_DIR, exist_ok=True)
    timestamp  = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename   = f"{strategy_name}_{timestamp}.json"
    filepath   = os.path.join(RESULTS_DIR, filename)

    result_dict = {
        "strategy":           result.strategy,
        "timestamp":          timestamp,
        "passed":             result.passed,
        "fail_reasons":       result.fail_reasons,
        "symbols_tested":     result.symbols_tested,
        "total_trades":       result.total_trades,
        "win_rate":           round(result.win_rate, 4),
        "avg_return_pct":     round(result.avg_return_pct, 4),
        "profit_factor":      round(result.profit_factor, 4),
        "boundary_date":      result.boundary_date,
        "max_drawdown_pct":   round(result.max_drawdown_pct, 4),
        "sharpe_ratio":       round(result.sharpe_ratio, 4),
        "in_sample_sharpe":   round(result.in_sample_sharpe, 4),
        "out_sample_sharpe":  round(result.out_sample_sharpe, 4),
        "in_sample_win_rate": round(result.in_sample_win_rate, 4),
        "out_sample_win_rate":round(result.out_sample_win_rate, 4),
        "equity_curve":       [round(v, 2) for v in result.equity_curve[-100:]],  # last 100 points
        "elapsed_seconds":    round(elapsed, 1),
    }

    with open(filepath, "w") as f:
        json.dump(result_dict, f, indent=2)
    print(f"   Results saved: {filepath}\n")

    return result_dict


def main():
    parser = argparse.ArgumentParser(description="MarketFlow AI Backtester")
    sub = parser.add_subparsers(dest="command")

    run_parser = sub.add_parser("run", help="Run a backtest")
    run_parser.add_argument(
        "--strategy", required=True,
        choices=["momentum_breakout", "mean_reversion", "dual_momentum", "all"],
        help="Strategy to backtest",
    )
    run_parser.add_argument(
        "--symbols", default=None,
        help="Comma-separated symbol list (default: 100-symbol S&P sample)",
    )

    args = parser.parse_args()

    if args.command != "run":
        parser.print_help()
        sys.exit(1)

    symbols = [s.strip().upper() for s in args.symbols.split(",")] if args.symbols else None

    strategies = (
        ["momentum_breakout", "mean_reversion", "dual_momentum"]
        if args.strategy == "all"
        else [args.strategy]
    )

    all_passed = True
    for s in strategies:
        result = run_strategy(s, symbols)
        if not result["passed"]:
            all_passed = False

    if len(strategies) > 1:
        print("\n" + "="*60)
        if all_passed:
            print("✅ ALL STRATEGIES PASSED — Phase 3 gate cleared")
        else:
            print("❌ PHASE 3 GATE NOT CLEARED — review fail reasons above")
        print("="*60 + "\n")

    sys.exit(0 if all_passed else 1)


if __name__ == "__main__":
    main()
