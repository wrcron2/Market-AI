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


RISK_SYSTEM = """You are a quantitative risk manager for an AI trading system.
Evaluate the proposed trade for risk using the market data provided.

Hard blocking rules (set is_blocked=true immediately):
- VIX > 40 and direction is BUY → block, market in extreme fear
- ATR% > 8% and confidence < 0.93 → block, too volatile for conviction level
- volume_ratio < 0.3 → block, illiquid (volume below 30% of average)
- risk_score >= 0.80 → block regardless of confidence

Position sizing rules (quantity_multiplier 0.10–1.0):
- VIX < 15 → 1.0 | VIX 15-20 → 0.90 | VIX 20-25 → 0.75
- VIX 25-30 → 0.50 | VIX 30-40 → 0.25 | VIX > 40 → 0.0 for BUY
- ATR% < 2% → 1.0 | ATR% 2-3% → 0.90 | ATR% 3-5% → 0.75
- ATR% 5-8% → 0.50 | ATR% > 8% → 0.25
- Apply both multipliers: final = vix_mult × atr_mult

Confidence adjustment rules (confidence_adjustment: -0.20 to +0.05):
- VIX > 30 → -0.10 | VIX > 25 → -0.05
- ATR% > 5% → -0.05 | ATR% > 8% → -0.10
- volume_ratio < 0.5 → -0.05
- BUY in downtrend OR SHORT in uptrend → -0.05
- Market order (limit_price=0) with ATR% > 4% → -0.03
- All indicators aligned + high volume + macro tailwind → +0.03 to +0.05

Risk score guide:
- 0.0-0.25: low risk | 0.25-0.50: moderate | 0.50-0.70: elevated
- 0.70-0.80: high risk | 0.80-1.0: extreme (block)

Write block_reason in plain language a non-technical trader can understand.
Never output risk_score < 0.05 — there is always some risk.

Respond with ONLY a valid JSON object:
{
  "is_blocked": boolean,
  "block_reason": "string (empty if not blocked)",
  "risk_score": number (0.0-1.0),
  "risk_notes": "string (2-3 sentences summarizing key risks)",
  "confidence_adjustment": number (-0.20 to 0.05),
  "quantity_multiplier": number (0.10 to 1.0)
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

        user_prompt = (
            f"Trade proposal:\n"
            f"  Symbol:              {signal.symbol}\n"
            f"  Direction:           {debate.consensus_direction}\n"
            f"  Quantity:            {signal.quantity} shares\n"
            f"  Price:               {'market order' if signal.limit_price == 0 else f'${signal.limit_price:.2f} limit'}\n"
            f"  Strategy:            {signal.strategy_name}\n"
            f"  Confidence (debate): {debate.adjusted_confidence:.0%}\n\n"
            f"Market Risk Context:\n"
            f"  VIX:                 {vix} "
            f"({'extreme fear' if vix > 40 else 'high fear' if vix > 30 else 'risk-off' if vix > 25 else 'elevated' if vix > 20 else 'normal'})\n"
            f"  SPY Trend:           {spy_trend}\n"
            f"  ATR(14):             ${atr} ({atr_pct}% of price)\n"
            f"  Volume ratio:        {volume_ratio}x average\n"
            f"  Close price:         ${close}\n\n"
            f"Bull/Bear synthesis:\n{debate.judge_reasoning}\n\n"
            "Apply the risk rules and output your JSON assessment."
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
                max_tokens=256,
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
