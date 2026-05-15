"""
debate_agent.py — Multi-Agent Debate Team
==========================================
Two agents (Bull and Bear) debate the candidate signal using Bedrock
(Claude Sonnet). A judge agent synthesises their arguments and
produces a final confidence score and combined reasoning.

Bull and Bear run in parallel (ThreadPoolExecutor) — judge runs after both.
All three calls use Complexity.HIGH (Bedrock) for rigorous reasoning.
"""
from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Any

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


BULL_SYSTEM = """You are a senior quantitative analyst on a trading desk arguing FOR a proposed trade.
Your argument must be strategy-aware and evidence-based. Structure your response as follows:

1. THESIS (1 sentence): Why this specific setup (momentum_breakout OR mean_reversion) is valid right now.
2. SUPPORTING EVIDENCE (cite exact indicator values):
   - Price/trend: where price sits vs SMA20/SMA50, Bollinger %B level
   - Momentum: RSI value AND direction (rising/falling), MACD histogram (expanding/contracting)
   - Volume: exact ratio vs SMA20 and what it implies about institutional participation
   - Macro: VIX level and regime, SPY trend alignment
3. RISK/REWARD: Estimate upside target (% from entry) and natural stop level (ATR-based). State R/R ratio.
4. BEAR REBUTTAL: Acknowledge the single strongest bear concern and explain why it does not invalidate the thesis.

Be precise. Use specific numbers. Max 200 words."""

BEAR_SYSTEM = """You are a senior quantitative risk analyst arguing AGAINST a proposed trade.
Your job is to find the genuine weaknesses — not to be reflexively negative. Structure your response:

1. THESIS CHALLENGE (1 sentence): What specific condition makes this setup questionable or premature.
2. COUNTER-EVIDENCE (cite exact indicator values):
   - What is the indicator data NOT showing that the strategy requires
   - Any conflicting signals (e.g. mean_reversion signal but MACD still strongly directional)
   - Volume quality: does it support the claimed conviction or contradict it
   - Macro headwinds: VIX regime, SPY trend misalignment, sector flow
3. FAILURE SCENARIO: Describe specifically how this trade fails (e.g. "momentum continues, stop hit at -4%").
4. QUANTIFIED DOWNSIDE: Estimate max loss if wrong, using ATR as stop reference.

If the setup is genuinely strong, say so and limit your objections to 1-2 real concerns rather than fabricating weakness.
Be precise. Use specific numbers. Max 200 words."""

JUDGE_SYSTEM = """You are an impartial quantitative trading judge. Your job is to adjust the initial_confidence score based on argument quality — not to produce a new score from scratch.

CRITICAL: The initial_confidence is a calibrated baseline from a quantitative signal model. Your role is to apply a bounded ADJUSTMENT, not replace it with your own absolute score.

Adjustment rules (apply to initial_confidence):
  Bull clearly stronger, bear had no valid counter:         +0.04 to +0.07
  Bull stronger, bear raised 1 minor concern:              +0.01 to +0.04
  Arguments balanced, setup still valid:                   -0.02 to +0.01
  Bear raised 1-2 specific valid concerns:                 -0.05 to -0.10
  Bear identified a significant setup flaw:                -0.10 to -0.16
  Bear exposed a fundamental thesis error:                 -0.16 to -0.22

Indicator correlation rule — RSI and MACD are both price-derived and correlated:
  RSI + MACD both aligned = ONE evidence block, not two independent confirmations.
  Volume alignment is the most independent confirmation — weight it highest.
  SPY trend + VIX regime = macro evidence block (partially independent of price indicators).

Direction rule:
  Keep the ORIGINAL direction unless the bear argument proved the directional thesis is fundamentally wrong.
  Balanced arguments = reduce confidence, keep direction.
  Do NOT flip direction because one side was slightly more persuasive.

Respond with ONLY a valid JSON object:
{
  "judge_reasoning": "string — 2-3 sentences citing the decisive specific indicator values from both arguments and explaining the adjustment applied",
  "adjusted_confidence": number (initial_confidence ± adjustment, capped 0.50–0.92),
  "consensus_direction": "BUY | SELL | SHORT | COVER"
}"""


class DebateAgent:
    """Runs a structured bull-bear debate on a candidate signal using Bedrock."""

    def __init__(self, router: LLMRouter) -> None:
        self.router = router

    def debate(self, signal: CandidateSignal, market_snapshot: dict[str, Any]) -> DebateResult:
        """
        Run the debate and return a DebateResult with adjusted confidence.
        Bull and bear run in parallel; judge runs after both complete.
        All calls use Bedrock (Complexity.HIGH) for rigorous reasoning.
        """
        indicators = market_snapshot.get("indicators", {})
        ctx        = market_snapshot.get("market_context", {})
        ohlcv      = market_snapshot.get("ohlcv", {})

        close          = ohlcv.get("close", 0)
        atr            = indicators.get("atr_14", 0)
        atr_pct        = round(atr / close * 100, 2) if close else 0
        volume         = ohlcv.get("volume", 0)
        volume_sma20   = indicators.get("volume_sma20", 1)
        volume_ratio   = round(volume / volume_sma20, 2) if volume_sma20 else 1.0
        bb_upper       = indicators.get("bb_upper", 0)
        bb_lower       = indicators.get("bb_lower", 0)
        bb_pct_b       = round((close - bb_lower) / (bb_upper - bb_lower), 2) if (bb_upper - bb_lower) else 0.5
        macd_val       = indicators.get("macd", 0)
        macd_signal    = indicators.get("macd_signal", 0)
        macd_hist      = round(macd_val - macd_signal, 4)
        rsi            = indicators.get("rsi_14", 50)
        sma20          = indicators.get("sma_20", close)
        price_vs_sma20 = round((close - sma20) / sma20 * 100, 2) if sma20 else 0

        strategy_context = {
            "momentum_breakout": "Trend-following — requires accelerating MACD histogram and volume > 1.5x. Bear should challenge momentum strength and volume quality.",
            "mean_reversion":    "Contrarian fade — requires RSI/BB extreme with volume contracting. Bear should challenge whether the move is truly overextended or just beginning.",
        }.get(signal.strategy_name, "")

        market_data_block = (
            f"Market Data:\n"
            f"  Price:         ${close} ({price_vs_sma20:+.2f}% vs SMA20)\n"
            f"  SMA20/50:      {sma20} / {indicators.get('sma_50', 'N/A')}\n"
            f"  RSI(14):       {rsi} "
            f"({'strongly overbought' if rsi > 75 else 'overbought' if rsi > 65 else 'strongly oversold' if rsi < 25 else 'oversold' if rsi < 35 else 'neutral'})\n"
            f"  MACD:          {macd_val} | Signal: {macd_signal} | Histogram: {macd_hist} "
            f"({'expanding bullish' if macd_hist > 0.05 else 'expanding bearish' if macd_hist < -0.05 else 'contracting/flat'})\n"
            f"  Bollinger %B:  {bb_pct_b} (0=lower band, 1=upper band)\n"
            f"  ATR(14):       ${atr} ({atr_pct}% of price)\n"
            f"  Volume ratio:  {volume_ratio}x average ({'high conviction' if volume_ratio > 1.5 else 'low conviction — caution' if volume_ratio < 0.8 else 'normal'})\n"
            f"Macro Context:\n"
            f"  VIX:           {ctx.get('vix', 'N/A')} "
            f"({'crisis/capitulation >30' if ctx.get('vix', 18) > 30 else 'risk-off >25' if ctx.get('vix', 18) > 25 else 'elevated >20' if ctx.get('vix', 18) > 20 else 'normal'})\n"
            f"  SPY Trend:     {ctx.get('spy_trend', 'N/A')}\n"
            f"  Sector Flow:   {ctx.get('sector_flow', 'N/A')}\n"
        )

        signal_summary = (
            f"Signal: {signal.direction} {signal.quantity} shares of {signal.symbol} @ {'market' if signal.limit_price == 0 else f'${signal.limit_price:.2f}'}\n"
            f"Strategy: {signal.strategy_name} — {strategy_context}\n"
            f"Initial confidence: {signal.initial_confidence:.0%}\n"
            f"Signal reasoning: {signal.reasoning}\n\n"
            f"{market_data_block}"
        )

        log.info("debate_agent.start", symbol=signal.symbol, direction=signal.direction)

        # Bull and Bear run in parallel; judge runs after both complete
        bull_prompt = f"Argue FOR this trade using the market data provided:\n\n{signal_summary}"
        bear_prompt = f"Argue AGAINST this trade using the market data provided:\n\n{signal_summary}"

        with ThreadPoolExecutor(max_workers=2) as pool:
            bull_fut = pool.submit(
                self.router.complete,
                system=BULL_SYSTEM,
                user=bull_prompt,
                complexity=Complexity.HIGH,
                max_tokens=400,
            )
            bear_fut = pool.submit(
                self.router.complete,
                system=BEAR_SYSTEM,
                user=bear_prompt,
                complexity=Complexity.HIGH,
                max_tokens=400,
            )
            bull_arg = bull_fut.result()
            bear_arg = bear_fut.result()

        log.debug("debate_agent.bull_bear_done",
                  bull_len=len(bull_arg), bear_len=len(bear_arg))

        judge_prompt = (
            f"Original trade:\n{signal_summary}\n\n"
            f"Bull argument:\n{bull_arg}\n\n"
            f"Bear argument:\n{bear_arg}\n\n"
            "Synthesize both arguments and produce your final JSON assessment."
        )
        judge_raw = self.router.complete(
            system=JUDGE_SYSTEM,
            user=judge_prompt,
            complexity=Complexity.HIGH,
            max_tokens=350,
            schema=DebateResult,
        )

        try:
            match = re.search(r'\{.*\}', judge_raw, re.DOTALL)
            judge_data = json.loads(match.group(0) if match else judge_raw)
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
            log.error("debate_agent.judge_parse_error", error=str(exc), raw=judge_raw[:300])
            raise RuntimeError(f"Debate judge failed to produce valid JSON for {signal.symbol}") from exc
