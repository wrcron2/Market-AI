"""
main.py — MarketFlow AI Brain Entry Point
==========================================
Starts the agent pipeline and feeds it real market data.

Trading modes (set via the React dashboard toggle):
  - Yahoo mode (default): Uses yfinance data. Simulated fills happen in the
    Go backend AFTER the trader clicks Green Light — not here in the brain.
  - IBKR mode: Uses the Go backend's IBKR client for real execution.

Data feed:
  - YahooFinanceFeed pulls real OHLCV data from Yahoo Finance (no API key needed)

Pre-filter:
  - Only snapshots with clear indicator signals reach the LLM.
  - Flat/neutral snapshots are dropped cheaply before any LLM call.
"""
from __future__ import annotations

import os
import signal
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
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

BAR_INTERVAL_SECONDS = 300    # 5-minute bar cycle
PIPELINE_WORKERS     = 5      # parallel threads for the AI agent pipeline
BACKEND_MODE_URL     = f"http://{os.getenv('BRAIN_HOST', '127.0.0.1')}:{os.getenv('GO_SERVER_PORT', '8080')}/api/mode"


def _get_current_mode() -> str:
    """Poll the Go backend for the current trading mode."""
    try:
        resp = httpx.get(BACKEND_MODE_URL, timeout=3)
        return resp.json().get("mode", "yahoo")
    except Exception:
        return "yahoo"


def _is_interesting(snapshot: dict[str, Any]) -> bool:
    """
    Cheap rule-based pre-filter. Returns False for flat/neutral snapshots
    that are very unlikely to generate a valid signal, saving LLM calls.
    A snapshot is interesting if at least one indicator is in an extreme zone.
    """
    ind = snapshot.get("indicators", {})
    ctx = snapshot.get("market_context", {})
    ohlcv = snapshot.get("ohlcv", {})

    rsi          = ind.get("rsi_14", 50)
    macd         = ind.get("macd", 0)
    macd_signal  = ind.get("macd_signal", 0)
    close        = ohlcv.get("close", 1)
    bb_upper     = ind.get("bb_upper", close * 1.05)
    bb_lower     = ind.get("bb_lower", close * 0.95)
    volume       = ohlcv.get("volume", 0)
    volume_sma20 = ind.get("volume_sma20", 1)
    vix          = ctx.get("vix", 18)

    bb_pct_b     = (close - bb_lower) / (bb_upper - bb_lower) if (bb_upper - bb_lower) else 0.5
    volume_ratio = volume / volume_sma20 if volume_sma20 else 1.0
    macd_crossed = (macd > macd_signal) != ((macd - 0.0001) > macd_signal)  # rough crossover check

    # In extreme fear, only pass SHORT/COVER candidates (all fail BUY pre-filter anyway)
    if vix > 40:
        return rsi > 70  # only overbought signals pass (for SHORT)

    return any([
        rsi < 35,                    # oversold
        rsi > 65,                    # overbought
        bb_pct_b < 0.15,             # near lower Bollinger Band
        bb_pct_b > 0.85,             # near upper Bollinger Band
        volume_ratio > 1.5,          # volume spike — conviction behind a move
        abs(macd - macd_signal) > abs(macd_signal) * 0.2,  # meaningful MACD divergence
    ])


def _demo_market_data() -> list[dict[str, Any]]:
    """
    Fallback demo data when yfinance is unavailable.
    Uses biased values that reliably trigger signal generation for testing.
    """
    import random
    snapshots = []
    scenarios = [
        # (rsi, macd_mult, bb_pct_b, vol_mult)  — each triggers a clear signal
        (28, -1, 0.08, 2.1),   # oversold + volume spike → BUY
        (74, +1, 0.93, 1.8),   # overbought + volume → SELL
        (32, -1, 0.12, 1.6),   # oversold → BUY
        (71, +1, 0.88, 1.4),   # overbought → SHORT
        (50, 0,  0.50, 0.9),   # neutral → filtered out by pre-filter
    ]
    symbols = ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL"]
    for sym, (rsi, macd_sign, bb_pct, vol_ratio) in zip(symbols, scenarios):
        price = random.uniform(100, 500)
        bb_range = price * 0.06
        bb_mid   = price
        volume_avg = random.randint(5_000_000, 20_000_000)
        snapshots.append({
            "symbol": sym,
            "ohlcv": {
                "open":   round(price * 0.99, 2),
                "high":   round(price * 1.02, 2),
                "low":    round(price * 0.97, 2),
                "close":  round(price, 2),
                "volume": int(volume_avg * vol_ratio),
            },
            "indicators": {
                "rsi_14":       rsi,
                "macd":         round(macd_sign * 0.5, 4),
                "macd_signal":  round(macd_sign * 0.3, 4),
                "bb_upper":     round(bb_mid + bb_range, 2),
                "bb_lower":     round(bb_mid - bb_range, 2),
                "atr_14":       round(price * 0.015, 2),
                "volume_sma20": volume_avg,
                "sma_20":       round(price * 0.98, 2),
                "sma_50":       round(price * 0.95, 2),
            },
            "market_context": {
                "vix":         18.0,
                "spy_trend":   "uptrend",
                "sector_flow": "risk-on",
            },
            "_source": "demo",
        })
    return snapshots


def _process(
    snapshot: dict[str, Any],
    orchestrator: Any,
    running_ref: list[bool],
) -> None:
    """Process one market snapshot through the full agent pipeline."""
    if not running_ref[0]:
        return
    sym = snapshot["symbol"]
    log.info("brain.processing", symbol=sym, source=snapshot.get("_source", "?"))
    try:
        result    = orchestrator.run(snapshot)
        submitted = result.get("submitted", False)
        signal_obj = result.get("signal")
        log.info(
            "brain.pipeline_complete",
            symbol=sym,
            submitted=submitted,
            has_signal=signal_obj is not None,
        )
        # NOTE: simulated fills are handled by the Go backend AFTER the trader
        # clicks Green Light. The brain does not execute fills directly.
    except Exception as exc:
        log.error("brain.pipeline_error", symbol=sym, error=str(exc))


def main() -> None:
    from agents.orchestrator import Orchestrator
    from data_feed.symbol_universe import get_symbols
    from data_feed.yahoo_feed import YahooFinanceFeed
    from execution.alpaca_executor import AlpacaExecutor

    # ── Phase 1 gate: verify Alpaca paper account before any trading logic ─────
    alpaca = AlpacaExecutor()
    alpaca.verify_account()
    log.info("alpaca.ready")

    symbols = get_symbols()
    log.info("marketflow.brain.starting", symbol_count=len(symbols))

    from agents.position_monitor import PositionMonitorAgent
    from db.position_store import PositionStore

    backend_host = os.getenv("BRAIN_HOST", "127.0.0.1")
    backend_port = os.getenv("GO_SERVER_PORT", "8080")
    backend_url  = f"http://{backend_host}:{backend_port}"

    position_store = PositionStore(backend_url)
    orchestrator   = Orchestrator(alpaca=alpaca, position_store=position_store)
    yahoo_feed     = YahooFinanceFeed(symbols)

    # ── Start position monitor in a background daemon thread ──────────────────
    monitor = PositionMonitorAgent(
        router=orchestrator.router,
        alpaca=alpaca,
        position_store=position_store,
        ws_broadcast_url=backend_url,
    )
    monitor_thread = threading.Thread(target=monitor.run_forever, daemon=True, name="position-monitor")
    monitor_thread.start()
    log.info("position_monitor.thread_started")

    log.info("orchestrator.ready")

    running_ref = [True]  # mutable ref so _process closure can read shutdown state

    def _stop(signum, frame):
        log.info("brain.shutdown_signal_received")
        running_ref[0] = False

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    bar_count           = 0
    last_symbol_refresh = 0

    while running_ref[0]:
        bar_count    += 1
        current_mode  = _get_current_mode()

        if time.time() - last_symbol_refresh > 86_400:
            symbols = get_symbols()
            yahoo_feed.symbols = symbols
            last_symbol_refresh = time.time()
            log.info("brain.symbols_refreshed", count=len(symbols))

        log.info("brain.bar_start", bar=bar_count, mode=current_mode, symbols=len(yahoo_feed.symbols))

        # ── Fetch market data ──────────────────────────────────────────────────
        if current_mode == "yahoo":
            try:
                snapshots = yahoo_feed.get_snapshots()
                log.info("brain.yahoo_data_loaded", count=len(snapshots))
            except Exception as exc:
                log.warning("brain.yahoo_feed_error", error=str(exc))
                snapshots = _demo_market_data()
        else:
            # IBKR mode: replace with real IBKR market data stream when ready
            log.info("brain.ibkr_mode_data", note="Replace with IBKR real-time feed")
            snapshots = _demo_market_data()

        if not snapshots:
            log.warning("brain.no_snapshots")
            time.sleep(BAR_INTERVAL_SECONDS)
            continue

        # ── Pre-filter: drop flat/neutral snapshots before touching the LLM ──
        before = len(snapshots)
        snapshots = [s for s in snapshots if _is_interesting(s)]
        log.info("brain.prefilter", before=before, after=len(snapshots), dropped=before - len(snapshots))

        # ── Deduplication: skip symbols with a pending signal or open position ──
        pending_symbols = position_store.get_pending_symbols()
        open_symbols    = position_store.get_open_symbols()
        blocked_symbols = pending_symbols | open_symbols
        if blocked_symbols:
            before_dedup = len(snapshots)
            snapshots = [s for s in snapshots if s["symbol"] not in blocked_symbols]
            log.info(
                "brain.dedup_filter",
                blocked=sorted(blocked_symbols),
                dropped=before_dedup - len(snapshots),
            )

        # ── Run pipeline in parallel across interesting symbols ────────────────
        with ThreadPoolExecutor(max_workers=PIPELINE_WORKERS) as pool:
            futures = {
                pool.submit(_process, s, orchestrator, running_ref): s["symbol"]
                for s in snapshots
            }
            for future in as_completed(futures):
                if not running_ref[0]:
                    break
                exc = future.exception()
                if exc:
                    log.error("brain.worker_error", symbol=futures[future], error=str(exc))

        log.info("brain.bar_complete", next_in_seconds=BAR_INTERVAL_SECONDS)
        time.sleep(BAR_INTERVAL_SECONDS)

    log.info("marketflow.brain.stopped")


if __name__ == "__main__":
    main()
