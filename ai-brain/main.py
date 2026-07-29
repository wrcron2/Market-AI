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

import json
import os
import signal
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Any

try:
    import pytz
    _ET = pytz.timezone("America/New_York")
except ImportError:
    _ET = None


def _market_window() -> str:
    """
    Returns the current market window:
      'pre_market'   — 8:30–9:20 ET  (scan for watchlist, no execution)
      'market'       — 9:25–16:05 ET (scan + AUTO_EXECUTE)
      'post_market'  — 16:15–16:45 ET (post-close scan for next-day prep)
      'closed'       — all other times (brain sleeps)
    """
    if _ET is None:
        return "market"  # fallback if pytz not installed
    now = datetime.now(_ET)
    if now.weekday() >= 5:  # weekend
        return "closed"
    h, m = now.hour, now.minute
    mins = h * 60 + m
    if 8 * 60 + 30 <= mins <= 9 * 60 + 20:
        return "pre_market"
    if 9 * 60 + 25 <= mins <= 16 * 60 + 5:
        return "market"
    if 16 * 60 + 15 <= mins <= 16 * 60 + 45:
        return "post_market"
    return "closed"

import httpx
import structlog
from dotenv import load_dotenv

from agents.telemetry import emit_activity

load_dotenv()

import logging
structlog.configure(
    wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
)

log = structlog.get_logger("marketflow.brain")

BAR_INTERVAL_SECONDS = 300    # 5-minute bar cycle
PIPELINE_WORKERS     = 5      # parallel threads for the AI agent pipeline

# ── Rotation guards (dual_momentum) ───────────────────────────────────────────
# A challenger must beat the incumbent's momentum score by this margin before we
# pay the spread to switch. Without it, two ETFs with near-identical scores would
# thrash the account back and forth on noise.
ROTATION_MIN_EDGE      = float(os.getenv("ROTATION_MIN_EDGE", "0.02"))   # 2%
# Minimum time a position is held before rotation may close it. Stop-loss,
# take-profit, and the SMA20 trend exit are NOT subject to this — they still fire
# immediately via the position monitor.
ROTATION_MIN_HOLD_DAYS = float(os.getenv("ROTATION_MIN_HOLD_DAYS", "3"))


def rotation_decision(
    candidate_symbol: str,
    open_positions: list[dict[str, Any]],
    scores: dict[str, float],
    now: float | None = None,
) -> tuple[str, dict[str, Any] | None, dict[str, Any]]:
    """
    Decide what dual_momentum should do about `candidate_symbol` given what is
    currently held. Pure — no IO, no side effects — so every branch is testable.

    Returns (decision, incumbent_or_None, detail) where decision is one of:
      multi_position       — >1 open position; invalid for a 1-position strategy
      incumbent_retained   — candidate is already held; hold it
      blocked_unscoreable  — incumbent has no momentum score this bar
      blocked_min_hold     — held for less than ROTATION_MIN_HOLD_DAYS
      blocked_edge         — challenger does not beat incumbent by ROTATION_MIN_EDGE
      rotate               — close incumbent, let the challenger through
      enter                — nothing held; normal entry
    """
    if not open_positions:
        return "enter", None, {}

    open_symbols = {p["symbol"] for p in open_positions}

    # Checked first and unconditionally: a corrupt multi-position state must
    # always surface, even when the candidate happens to be one of the held
    # symbols (which would otherwise mask it as a routine hold).
    if len(open_positions) != 1:
        return "multi_position", None, {"open_symbols": sorted(open_symbols)}

    if candidate_symbol in open_symbols:
        return "incumbent_retained", open_positions[0], {}

    incumbent = open_positions[0]
    inc_sym   = incumbent["symbol"]
    inc_score = scores.get(inc_sym)
    cand_score = scores.get(candidate_symbol, 0.0)
    held_days = ((now if now is not None else time.time())
                 - incumbent.get("entry_time", 0) / 1000) / 86_400
    detail = {"incumbent": inc_sym, "candidate": candidate_symbol,
              "incumbent_score": inc_score, "candidate_score": cand_score,
              "held_days": held_days}

    if inc_score is None:
        # Never rotate on an unfair comparison.
        return "blocked_unscoreable", incumbent, detail
    if held_days < ROTATION_MIN_HOLD_DAYS:
        detail["required_days"] = ROTATION_MIN_HOLD_DAYS
        return "blocked_min_hold", incumbent, detail
    required = inc_score * (1 + ROTATION_MIN_EDGE)
    detail["required_score"] = required
    if cand_score < required:
        return "blocked_edge", incumbent, detail
    return "rotate", incumbent, detail
BACKEND_MODE_URL     = f"http://{os.getenv('BRAIN_HOST', '127.0.0.1')}:{os.getenv('GO_SERVER_PORT', '8080')}/api/mode"

# Heartbeat file — written every loop iteration (including sleep ticks) so the
# host-side watchdog (scripts/oracle/watchdog.sh) can detect a stalled or dead
# brain by file age. Mounted to the host via ./logs:/app/logs in docker-compose.
HEARTBEAT_PATH = os.getenv("HEARTBEAT_PATH", "/app/logs/brain_heartbeat.json")


def _write_heartbeat(window: str, bar: int, mode: str) -> None:
    """Best-effort liveness marker — never let heartbeat IO kill the loop."""
    try:
        os.makedirs(os.path.dirname(HEARTBEAT_PATH), exist_ok=True)
        with open(HEARTBEAT_PATH, "w") as f:
            json.dump({"ts": int(time.time()), "window": window,
                       "bar": bar, "mode": mode}, f)
    except Exception as exc:
        log.warning("brain.heartbeat_write_failed", error=str(exc))


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
    from data_feed.alpaca_feed import AlpacaFeed
    from execution.alpaca_executor import AlpacaExecutor

    # ── Phase 1 gate: verify Alpaca paper account before any trading logic ─────
    alpaca = AlpacaExecutor()
    alpaca.verify_account()
    log.info("alpaca.ready")

    trading_mode = os.getenv("TRADING_MODE", "paper").lower()
    log.info("marketflow.brain.starting", symbol_count=len(get_symbols()), trading_mode=trading_mode)
    symbols = get_symbols()

    from agents.outcome_checker import OutcomeChecker
    from agents.position_monitor import PositionMonitorAgent
    from db.position_store import PositionStore
    from alerts.notifier import Notifier
    from reports.eod_report import maybe_generate_eod_report

    backend_host = os.getenv("BRAIN_HOST", "127.0.0.1")
    backend_port = os.getenv("GO_SERVER_PORT", "8080")
    backend_url  = f"http://{backend_host}:{backend_port}"

    position_store = PositionStore(backend_url)
    notifier       = Notifier(backend_url)
    notifier.medium("MarketFlow AI Started", f"Brain started on Oracle.\nTrading mode: {trading_mode}\nStrategy: dual_momentum\nUniverse: QQQ, GLD, TLT, EEM, XLE")
    orchestrator   = Orchestrator(alpaca=alpaca, position_store=position_store, notifier=notifier)

    # Feed selection: Alpaca (realtime) for live, Yahoo (delayed) for paper
    if trading_mode == "live":
        log.info("brain.feed", source="alpaca", feed=os.getenv("ALPACA_DATA_FEED", "iex"))
        active_feed = AlpacaFeed(symbols)
    else:
        log.info("brain.feed", source="yahoo", note="delayed — paper mode only")
        active_feed = YahooFinanceFeed(symbols)
    yahoo_feed = active_feed  # keep variable name for compatibility

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

    # ── Start outcome checker — checks signal accuracy at 5d and 20d horizons ──
    outcome_checker = OutcomeChecker(backend_url, alpaca)
    outcome_thread = threading.Thread(target=outcome_checker.run_forever, daemon=True, name="outcome-checker")
    outcome_thread.start()
    log.info("outcome_checker.thread_started")

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
        window        = _market_window()
        _write_heartbeat(window, bar_count, current_mode)

        # ── End-of-day report — generated once per day during the post-market
        # window, after the EOD position sweep has had a chance to run.
        if window == "post_market":
            maybe_generate_eod_report(backend_url, alpaca, orchestrator.router)

        # ── Sleep outside scan windows ─────────────────────────────────────────
        if window == "closed":
            log.info("brain.sleeping", reason="outside market windows", next_check_seconds=60)
            time.sleep(60)
            continue

        if time.time() - last_symbol_refresh > 86_400:
            symbols = get_symbols()
            yahoo_feed.symbols = symbols
            last_symbol_refresh = time.time()
            log.info("brain.symbols_refreshed", count=len(symbols))

        log.info("brain.bar_start", bar=bar_count, mode=current_mode,
                 window=window, symbols=len(yahoo_feed.symbols))

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

        # Tag pre/post market snapshots — orchestrator will stage but not execute
        if window in ("pre_market", "post_market"):
            for s in snapshots:
                s["_requires_revalidation"] = True
            log.info("brain.watchlist_mode", window=window,
                     note="signals staged as watchlist only — no execution until market open")

        # ── Pre-filter: drop flat/neutral snapshots before touching the LLM ──
        scanned_count = len(snapshots)
        interesting, dull = [], []
        for s in snapshots:
            (interesting if _is_interesting(s) else dull).append(s)
        for s in dull:
            emit_activity(backend_url, s["symbol"], "scan", "skip",
                          "prefilter: flat/neutral bar — no indicator in an extreme zone, not worth AI analysis")
        snapshots = interesting
        prefiltered_count = len(dull)
        log.info("brain.prefilter", before=scanned_count, after=len(snapshots), dropped=prefiltered_count)

        # ── Relative Strength Rotation — trade only the strongest ETF ──────────
        # dual_momentum design: among candidates meeting entry criteria, trade only
        # the one with the highest 12-month momentum (close / sma_50 ratio as proxy).
        # This prevents simultaneous positions in correlated ETFs.
        def _momentum_score(s: dict) -> float:
            close  = s.get("ohlcv", {}).get("close", 0)
            sma50  = s.get("indicators", {}).get("sma_50", close)
            high52 = s.get("indicators", {}).get("high_52w", close)
            if close <= 0:
                return 0.0
            # Score = proximity to 52wk high × trend strength
            return (close / high52) * (close / sma50) if sma50 > 0 and high52 > 0 else 0.0

        scores = {s["symbol"]: _momentum_score(s) for s in snapshots}
        if len(snapshots) > 1:
            best = max(snapshots, key=lambda s: scores[s["symbol"]])
            dropped_symbols = [s["symbol"] for s in snapshots if s["symbol"] != best["symbol"]]
            for sym in dropped_symbols:
                emit_activity(backend_url, sym, "scan", "skip",
                              f"rotation: {best['symbol']} has stronger momentum — dual-momentum trades only the strongest ETF")
            log.info("brain.relative_strength_rotation",
                     selected=best["symbol"],
                     dropped=dropped_symbols,
                     note="trading strongest ETF only")
            snapshots = [best]
        rotation_dropped_count = scanned_count - prefiltered_count - len(snapshots)

        # ── Rotation vs incumbent — the switch decision ────────────────────────
        # dual_momentum holds exactly ONE ETF: the strongest. Two cases arise here
        # and before 2026-07-29 neither was handled correctly.
        #
        #   incumbent still strongest → hold. Previously this fell through to the
        #     dedup gate and was logged as a duplicate drop, so 16 days of correct
        #     holding was indistinguishable from a dead pipeline.
        #   challenger stronger       → rotate: close the incumbent, let the
        #     challenger through. Previously the challenger was simply bought and
        #     the incumbent kept, accumulating correlated ETFs — the opposite of
        #     what the rotation comment above claims.
        #
        # Guarded by ROTATION_MIN_EDGE and ROTATION_MIN_HOLD_DAYS so near-tied
        # scores cannot churn the account.
        pending_symbols = position_store.get_pending_symbols()
        open_positions  = position_store.list_open_positions()
        open_symbols    = {p["symbol"] for p in open_positions}
        decision        = "no_candidate"
        # Mirrors open_symbols but is updated when a rotation actually closes a
        # position, so the per-bar decision record reports post-decision state
        # rather than showing a just-closed symbol as still held.
        held_now        = set(open_symbols)

        if snapshots and open_symbols:
            cand_sym = snapshots[0]["symbol"]
            verdict, incumbent, detail = rotation_decision(cand_sym, open_positions, scores)

            if verdict == "multi_position":
                decision = "rotation_skipped_multi_position"
                log.warning("brain.rotation_skipped_multi_position", candidate=cand_sym, **detail,
                            note="single-position strategy holds >1 position — DB may be desynced from broker")
                notifier.high(
                    "Multi-position state detected",
                    f"dual_momentum is a single-position strategy but the store reports "
                    f"{len(open_positions)} open: {detail['open_symbols']}. Rotation suspended "
                    f"until reconciled with the broker.",
                )
                snapshots = []

            elif verdict == "incumbent_retained":
                decision = "incumbent_retained"
                emit_activity(backend_url, cand_sym, "scan", "hold",
                              f"holding {cand_sym} — still the strongest ETF; dual-momentum stays in its winner")
                log.info("brain.incumbent_retained", symbol=cand_sym,
                         note="strongest ETF is already held — correct action is to hold")
                snapshots = []

            elif verdict.startswith("blocked_"):
                decision = f"rotation_{verdict}"
                log.info("brain.rotation_blocked", reason=verdict, **detail)
                snapshots = []

            else:  # verdict == "rotate"
                inc_sym   = incumbent["symbol"]
                held_days = detail["held_days"]
                decision  = "rotated"
                log.info("brain.rotation_exit", **detail)
                try:
                    # Exit price and P&L live on the BROKER record, not the
                    # backend DB row — fetch before closing or the trade is
                    # persisted with exit_price=0 and realized_pnl=0.
                    live = alpaca.get_position(inc_sym)

                    if live is None:
                        # Already flat at the broker — the position monitor's
                        # stop-loss/take-profit/SMA20 exit almost certainly won
                        # the race. Re-issuing the close would 404 and raise,
                        # stranding the DB row OPEN forever. Reconcile instead.
                        log.warning("brain.rotation_exit_already_flat", incumbent=inc_sym,
                                    note="broker reports no position; reconciling DB row only")
                        exit_price, realized = 0.0, 0.0
                    else:
                        exit_price = float(live["current_price"])
                        realized   = float(live["unrealized_pl"])
                        alpaca.close_position(inc_sym, signal_id=incumbent["id"])

                    # close_position() swallows its own exceptions and reports
                    # via return value — an unchecked False here would leave the
                    # broker flat while the DB still shows OPEN, and we would
                    # wrongly report success and release the buy leg.
                    if not position_store.close_position(
                        signal_id=incumbent["id"],
                        exit_price=exit_price,
                        realized_pnl=realized,
                        reason=f"rotation_exit ({cand_sym} stronger)",
                    ):
                        decision = "rotation_db_desync"
                        log.error("brain.rotation_db_desync", incumbent=inc_sym,
                                  candidate=cand_sym,
                                  note="broker closed but backend did not record it")
                        notifier.high(
                            "Rotation DB desync",
                            f"{inc_sym} was closed at the broker but the backend did not "
                            f"record it. DB and broker are out of sync — reconcile before "
                            f"trading resumes. Buy leg for {cand_sym} suppressed.",
                        )
                        snapshots = []
                    else:
                        held_now.discard(inc_sym)
                        emit_activity(backend_url, inc_sym, "execute", "sell",
                                      f"rotation: closed {inc_sym} — {cand_sym} now has stronger momentum")
                        notifier.medium(
                            "Rotation exit",
                            f"Closed {inc_sym} — {cand_sym} took the momentum lead "
                            f"(held {held_days:.1f}d).",
                        )
                except Exception as exc:
                    # Exit failed — do NOT let the buy leg through, or the
                    # account would end up holding both ETFs.
                    decision = "rotation_exit_failed"
                    log.error("brain.rotation_exit_failed", incumbent=inc_sym,
                              candidate=cand_sym, error=str(exc))
                    notifier.high(
                        "Rotation exit FAILED",
                        f"Could not close {inc_sym} while rotating into {cand_sym}: {exc}. "
                        f"Buy leg suppressed to avoid holding both.",
                    )
                    snapshots = []
        elif snapshots:
            decision = "entering"

        # ── Deduplication: skip symbols with a pending signal awaiting approval ─
        # Open positions are handled by the rotation block above; this gate now
        # only prevents stacking a second signal on one already awaiting a click.
        dedup_dropped_count = 0
        if pending_symbols and snapshots:
            before_dedup = len(snapshots)
            for s in snapshots:
                if s["symbol"] in pending_symbols:
                    emit_activity(backend_url, s["symbol"], "scan", "skip",
                                  f"dedup: {s['symbol']} already has a signal awaiting Green Light approval")
            snapshots = [s for s in snapshots if s["symbol"] not in pending_symbols]
            dedup_dropped_count = before_dedup - len(snapshots)
            if dedup_dropped_count:
                decision = "dedup_pending"
                log.info("brain.dedup_filter", blocked=sorted(pending_symbols),
                         dropped=dedup_dropped_count)

        # ── Decision throughput — one record per bar, always ───────────────────
        # A trading system's health is decision throughput, not uptime. Both the
        # 2026-07-25 and 2026-07-29 incidents presented as "everything green, zero
        # trades". This line makes a non-deciding pipeline greppable and alertable.
        log.info("brain.bar_decision", decision=decision, bar=bar_count,
                 candidates=len(snapshots), held=sorted(held_now) or None)

        # ── Per-bar heartbeat: always visible on the dashboard, even when every
        # symbol was filtered out — proves the brain is alive and explains why
        # a quiet bar produced no signals.
        if snapshots:
            heartbeat = (f"bar #{bar_count}: scanned {scanned_count} · "
                         f"{len(snapshots)} into AI pipeline ({', '.join(s['symbol'] for s in snapshots)})")
        else:
            heartbeat = (f"bar #{bar_count}: scanned {scanned_count}, all filtered out — "
                         f"{prefiltered_count} flat, {rotation_dropped_count} weaker rotation, "
                         f"{dedup_dropped_count} already held/pending. No new signals this bar.")
        emit_activity(backend_url, "ALL", "scan", "ok" if snapshots else "skip", heartbeat)

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
