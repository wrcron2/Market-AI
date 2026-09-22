"""
operating_mode.py — process-wide operating mode policy (Block 1: learning-only)
================================================================================
The mode is read from MARKET_AI_OPERATING_MODE at process start and is
immutable for the process lifetime. Only the exact value "paper" permits the
legacy paper-trading decision/execution behavior; missing, empty, or unknown
values fail closed to "learning". There is deliberately no live mode.

This policy module is pure stdlib. The main entrypoint still imports its
existing third-party logging/HTTP dependencies before selecting the learning
branch; the policy itself does not require credentials, market data, or models.
"""
from __future__ import annotations

import os

ENV_VAR = "MARKET_AI_OPERATING_MODE"

LEARNING = "learning"
PAPER = "paper"

# The only broker base URL from which order mutations are accepted. Anything
# else — live host, added suffix/path, userinfo, insecure scheme — fails closed.
PAPER_BROKER_BASE_URL = "https://paper-api.alpaca.markets"


class LearningModeError(RuntimeError):
    """Raised when a broker mutation is attempted outside explicit paper mode."""


def parse(raw: str | None) -> str:
    """Map a raw MARKET_AI_OPERATING_MODE value to a mode. Fail closed."""
    return PAPER if raw == PAPER else LEARNING


def current_mode(environ: dict | None = None) -> str:
    """Operating mode from `environ` (defaults to os.environ)."""
    env = os.environ if environ is None else environ
    return parse(env.get(ENV_VAR))


def decisions_enabled(mode: str) -> bool:
    """Whether decision-producing behavior (scanning, monitors) may run."""
    return mode == PAPER


def execution_enabled(mode: str) -> bool:
    """Whether broker mutations (orders, position close) may run."""
    return mode == PAPER


def mutation_block_reason(environ: dict | None = None) -> str | None:
    """
    Return None when a broker mutation (order submit/cancel, position close)
    is allowed, otherwise the reason it is blocked. Fails closed on any doubt:

      - operating mode must be exactly "paper";
      - PAPER_TRADING must be explicitly "true" (case-insensitive);
      - ALPACA_BASE_URL must be exactly PAPER_BROKER_BASE_URL (an unset variable
        falls back to the paper default, matching the executor; any set value
        that deviates — live host, suffix, userinfo, insecure scheme — fails
        closed).

    Read-only broker calls are never gated by this function.
    """
    env = os.environ if environ is None else environ
    if current_mode(env) != PAPER:
        return (
            f"learning_only: {ENV_VAR} is not exactly 'paper' — "
            "broker mutations are disabled in learning mode"
        )
    if env.get("PAPER_TRADING", "").strip().lower() != "true":
        return "PAPER_TRADING=true is required for broker mutations"
    if env.get("ALPACA_BASE_URL", PAPER_BROKER_BASE_URL) != PAPER_BROKER_BASE_URL:
        # Deliberately does not echo the URL: a rejected URL may carry userinfo.
        return f"ALPACA_BASE_URL is not exactly {PAPER_BROKER_BASE_URL} — fail closed"
    return None


def require_mutation_allowed(environ: dict | None = None) -> None:
    """
    Raise LearningModeError unless broker mutations are explicitly allowed.
    Mutation methods must call this BEFORE any network request — including
    preliminary GETs — so a blocked attempt produces zero outbound calls.
    """
    reason = mutation_block_reason(environ)
    if reason is not None:
        raise LearningModeError(reason)
