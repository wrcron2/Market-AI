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


BULL_SYSTEM = """You are a bullish quantitative analyst reviewing a proposed trade.
Argue FOR the trade using the provided market data. Cite specific indicator values
(RSI level, MACD crossover, volume vs average, Bollinger Band position, ATR).
Reference macro context (VIX level, SPY trend). Be rigorous and specific.
Keep your argument under 150 words."""

BEAR_SYSTEM = """You are a bearish quantitative analyst reviewing a proposed trade.
Argue AGAINST the trade using the provided market data. Identify risks using specific
indicator values (RSI overbought/oversold, MACD divergence, low volume, ATR volatility).
Reference macro headwinds (VIX spike, SPY downtrend). Be rigorous and specific.
Keep your argument under 150 words."""

JUDGE_SYSTEM = """You are an impartial quantitative trading judge.
You have received bull and bear arguments about a proposed trade.
Weigh the specific indicator evidence cited in each argument.
In bear markets or high VIX environments, default to caution when arguments are equal.
Respond with ONLY a valid JSON object:
{
  "judge_reasoning": "string (2-3 sentences synthesizing both views with indicator evidence)",
  "adjusted_confidence": number (0.0-1.0, reflecting weight of evidence — be conservative),
  "consensus_direction": "BUY | SELL | SHORT | COVER (same or changed from original)"
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

        market_data_block = (
            f"Market Data:\n"
            f"  RSI(14):       {indicators.get('rsi_14', 'N/A')} "
            f"({'overbought >70' if indicators.get('rsi_14', 50) > 70 else 'oversold <30' if indicators.get('rsi_14', 50) < 30 else 'neutral'})\n"
            f"  MACD:          {indicators.get('macd', 'N/A')} | Signal: {indicators.get('macd_signal', 'N/A')} "
            f"({'bullish crossover' if indicators.get('macd', 0) > indicators.get('macd_signal', 0) else 'bearish crossover'})\n"
            f"  Bollinger %B:  {bb_pct_b} (0=lower band, 1=upper band)\n"
            f"  ATR(14):       ${atr} ({atr_pct}% of price)\n"
            f"  Volume ratio:  {volume_ratio}x average ({'high conviction' if volume_ratio > 1.5 else 'low conviction' if volume_ratio < 0.8 else 'normal'})\n"
            f"  SMA20/50:      {indicators.get('sma_20', 'N/A')} / {indicators.get('sma_50', 'N/A')}\n"
            f"Macro Context:\n"
            f"  VIX:           {ctx.get('vix', 'N/A')} "
            f"({'risk-off >25' if ctx.get('vix', 18) > 25 else 'fear >20' if ctx.get('vix', 18) > 20 else 'normal'})\n"
            f"  SPY Trend:     {ctx.get('spy_trend', 'N/A')}\n"
            f"  Sector Flow:   {ctx.get('sector_flow', 'N/A')}\n"
        )

        signal_summary = (
            f"Signal: {signal.direction} {signal.quantity} shares of {signal.symbol}\n"
            f"Strategy: {signal.strategy_name}\n"
            f"Limit price: {'market' if signal.limit_price == 0 else f'${signal.limit_price:.2f}'}\n"
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
                max_tokens=300,
            )
            bear_fut = pool.submit(
                self.router.complete,
                system=BEAR_SYSTEM,
                user=bear_prompt,
                complexity=Complexity.HIGH,
                max_tokens=300,
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
            max_tokens=256,
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
            log.error("debate_agent.judge_parse_error", error=str(exc))
            return DebateResult(
                bull_argument=bull_arg,
                bear_argument=bear_arg,
                judge_reasoning="Judge parse error — confidence penalised.",
                adjusted_confidence=max(0.0, signal.initial_confidence - 0.15),
                consensus_direction=signal.direction,
            )
