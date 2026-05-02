"""
risk_agent.py — Risk Assessment Agent
=======================================
Final gate before a signal is submitted to the Go backend.
Uses Ollama (local, low-complexity) to quickly score risk factors
and enforce hard limits (position size, max drawdown, etc.).

The risk agent can:
  - Reduce confidence further if risk factors are present
  - Block the signal entirely (return is_blocked=True)
  - Adjust quantity for position sizing

This runs AFTER the debate, so it is the last check before the
signal enters the Green Light queue.
"""
from __future__ import annotations

import json
import os
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


RISK_SYSTEM = """You are a quantitative risk manager for an HFT trading system.
Evaluate the proposed trade for risk factors including:
- Position size relative to typical daily volume
- Volatility risk
- Execution risk (market vs limit)
- Concentration risk
- Macro/event risk (earnings, FOMC, etc.)

Respond with ONLY a valid JSON object:
{
  "is_blocked": boolean,
  "block_reason": "string (empty if not blocked)",
  "risk_score": number (0.0=low risk, 1.0=extreme risk),
  "risk_notes": "string (2-3 sentences summarizing key risks)",
  "confidence_adjustment": number (-0.2 to 0.05, applied to debate confidence),
  "quantity_multiplier": number (0.1 to 1.0, applied to original quantity)
}"""


class RiskAgent:
    """
    Applies position sizing and hard risk limits before staging a signal.
    Uses Ollama for speed — this should be fast since it runs on every signal.
    """

    def __init__(self, router: LLMRouter) -> None:
        self.router = router
        self.min_confidence = float(os.getenv("MIN_SIGNAL_CONFIDENCE", "0.90"))
        self.max_quantity   = float(os.getenv("RISK_MAX_QUANTITY", "10000"))

    def assess(self, signal: CandidateSignal, debate: DebateResult) -> RiskAssessment:
        """
        Run risk assessment on a signal after debate adjudication.

        Returns a RiskAssessment. If is_blocked is True, the orchestrator
        drops the signal before submitting to the Go backend.
        """
        log.info("risk_agent.assess", symbol=signal.symbol, confidence=debate.adjusted_confidence)

        user_prompt = (
            f"Trade proposal:\n"
            f"  Symbol:    {signal.symbol}\n"
            f"  Direction: {debate.consensus_direction}\n"
            f"  Quantity:  {signal.quantity}\n"
            f"  Price:     {'market' if signal.limit_price == 0 else f'${signal.limit_price:.2f}'}\n"
            f"  Strategy:  {signal.strategy_name}\n"
            f"  Confidence after debate: {debate.adjusted_confidence:.0%}\n\n"
            f"Bull/Bear synthesis:\n{debate.judge_reasoning}\n\n"
            "Assess the risk."
        )

        try:
            raw = self.router.complete(
                system=RISK_SYSTEM,
                user=user_prompt,
                complexity=Complexity.LOW,   # Ollama — fast, local
            )
            data = json.loads(raw)

            final_conf = max(
                0.0,
                min(1.0, debate.adjusted_confidence + float(data.get("confidence_adjustment", 0))),
            )
            qty = min(
                self.max_quantity,
                signal.quantity * float(data.get("quantity_multiplier", 1.0)),
            )

            # Hard block: confidence below threshold after risk adjustment
            is_blocked = data.get("is_blocked", False)
            block_reason = data.get("block_reason", "")

            if not is_blocked and final_conf < self.min_confidence:
                is_blocked = True
                block_reason = (
                    f"Post-risk confidence {final_conf:.0%} < threshold {self.min_confidence:.0%}"
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
            )
            return result

        except (json.JSONDecodeError, ValueError) as exc:
            log.error("risk_agent.parse_error", error=str(exc))
            # Parse failure → block the signal conservatively
            return RiskAssessment(
                is_blocked=True,
                block_reason=f"Risk agent parse error: {exc}",
                final_confidence=0.0,
                risk_score=1.0,
                risk_notes="Risk assessment failed — signal blocked.",
                adjusted_quantity=0.0,
            )
