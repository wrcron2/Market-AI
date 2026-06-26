"""
outcome_checker.py — Signal Postmortem Agent
=============================================
Checks approved signal outcomes at 5-day and 20-day horizons.
Fetches current price via yfinance, computes return, classifies as
TRUE_POSITIVE / FALSE_POSITIVE / REGIME_MISMATCH.

Runs as a background thread in the brain, checking every hour.
"""
from __future__ import annotations

import time
import threading
from typing import Any

import httpx
import structlog

log = structlog.get_logger(__name__)

CHECK_INTERVAL_SECONDS = 3600  # check once per hour


class OutcomeChecker:
    def __init__(self, backend_url: str, alpaca: Any) -> None:
        self._client = httpx.Client(base_url=backend_url, timeout=15)
        self._alpaca = alpaca

    def run_forever(self) -> None:
        log.info("outcome_checker.started", interval=CHECK_INTERVAL_SECONDS)
        calibrator = ThresholdCalibrator(self._client.base_url)
        while True:
            try:
                self._check_pending()
                calibrator.calibrate()  # dormant until MIN_SAMPLES_PER_BUCKET reached
            except Exception as exc:
                log.error("outcome_checker.error", error=str(exc))
            time.sleep(CHECK_INTERVAL_SECONDS)

    def _check_pending(self) -> None:
        resp = self._client.get("/api/signal-outcomes/pending-checks")
        if resp.status_code != 200:
            return
        outcomes = resp.json().get("outcomes") or []
        if not outcomes:
            return

        log.info("outcome_checker.cycle_start", count=len(outcomes))
        now_ms = int(time.time() * 1000)

        for o in outcomes:
            symbol      = o["symbol"]
            signal_id   = o["signal_id"]
            direction   = o["predicted_direction"]  # BUY | SELL | SHORT | COVER
            entry_price = o.get("entry_price", 0.0)
            check_5d    = o["check_5d_at"]
            check_20d   = o["check_20d_at"]
            outcome_5d  = o.get("outcome_5d")
            outcome_20d = o.get("outcome_20d")

            try:
                current_price = self._fetch_price(symbol)
                if current_price is None or current_price <= 0:
                    continue

                # Sync entry price from positions if missing
                if entry_price <= 0:
                    entry_price = self._get_entry_price(signal_id)
                if entry_price <= 0:
                    continue

                if outcome_5d is None and now_ms >= check_5d:
                    ret = (current_price - entry_price) / entry_price * 100
                    outcome = self._classify(direction, ret)
                    self._post_update(signal_id, "5d", current_price, ret, outcome)
                    log.info("outcome_checker.5d_recorded",
                             symbol=symbol, return_pct=round(ret, 2), outcome=outcome)

                if outcome_20d is None and now_ms >= check_20d:
                    ret = (current_price - entry_price) / entry_price * 100
                    outcome = self._classify(direction, ret)
                    self._post_update(signal_id, "20d", current_price, ret, outcome)
                    log.info("outcome_checker.20d_recorded",
                             symbol=symbol, return_pct=round(ret, 2), outcome=outcome)

            except Exception as exc:
                log.warning("outcome_checker.symbol_error", symbol=symbol, error=str(exc))

    def _fetch_price(self, symbol: str) -> float | None:
        """Fetch latest trade price from Alpaca market data API."""
        return self._alpaca.get_latest_price(symbol)

    def _get_entry_price(self, signal_id: str) -> float:
        try:
            resp = self._client.get("/api/positions")
            if resp.status_code != 200:
                return 0.0
            for p in resp.json().get("positions", []):
                if p.get("id") == signal_id:
                    return float(p.get("entry_price", 0))
        except Exception:
            pass
        return 0.0

    def _classify(self, direction: str, return_pct: float) -> str:
        bullish = direction in ("BUY", "COVER")
        if bullish and return_pct > 0:
            return "TRUE_POSITIVE"
        if not bullish and return_pct < 0:
            return "TRUE_POSITIVE"
        # Small moves (< 0.5%) in either direction = regime noise, not skill failure
        if abs(return_pct) < 0.5:
            return "REGIME_MISMATCH"
        return "FALSE_POSITIVE"

    def _post_update(self, signal_id: str, horizon: str,
                     price: float, ret: float, outcome: str) -> None:
        self._client.post("/api/signal-outcomes/update", json={
            "signal_id":  signal_id,
            "horizon":    horizon,
            "price":      price,
            "return_pct": ret,
            "outcome":    outcome,
        })


# ── Threshold Calibrator ────────────────────────────────────────────────────────

MIN_SAMPLES_PER_BUCKET = 30   # activation gate — never calibrate on fewer samples
WIN_RATE_TARGET        = 0.55  # minimum win rate to keep trading a bucket
FALLBACK_CONFIDENCE    = 0.70  # default when no calibration data exists


class ThresholdCalibrator:
    """
    Reads signal_outcomes, computes win rate per (strategy, confidence_bucket, spy_trend),
    and pushes calibrated min_confidence thresholds to the backend threshold_store.

    Runs after every OutcomeChecker cycle. Dormant until MIN_SAMPLES_PER_BUCKET
    is reached for a given bucket — never calibrates on noise.
    """

    def __init__(self, backend_url: str) -> None:
        self._client = httpx.Client(base_url=backend_url, timeout=15)

    def calibrate(self) -> None:
        """Fetch all outcomes and push updated thresholds for any bucket with enough data."""
        try:
            resp = self._client.get("/api/signal-outcomes/all")
            if resp.status_code != 200:
                return
            outcomes = resp.json().get("outcomes", [])
            if not outcomes:
                return

            # Group by (strategy, confidence_bucket, spy_trend)
            buckets: dict[tuple, list[str]] = {}
            for o in outcomes:
                if not o.get("outcome_5d"):
                    continue  # skip unresolved outcomes
                strategy  = o.get("strategy_name", "unknown")
                confidence = float(o.get("confidence", 0.70))
                spy_trend  = o.get("spy_trend", "sideways")
                bucket     = self._confidence_bucket(confidence)
                key        = (strategy, bucket, spy_trend)
                buckets.setdefault(key, []).append(o["outcome_5d"])

            for (strategy, bucket, spy_trend), outcomes_list in buckets.items():
                n = len(outcomes_list)
                if n < MIN_SAMPLES_PER_BUCKET:
                    log.debug("threshold_calibrator.bucket_too_small",
                              strategy=strategy, bucket=bucket, spy_trend=spy_trend, n=n)
                    continue

                win_rate = sum(1 for o in outcomes_list if o == "TRUE_POSITIVE") / n
                # If win rate is above target → lower the threshold (more permissive)
                # If win rate is below target → raise the threshold (more selective)
                if win_rate >= WIN_RATE_TARGET:
                    new_threshold = max(FALLBACK_CONFIDENCE - 0.05, 0.60)
                else:
                    new_threshold = min(FALLBACK_CONFIDENCE + 0.05, 0.85)

                self._client.post("/api/thresholds", json={
                    "strategy_name":     strategy,
                    "confidence_bucket": bucket,
                    "spy_trend":         spy_trend,
                    "sample_count":      n,
                    "win_rate":          round(win_rate, 4),
                    "min_confidence":    round(new_threshold, 4),
                })
                log.info("threshold_calibrator.updated",
                         strategy=strategy, bucket=bucket, spy_trend=spy_trend,
                         n=n, win_rate=round(win_rate, 3), new_threshold=new_threshold)

        except Exception as exc:
            log.warning("threshold_calibrator.error", error=str(exc))

    @staticmethod
    def _confidence_bucket(confidence: float) -> str:
        """Map confidence to a 0.05-wide bucket string."""
        lower = round(int(confidence * 20) / 20, 2)
        upper = round(lower + 0.05, 2)
        return f"{lower:.2f}-{upper:.2f}"
