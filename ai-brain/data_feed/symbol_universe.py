"""
symbol_universe.py — Symbol Universe via Alpaca
================================================
Fetches active US equity symbols from the Alpaca Assets API.
Filters to the 500 most liquid (fractionable) stocks.
Caches daily so the pipeline doesn't re-fetch on every restart.

Requires: ALPACA_API_KEY and ALPACA_SECRET_KEY in .env
Endpoint:  GET /v2/assets?status=active&asset_class=us_equity
"""
from __future__ import annotations

import json
import os
import time
import urllib.request
from pathlib import Path

import structlog

log = structlog.get_logger(__name__)

_ALPACA_BASE_URL = os.getenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
_ASSETS_ENDPOINT = f"{_ALPACA_BASE_URL}/v2/assets"
_TARGET_SIZE     = 500

_CACHE_DIR  = Path(__file__).resolve().parents[2] / "infra"
_CACHE_FILE = _CACHE_DIR / "symbols_cache.json"
_CACHE_TTL  = 86_400  # 24 hours


def get_symbols() -> list[str]:
    """
    Return up to 500 active, tradeable US equity symbols.
    Reads from cache if fresh; otherwise fetches from Alpaca.
    """
    cached = _load_cache()
    if cached:
        log.info("symbol_universe.cache_hit", count=len(cached))
        return cached

    log.info("symbol_universe.fetching", source="alpaca")
    symbols = _fetch_alpaca()

    if symbols:
        _save_cache(symbols)
        log.info("symbol_universe.fetched", count=len(symbols))
    else:
        log.warning("symbol_universe.fetch_failed", fallback="hardcoded_core")
        symbols = _fallback_symbols()

    return symbols


def refresh() -> list[str]:
    """Force a fresh fetch, ignoring the cache."""
    _CACHE_FILE.unlink(missing_ok=True)
    return get_symbols()


# ── Private ────────────────────────────────────────────────────────────────────

def _fetch_alpaca() -> list[str]:
    """
    Fetch active US equities from Alpaca Assets API.
    Prefers fractionable stocks (most liquid large-caps) and caps at 500.
    """
    api_key    = os.getenv("ALPACA_API_KEY", "")
    secret_key = os.getenv("ALPACA_SECRET_KEY", "")

    if not api_key or not secret_key:
        log.error("symbol_universe.missing_alpaca_keys")
        return []

    url = f"{_ASSETS_ENDPOINT}?status=active&asset_class=us_equity"
    req = urllib.request.Request(
        url,
        headers={
            "APCA-API-KEY-ID":     api_key,
            "APCA-API-SECRET-KEY": secret_key,
            "accept":              "application/json",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            assets = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        log.error("symbol_universe.alpaca_error", error=str(exc))
        return []

    # Filter: must be tradable and have a clean symbol (no / for special classes)
    tradable = [
        a for a in assets
        if a.get("tradable") and "/" not in a.get("symbol", "")
    ]

    # Prefer fractionable (liquid large-caps) — fill remainder with other tradables
    fractionable = [a for a in tradable if a.get("fractionable")]
    others       = [a for a in tradable if not a.get("fractionable")]

    selected = fractionable[:_TARGET_SIZE]
    if len(selected) < _TARGET_SIZE:
        selected += others[: _TARGET_SIZE - len(selected)]

    symbols = sorted(a["symbol"] for a in selected)
    return symbols


def _load_cache() -> list[str] | None:
    if not _CACHE_FILE.exists():
        return None
    try:
        data = json.loads(_CACHE_FILE.read_text())
        age  = time.time() - data.get("timestamp", 0)
        if age > _CACHE_TTL:
            return None
        return data["symbols"]
    except Exception:
        return None


def _save_cache(symbols: list[str]) -> None:
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _CACHE_FILE.write_text(json.dumps({
            "timestamp": time.time(),
            "symbols":   symbols,
        }))
    except Exception as exc:
        log.warning("symbol_universe.cache_write_error", error=str(exc))


def _fallback_symbols() -> list[str]:
    """Minimal hardcoded fallback if Alpaca is unreachable."""
    return [
        "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "BRK-B",
        "JPM",  "V",    "UNH",  "XOM",  "LLY",   "JNJ",  "WMT",  "MA",
        "PG",   "AVGO", "HD",   "CVX",  "MRK",   "ABBV", "COST", "PEP",
        "KO",   "ADBE", "CRM",  "TMO",  "MCD",   "ACN",  "CSCO", "AMD",
        "NEE",  "ABT",  "TXN",  "LIN",  "DHR",   "AMGN", "INTC", "UPS",
        "SPY",  "QQQ",
    ]
