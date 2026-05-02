"""
main.py — MarketFlow AI Brain Entry Point
==========================================
Starts the agent pipeline and feeds it real market data.

Trading modes (set via the React dashboard toggle):
  - Yahoo mode (default): Uses yfinance data + simulated order execution.
    No API key needed. No real money at risk. Great for development.
  - IBKR mode:           Uses the Go backend's IBKR client for real execution
    through the Green Light gate. Set PAPER_TRADING=false in .env for live.

Data feed:
  - YahooFinanceFeed pulls REAL OHLCV data from Yahoo Finance for free
    (no API key required — yfinance uses Yahoo's public endpoints)

The brain polls the Go backend's /api/mode endpoint every bar cycle
and switches feeds/executors accordingly.
"""
from __future__ import annotations

import os
import signal
import time
from typing import Any

import httpx
import structlog
from dotenv import load_dotenv

load_dotenv()

import logging
structlog.configure(
    wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
)

log = structlog.get_logger("marketflow.brain")

# ── Watchlist ─────────────────────────────────────────────────────────────────
# These symbols are analysed each bar. Edit freely — yfinance supports any
# US equity, ETF, or index ticker that trades on Yahoo Finance.
WATCHLIST = [
    "AAPL", "MSFT", "NVDA",    # Mega-cap tech
    "SPY",  "QQQ",             # ETFs for market-wide signal
    "TSLA", "AMZN", "GOOGL",   # Growth / volatile
]

BAR_INTERVAL_SECONDS = 300    # 5-minute bar cycle (edit to 60 for 1-min bars)
BACKEND_MODE_URL     = f"http://{os.getenv('BRAIN_HOST', '127.0.0.1')}:{os.getenv('GO_SERVER_PORT', '8080')}/api/mode"


def _get_current_mode() -> str:
    """Poll the Go backend for the current trading mode."""
    try:
        resp = httpx.get(BACKEND_MODE_URL, timeout=3)
        data = resp.json()
        return data.get("mode", "yahoo")
    except Exception:
        return "yahoo"   # Safe fallback if backend is not up yet


def _demo_market_data() -> list[dict[str, Any]]:
    """
    Fallback demo data when yfinance is unavailable (e.g. no internet).
    Uses randomised but realistic-looking values.
    """
    import random
    snapshots = []
    for sym in WATCHLIST[:5]:
        price = random.uniform(100, 500)
        snapshots.append({
            "symbol": sym,
            "ohlcv": {
                "open":   round(price * 0.99, 2),
                "high":   round(price * 1.02, 2),
                "low":    round(price * 0.97, 2),
                "close":  round(price, 2),
                "volume": random.randint(1_000_000, 50_000_000),
            },
            "indicators": {
                "rsi_14":       round(random.uniform(30, 70), 2),
                "macd":         round(random.uniform(-2, 2), 4),
                "macd_signal":  round(random.uniform(-2, 2), 4),
                "bb_upper":     round(price * 1.05, 2),
                "bb_lower":     round(price * 0.95, 2),
                "atr_14":       round(price * 0.02, 2),
                "volume_sma20": random.randint(5_000_000, 20_000_000),
                "sma_20":       round(price * 0.98, 2),
                "sma_50":       round(price * 0.95, 2),
            },
            "market_context": {
                "vix":         round(random.uniform(12, 30), 2),
                "spy_trend":   random.choice(["uptrend", "downtrend", "sideways"]),
                "sector_flow": random.choice(["risk-on", "risk-off", "neutral"]),
            },
            "_source": "demo",
        })
    return snapshots


def main() -> None:
    log.info("marketflow.brain.starting", watchlist=WATCHLIST)

    from agents.orchestrator import Orchestrator
    from data_feed.yahoo_feed import YahooFinanceFeed
    from execution.simulated_executor import SimulatedExecutor

    orchestrator    = Orchestrator()
    yahoo_feed      = YahooFinanceFeed(WATCHLIST)
    sim_executor    = SimulatedExecutor(
        initial_cash=float(os.getenv("SIM_INITIAL_CASH", "100000")),
        slippage_bps=float(os.getenv("SIM_SLIPPAGE_BPS", "5")),
    )

    log.info("orchestrator.ready")

    # ── Graceful shutdown ──────────────────────────────────────────────────────
    running = True
    def _stop(signum, frame):
        nonlocal running
        log.info("brain.shutdown_signal_received")
        running = False

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    # ── Main bar loop ──────────────────────────────────────────────────────────
    bar_count = 0
    while running:
        bar_count += 1
        current_mode = _get_current_mode()
        log.info("brain.bar_start", bar=bar_count, mode=current_mode)

        # ── Fetch market data ──────────────────────────────────────────────────
        if current_mode == "yahoo":
            try:
                snapshots = yahoo_feed.get_snapshots()
                log.info("brain.yahoo_data_loaded", count=len(snapshots))
            except Exception as exc:
                log.warning("brain.yahoo_feed_error", error=str(exc))
                log.info("brain.using_demo_fallback")
                snapshots = _demo_market_data()
        else:
            # IBKR mode: use demo data until you wire a real IBKR market data stream
            # Replace _demo_market_data() here with your IBKR data reader
            log.info("brain.ibkr_mode_data", note="Replace with IBKR real-time feed")
            snapshots = _demo_market_data()

        if not snapshots:
            log.warning("brain.no_snapshots")
            time.sleep(BAR_INTERVAL_SECONDS)
            continue

        # ── Run pipeline for each symbol ───────────────────────────────────────
        for snapshot in snapshots:
            if not running:
                break

            sym = snapshot["symbol"]
            log.info("brain.processing", symbol=sym, source=snapshot.get("_source", "?"))

            try:
                result = orchestrator.run(snapshot)
                submitted = result.get("submitted", False)
                signal_obj = result.get("signal")

                log.info(
                    "brain.pipeline_complete",
                    symbol=sym,
                    submitted=submitted,
                    has_signal=signal_obj is not None,
                )

                # ── Simulate execution in Yahoo mode ───────────────────────────
                # The Go backend already marked the order EXECUTED in its DB
                # (because its greenlight handler runs the sim path when mode=yahoo).
                # Here we also record the fill in the Python virtual portfolio.
                if submitted and current_mode == "yahoo" and signal_obj:
                    live_price = yahoo_feed.get_live_price(signal_obj.symbol)
                    fill = sim_executor.execute(
                        signal_id=signal_obj.signal_id,
                        symbol=signal_obj.symbol,
                        direction=result["debate_result"].consensus_direction if result.get("debate_result") else signal_obj.direction,
                        quantity=result["risk_result"].adjusted_quantity if result.get("risk_result") else signal_obj.quantity,
                        limit_price=signal_obj.limit_price,
                        market_price=live_price,
                    )
                    log.info(
                        "brain.sim_fill",
                        symbol=sym,
                        fill_price=fill.fill_price,
                        pnl=fill.pnl,
                        portfolio=sim_executor.get_portfolio_summary(),
                    )

            except Exception as exc:
                log.error("brain.pipeline_error", symbol=sym, error=str(exc))

        # Portfolio snapshot at end of each bar
        log.info("brain.portfolio_snapshot", **sim_executor.get_portfolio_summary())
        log.info("brain.bar_complete", next_in_seconds=BAR_INTERVAL_SECONDS)
        time.sleep(BAR_INTERVAL_SECONDS)

    log.info("marketflow.brain.stopped")


if __name__ == "__main__":
    main()
