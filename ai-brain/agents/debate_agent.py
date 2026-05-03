"""
debate_agent.py — Multi-Agent Debate Team
==========================================
Two agents (Bull and Bear) debate the candidate signal using Bedrock
(Claude 3.5 Sonnet). A judge agent synthesises their arguments and
produces a final confidence score and combined reasoning.

This is a key accuracy lever: single-model signals are vulnerable to
hallucinations. The adversarial debate catches flawed reasoning before
the signal reaches the risk agent.

Memory usage: Bedrock calls are API-bound (cloud), so this does NOT
consume local RAM beyond the Python process.
"""
from __future__ import annotations

import json
import structlog
from pydantic import BaseModel

from .router import Complexity, LLMRouter
from .signal_agent import CandidateSignal

log = structlog.get_logger(__name__)


class DebateResult(BaseModel):
    bull_argument: str
    bear_argument: str
    judge_reasoning: str
    adjusted_confidence: float   # Final confidence after debate (0.0–1.0)
    consensus_direction: str     # May change from original if debate overturns it


BULL_SYSTEM = """You are a bullish quantitative analyst.
Your job is to argue FOR the proposed trade. Cite technical indicators,
momentum, volume, macro tailwinds, and historical analogues.
Be rigorous but advocate for the trade. Keep your argument under 150 words."""

BEAR_SYSTEM = """You are a bearish quantitative analyst.
Your job is to argue AGAINST the proposed trade. Identify risks, counter-trends,
potential false breakouts, macro headwinds, and execution risks.
Be rigorous and critical. Keep your argument under 150 words."""

JUDGE_SYSTEM = """You are an impartial quantitative trading judge.
You have received bull and bear arguments about a proposed trade.
Synthesize both arguments and produce a final assessment.
Respond with ONLY a valid JSON object:
{
  "judge_reasoning": "string (2-3 sentences synthesizing both views)",
  "adjusted_confidence": number (0.0-1.0, reflecting the weight of evidence),
  "consensus_direction": "BUY | SELL | SHORT | COVER (same or changed from original)"
}"""


class DebateAgent:
    """Runs a structured bull-bear debate on a candidate signal."""

    def __init__(self, router: LLMRouter) -> None:
        self.router = router

    def debate(self, signal: CandidateSignal) -> DebateResult:
        """
        Run the debate and return a DebateResult with adjusted confidence.

        Uses Bedrock (Claude 3.5 Sonnet) for all three calls to ensure
        high-quality reasoning. Each call is independent, simulating
        genuine adversarial perspectives.
        """
        signal_summary = (
            f"Signal: {signal.direction} {signal.quantity} shares of {signal.symbol}\n"
            f"Strategy: {signal.strategy_name}\n"
            f"Limit price: {'market' if signal.limit_price == 0 else f'${signal.limit_price:.2f}'}\n"
            f"Initial confidence: {signal.initial_confidence:.0%}\n"
            f"Original reasoning: {signal.reasoning}"
        )

        log.info("debate_agent.start", symbol=signal.symbol, direction=signal.direction)

        # ── Bull argument ──────────────────────────────────────────────────────
        bull_arg = self.router.complete(
            system=BULL_SYSTEM,
            user=f"Argue FOR this trade:\n\n{signal_summary}",
            complexity=Complexity.LOW,
        )
        log.debug("debate_agent.bull_done", length=len(bull_arg))

        # ── Bear argument ──────────────────────────────────────────────────────
        bear_arg = self.router.complete(
            system=BEAR_SYSTEM,
            user=f"Argue AGAINST this trade:\n\n{signal_summary}",
            complexity=Complexity.LOW,
        )
        log.debug("debate_agent.bear_done", length=len(bear_arg))

        # ── Judge synthesis ────────────────────────────────────────────────────
        judge_prompt = (
            f"Original trade:\n{signal_summary}\n\n"
            f"Bull argument:\n{bull_arg}\n\n"
            f"Bear argument:\n{bear_arg}\n\n"
            "Synthesize and score."
        )
        judge_raw = self.router.complete(
            system=JUDGE_SYSTEM,
            user=judge_prompt,
            complexity=Complexity.LOW,
            schema=DebateResult,
        )

        try:
            judge_data = json.loads(judge_raw)
            result = DebateResult(
                bull_argument=bull_arg,
                bear_argument=bear_arg,
                judge_reasoning=judge_data["judge_reasoning"],
                adjusted_confidence=float(judge_data["adjusted_confidence"]),
                consensus_direction=judge_data["consensus_direction"].upper(),
            )
            log.info(
                "debate_agent.complete",
                symbol=signal.symbol,
                original_confidence=signal.initial_confidence,
                adjusted_confidence=result.adjusted_confidence,
                direction_changed=(result.consensus_direction != signal.direction),
            )
            return result

        except (json.JSONDecodeError, KeyError, ValueError) as exc:
            log.error("debate_agent.judge_parse_error", error=str(exc))
            # Fallback: return a conservative result that keeps the signal
            # but drops confidence, ensuring the risk agent will scrutinise it.
            return DebateResult(
                bull_argument=bull_arg,
                bear_argument=bear_arg,
                judge_reasoning="Judge parse error — confidence penalised.",
                adjusted_confidence=max(0.0, signal.initial_confidence - 0.15),
                consensus_direction=signal.direction,
            )
