"""
risk_agent.py — Risk Assessment Agent
=======================================
Final gate before a signal is submitted to the Go backend.
Uses Ollama (local, low-complexity) to score risk and enforce hard limits.

Receives the full market snapshot so it can factor in VIX, ATR, SPY trend,
and volume — the context that actually determines position sizing.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any

import structlog
from pydantic import BaseModel

from .router import Complexity, LLMRouter
from .signal_agent import CandidateSignal
from .debate_agent import DebateResult

log = structlog.get_logger(__name__)


class RiskAssessment(BaseModel):
    is_blocked: bool          # True = signal is dropped entirely
    block_reason: str = ""    # Populated if is_blocked is True
    final_confidence: float   # Adjusted confidence after risk checks
    risk_score: float         # 0.0 (low risk) – 1.0 (high risk)
    risk_notes: str           # Human-readable risk summary
    adjusted_quantity: float  # Quantity after position-sizing rules


RISK_SYSTEM = """You are a quantitative risk manager performing the FINAL hard-limits check before a trade is submitted.

CRITICAL: VIX, ATR%, SPY trend, and volume have ALREADY been factored into the signal confidence and quantity by upstream agents. Do NOT re-penalize for those conditions. Your job is to catch only what the upstream agents could NOT see: execution risk, absolute liquidity limits, and hard system caps.

== YOUR ROLE ==
You are NOT re-scoring market conditions. You are answering three questions:
1. Does this trade violate any absolute hard limit? (block it if yes)
2. Is there execution or structural risk the upstream agents missed? (adjust confidence slightly if yes)
3. Does the position size need a hard cap for system safety? (cap quantity if yes)

== HARD BLOCKING RULES (is_blocked=true, these override everything) ==
- volume_ratio < 0.30: cannot fill — order would move the market (true illiquidity)
- ATR% > 8.0%: structurally too volatile, gap risk exceeds position tolerance
- VIX > 40 AND direction is BUY: crisis regime, no new long exposure
- Debate confidence (post-debate) < 0.60: signal quality too low to proceed regardless of risk metrics

== CONFIDENCE ADJUSTMENT (-0.08 to +0.04 only) ==
Only adjust for factors NOT already seen by upstream agents:
- Market order (limit_price=0) AND ATR% > 5%: -0.03 (execution slippage risk on volatile stock)
- volume_ratio 0.30–0.60 (low but not blocked): -0.03 (partial fill risk)
- Counter-trend trade (BUY in downtrend OR SHORT in uptrend): -0.03 (directional risk not fully captured)
- All risk checks pass cleanly AND volume_ratio > 1.5: +0.02 to +0.04 (execution quality premium)
- Do NOT penalize for VIX level — already factored upstream
- Do NOT penalize for ATR% < 8% — already factored upstream
- Maximum total adjustment: -0.08 downward, +0.04 upward

== POSITION SIZING (quantity_multiplier 0.50–1.0) ==
Signal agent already applied ATR% and VIX multipliers to quantity. Do NOT re-apply them.
Only reduce quantity for hard system limits:
- volume_ratio < 0.60: cap at 0.70 (partial fill protection)
- ATR% > 6%: cap at 0.60 (gap risk on large positions)
- All other cases: 1.0 (pass through as-is)

== RISK SCORE (0.05–1.0) ==
Score the execution and structural risk only (not market direction risk):
- 0.05–0.25: clean setup, no execution concerns
- 0.25–0.50: minor concerns (slightly low volume, moderate ATR)
- 0.50–0.70: elevated (market order in volatile stock, low volume)
- 0.70–0.80: high (multiple execution concerns)
- 0.80–1.0: extreme → set is_blocked=true

Write block_reason and risk_notes in plain language a trader can act on.
Never output risk_score < 0.05.

Respond with ONLY a valid JSON object:
{
  "is_blocked": boolean,
  "block_reason": "string (empty if not blocked, plain English if blocked)",
  "risk_score": number (0.05-1.0),
  "risk_notes": "string (1-2 sentences on execution and structural risk only)",
  "confidence_adjustment": number (-0.08 to +0.04),
  "quantity_multiplier": number (0.50 to 1.0)
}"""


class RiskAgent:
    """Applies position sizing and hard risk limits before staging a signal."""

    def __init__(self, router: LLMRouter) -> None:
        self.router = router
        self.min_confidence = float(os.getenv("MIN_SIGNAL_CONFIDENCE", "0.90"))
        self.max_quantity   = float(os.getenv("RISK_MAX_QUANTITY", "10000"))

    def assess(
        self,
        signal: CandidateSignal,
        debate: DebateResult,
        market_snapshot: dict[str, Any],
    ) -> RiskAssessment:
        """
        Run risk assessment on a signal after debate adjudication.
        Market snapshot is required to evaluate VIX, ATR, and volume context.
        """
        log.info("risk_agent.assess", symbol=signal.symbol, confidence=debate.adjusted_confidence)

        indicators   = market_snapshot.get("indicators", {})
        ctx          = market_snapshot.get("market_context", {})
        ohlcv        = market_snapshot.get("ohlcv", {})

        close        = ohlcv.get("close", 0)
        atr          = indicators.get("atr_14", 0)
        atr_pct      = round(atr / close * 100, 2) if close else 0
        volume       = ohlcv.get("volume", 0)
        volume_sma20 = indicators.get("volume_sma20", 1)
        volume_ratio = round(volume / volume_sma20, 2) if volume_sma20 else 1.0
        vix          = ctx.get("vix", 18)
        spy_trend    = ctx.get("spy_trend", "sideways")

        order_type = 'market order' if signal.limit_price == 0 else f'${signal.limit_price:.2f} limit'
        user_prompt = (
            f"Trade proposal:\n"
            f"  Symbol:              {signal.symbol}\n"
            f"  Direction:           {debate.consensus_direction}\n"
            f"  Strategy:            {signal.strategy_name}\n"
            f"  Quantity:            {signal.quantity} shares @ {order_type}\n"
            f"  Confidence (post-debate): {debate.adjusted_confidence:.0%}\n\n"
            f"Execution Risk Inputs (focus your assessment here):\n"
            f"  Order type:          {order_type} — {'slippage risk if ATR% is high' if signal.limit_price == 0 else 'price risk controlled'}\n"
            f"  Volume ratio:        {volume_ratio}x average — {'adequate liquidity' if volume_ratio >= 0.6 else 'LOW LIQUIDITY — partial fill risk'}\n"
            f"  ATR(14):             {atr_pct}% of price — {'EXTREME volatility' if atr_pct > 8 else 'high volatility' if atr_pct > 5 else 'normal'}\n"
            f"  SPY Trend:           {spy_trend} — {'counter-trend trade' if (debate.consensus_direction == 'BUY' and spy_trend == 'downtrend') or (debate.consensus_direction in ('SELL','SHORT') and spy_trend == 'uptrend') else 'trend-aligned'}\n\n"
            f"Macro Context (already priced into confidence — do not re-penalize):\n"
            f"  VIX:                 {vix} ({'extreme' if vix > 40 else 'crisis' if vix > 30 else 'elevated' if vix > 20 else 'normal'})\n"
            f"  Close price:         ${close}\n\n"
            f"Judge reasoning (upstream summary):\n{debate.judge_reasoning}\n\n"
            f"Apply ONLY the hard-limits check and execution risk assessment. Output JSON."
        )

        try:
            class _RiskOutput(BaseModel):
                is_blocked: bool
                block_reason: str = ""
                risk_score: float
                risk_notes: str
                confidence_adjustment: float = 0.0
                quantity_multiplier: float = 1.0

            raw = self.router.complete(
                system=RISK_SYSTEM,
                user=user_prompt,
                complexity=Complexity.LOW,
                max_tokens=300,
                schema=_RiskOutput,
            )

            # Regex fallback in case model adds surrounding text
            match = re.search(r'\{.*\}', raw, re.DOTALL)
            data = json.loads(match.group(0) if match else raw)

            final_conf = max(
                0.0,
                min(1.0, debate.adjusted_confidence + float(data.get("confidence_adjustment", 0))),
            )
            qty = min(
                self.max_quantity,
                signal.quantity * float(data.get("quantity_multiplier", 1.0)),
            )

            is_blocked   = data.get("is_blocked", False)
            block_reason = data.get("block_reason", "")

            if not is_blocked and final_conf < self.min_confidence:
                is_blocked = True
                block_reason = (
                    f"Post-risk confidence {final_conf:.0%} below minimum threshold {self.min_confidence:.0%}"
                )

            result = RiskAssessment(
                is_blocked=is_blocked,
                block_reason=block_reason,
                final_confidence=final_conf,
                risk_score=float(data.get("risk_score", 0.5)),
                risk_notes=data.get("risk_notes", ""),
                adjusted_quantity=qty,
            )

            log.info(
                "risk_agent.complete",
                symbol=signal.symbol,
                is_blocked=result.is_blocked,
                final_confidence=result.final_confidence,
                risk_score=result.risk_score,
                vix=vix,
                atr_pct=atr_pct,
            )
            return result

        except (json.JSONDecodeError, ValueError) as exc:
            log.error("risk_agent.parse_error", error=str(exc))
            return RiskAssessment(
                is_blocked=True,
                block_reason=f"Risk assessment failed — signal blocked for safety.",
                final_confidence=0.0,
                risk_score=1.0,
                risk_notes="Risk agent parse error — blocked conservatively.",
                adjusted_quantity=0.0,
            )
