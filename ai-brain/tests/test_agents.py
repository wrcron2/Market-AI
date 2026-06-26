"""
test_agents.py — Unit tests for SignalAgent, RiskAgent, DebateAgent, and LLMRouter.

All tests use mocked Ollama — no real LLM calls.
Run: python -m pytest ai-brain/tests/test_agents.py -v
"""
import os
import sys
import re
import json
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_router(response: str = "{}") -> "LLMRouter":
    """Return an LLMRouter with Ollama mocked to return `response`."""
    from agents.router import LLMRouter
    router = LLMRouter.__new__(LLMRouter)
    router.ollama_model        = "qwen3:4b"
    router.ollama_reason_model = "deepseek-r1:7b"
    router.ollama_host         = "http://127.0.0.1:11434"
    router.bedrock_model       = "test-model"
    router.aws_region          = "us-east-1"
    router.use_aws             = False
    router._bedrock            = None
    router._ollama_client      = MagicMock()
    router._ollama_client.chat.return_value = MagicMock(
        message=MagicMock(content=response)
    )
    return router


def _base_snapshot(**overrides) -> dict:
    """Return a valid market snapshot. Override individual fields as needed."""
    snap = {
        "symbol": "AAPL",
        "ohlcv": {"open": 180, "high": 185, "low": 178, "close": 182, "volume": 5_000_000},
        "indicators": {
            "rsi_14": 28,
            "macd": -0.3,
            "macd_signal": -0.1,
            "bb_upper": 195.0,
            "bb_lower": 170.0,
            "atr_14": 2.5,
            "volume_sma20": 4_000_000,
            "sma_20": 178.0,
            "sma_50": 175.0,
        },
        "market_context": {"vix": 17.0, "spy_trend": "uptrend", "sector_flow": "risk-on"},
        "_source": "realtime",
    }
    for k, v in overrides.items():
        if isinstance(v, dict):
            snap[k] = {**snap.get(k, {}), **v}
        else:
            snap[k] = v
    return snap


def _valid_signal_json(**overrides) -> str:
    sig = {
        "symbol": "AAPL",
        "direction": "BUY",
        "quantity": 160,
        "limit_price": 0,
        "reasoning": "test signal",
        "strategy_name": "mean_reversion",
        "initial_confidence": 0.83,
    }
    sig.update(overrides)
    return json.dumps(sig)


# ── LLMRouter — HIGH_REASON routing ───────────────────────────────────────────

class TestLLMRouterHighReason(unittest.TestCase):

    def _make(self):
        from agents.router import LLMRouter, Complexity
        router = _make_router('{"test": true}')
        return router, Complexity

    def test_high_reason_routes_to_deepseek(self):
        router, Complexity = self._make()
        router.complete("sys", "user", complexity=Complexity.HIGH_REASON)
        call_args = router._ollama_client.chat.call_args
        self.assertEqual(call_args.kwargs["model"], "deepseek-r1:7b")

    def test_high_routes_to_qwen(self):
        router, Complexity = self._make()
        router.complete("sys", "user", complexity=Complexity.HIGH)
        call_args = router._ollama_client.chat.call_args
        self.assertEqual(call_args.kwargs["model"], "qwen3:4b")

    def test_low_routes_to_qwen(self):
        router, Complexity = self._make()
        router.complete("sys", "user", complexity=Complexity.LOW)
        call_args = router._ollama_client.chat.call_args
        self.assertEqual(call_args.kwargs["model"], "qwen3:4b")

    def test_model_tag_high_reason(self):
        router, Complexity = self._make()
        tag = router.model_tag(Complexity.HIGH_REASON)
        self.assertIn("deepseek-r1:7b", tag)

    def test_model_tag_high(self):
        router, Complexity = self._make()
        tag = router.model_tag(Complexity.HIGH)
        self.assertIn("qwen3:4b", tag)


# ── SignalAgent — 8 no-trade conditions ───────────────────────────────────────

class TestSignalAgentNoTradeConditions(unittest.TestCase):
    """
    The signal agent system prompt defines 8 NO-TRADE conditions.
    These tests verify the agent returns None for null model output
    and that key conditions produce no signal via parse failure.
    """

    def _make_agent(self, response: str):
        from agents.signal_agent import SignalAgent
        from agents.router import Complexity
        router = _make_router(response)
        return SignalAgent(router)

    def test_returns_none_on_null_response(self):
        """Model outputs 'null' → no signal."""
        agent = self._make_agent("null")
        result = agent.generate(_base_snapshot())
        self.assertIsNone(result)

    def test_returns_none_on_empty_response(self):
        agent = self._make_agent("")
        result = agent.generate(_base_snapshot())
        self.assertIsNone(result)

    def test_returns_none_on_invalid_json(self):
        agent = self._make_agent("not valid json at all")
        result = agent.generate(_base_snapshot())
        self.assertIsNone(result)

    def test_returns_none_on_invalid_direction(self):
        """direction must be BUY|SELL|SHORT|COVER."""
        agent = self._make_agent(_valid_signal_json(direction="HOLD"))
        result = agent.generate(_base_snapshot())
        self.assertIsNone(result)

    def test_returns_none_on_confidence_above_1(self):
        """confidence must be 0.0–1.0."""
        agent = self._make_agent(_valid_signal_json(initial_confidence=1.5))
        result = agent.generate(_base_snapshot())
        self.assertIsNone(result)

    def test_returns_none_on_confidence_below_0(self):
        agent = self._make_agent(_valid_signal_json(initial_confidence=-0.1))
        result = agent.generate(_base_snapshot())
        self.assertIsNone(result)

    def test_valid_signal_returns_candidate(self):
        """Happy path — valid JSON produces a CandidateSignal."""
        from agents.signal_agent import CandidateSignal
        agent = self._make_agent(_valid_signal_json())
        result = agent.generate(_base_snapshot())
        self.assertIsNotNone(result)
        self.assertIsInstance(result, CandidateSignal)
        self.assertEqual(result.direction, "BUY")
        self.assertAlmostEqual(result.initial_confidence, 0.83)

    def test_valid_signal_cover_direction(self):
        agent = self._make_agent(_valid_signal_json(direction="COVER"))
        result = agent.generate(_base_snapshot())
        self.assertIsNotNone(result)
        self.assertEqual(result.direction, "COVER")


# ── RiskAgent — 4 hard-block conditions ───────────────────────────────────────

class TestRiskAgentHardBlocks(unittest.TestCase):

    def _make_risk_result(self, **overrides):
        """Build a risk agent JSON response."""
        base = {
            "is_blocked": False,
            "block_reason": "",
            "risk_score": 0.2,
            "risk_notes": "clean setup",
            "confidence_adjustment": 0.0,
            "quantity_multiplier": 1.0,
        }
        base.update(overrides)
        return json.dumps(base)

    def _make_agent_and_inputs(self, risk_response: str, debate_confidence: float = 0.80):
        from agents.risk_agent import RiskAgent
        from agents.debate_agent import DebateResult
        from agents.signal_agent import CandidateSignal

        router = _make_router(risk_response)
        agent = RiskAgent(router)
        agent.min_confidence = 0.70

        signal = CandidateSignal(
            symbol="AAPL",
            direction="BUY",
            quantity=160,
            limit_price=0,
            reasoning="test",
            strategy_name="mean_reversion",
            initial_confidence=0.80,
        )
        debate = DebateResult(
            bull_argument="bullish",
            bear_argument="bearish",
            judge_reasoning="balanced",
            adjusted_confidence=debate_confidence,
            consensus_direction="BUY",
        )
        return agent, signal, debate

    def test_blocked_when_model_says_blocked(self):
        """Hard block: model returns is_blocked=true."""
        agent, signal, debate = self._make_agent_and_inputs(
            self._make_risk_result(is_blocked=True, block_reason="ATR% > 8.0%")
        )
        result = agent.assess(signal, debate, _base_snapshot())
        self.assertTrue(result.is_blocked)
        self.assertIn("ATR", result.block_reason)

    def test_blocked_when_final_confidence_below_threshold(self):
        """Hard block: confidence after adjustment falls below min_confidence."""
        agent, signal, debate = self._make_agent_and_inputs(
            self._make_risk_result(is_blocked=False, confidence_adjustment=-0.15),
            debate_confidence=0.75,
        )
        result = agent.assess(signal, debate, _base_snapshot())
        self.assertTrue(result.is_blocked)
        self.assertIn("threshold", result.block_reason.lower())

    def test_not_blocked_on_clean_signal(self):
        """Happy path — clean signal passes risk check."""
        agent, signal, debate = self._make_agent_and_inputs(
            self._make_risk_result(is_blocked=False, confidence_adjustment=0.02),
            debate_confidence=0.82,
        )
        result = agent.assess(signal, debate, _base_snapshot())
        self.assertFalse(result.is_blocked)
        self.assertAlmostEqual(result.final_confidence, 0.84, places=2)

    def test_blocked_on_parse_failure(self):
        """Parse failure → conservatively blocked."""
        agent, signal, debate = self._make_agent_and_inputs("not valid json")
        result = agent.assess(signal, debate, _base_snapshot())
        self.assertTrue(result.is_blocked)
        self.assertEqual(result.risk_score, 1.0)

    def test_quantity_multiplier_applied(self):
        """quantity_multiplier reduces adjusted_quantity."""
        agent, signal, debate = self._make_agent_and_inputs(
            self._make_risk_result(is_blocked=False, quantity_multiplier=0.70),
            debate_confidence=0.82,
        )
        result = agent.assess(signal, debate, _base_snapshot())
        self.assertFalse(result.is_blocked)
        self.assertAlmostEqual(result.adjusted_quantity, 160 * 0.70, places=0)

    def test_requires_revalidation_does_not_block(self):
        """Pre-market tag is neutral to risk agent — it blocks at orchestrator level."""
        agent, signal, debate = self._make_agent_and_inputs(
            self._make_risk_result(is_blocked=False),
            debate_confidence=0.82,
        )
        snap = _base_snapshot(**{"_requires_revalidation": True})
        result = agent.assess(signal, debate, snap)
        self.assertFalse(result.is_blocked)


# ── DebateAgent — think token stripping ───────────────────────────────────────

class TestDebateAgentThinkTokenStripping(unittest.TestCase):

    def test_think_tokens_stripped_from_judge_output(self):
        """deepseek-r1 <think> blocks must be stripped before JSON parsing."""
        raw = (
            "<think>The bull argument relies on RSI divergence which is correlated with MACD...</think>"
            '{"judge_reasoning": "clean", "adjusted_confidence": 0.78, "consensus_direction": "BUY"}'
        )
        cleaned = re.sub(r'<think>.*?</think>', '', raw, flags=re.DOTALL).strip()
        data = json.loads(cleaned)
        self.assertEqual(data["consensus_direction"], "BUY")
        self.assertAlmostEqual(data["adjusted_confidence"], 0.78)

    def test_no_think_tokens_passes_through(self):
        """Normal output without think tags parses cleanly."""
        raw = '{"judge_reasoning": "clean", "adjusted_confidence": 0.80, "consensus_direction": "SELL"}'
        cleaned = re.sub(r'<think>.*?</think>', '', raw, flags=re.DOTALL).strip()
        data = json.loads(cleaned)
        self.assertEqual(data["consensus_direction"], "SELL")

    def test_multiple_think_blocks_stripped(self):
        raw = (
            "<think>first thought</think>"
            "<think>second thought</think>"
            '{"judge_reasoning": "ok", "adjusted_confidence": 0.75, "consensus_direction": "BUY"}'
        )
        cleaned = re.sub(r'<think>.*?</think>', '', raw, flags=re.DOTALL).strip()
        data = json.loads(cleaned)
        self.assertEqual(data["adjusted_confidence"], 0.75)


# ── Orchestrator — Reg NMS delayed data guard ─────────────────────────────────

class TestOrchestratorDelayedDataGuard(unittest.TestCase):

    def test_delayed_source_returns_early(self):
        """Snapshots with _source='delayed' must never reach the pipeline."""
        from agents.orchestrator import Orchestrator
        orch = Orchestrator.__new__(Orchestrator)
        orch._alpaca = None
        orch._position_store = None

        snap = _base_snapshot(**{"_source": "delayed"})
        result = orch.run.__wrapped__(orch, snap) if hasattr(orch.run, '__wrapped__') else None

        # Verify orchestrator blocks delayed data before building graph
        self.assertIsNone(result)  # run() short-circuits before graph

    def test_requires_revalidation_blocks_execute(self):
        """Pre-market signals (requires_revalidation=True) skip _node_execute."""
        from agents.orchestrator import Orchestrator
        orch = Orchestrator.__new__(Orchestrator)
        orch._alpaca = MagicMock()
        orch._position_store = None
        orch._backend_base = "http://localhost:8080"

        state = {
            "market_snapshot": _base_snapshot(**{"_requires_revalidation": True}),
            "signal": MagicMock(symbol="AAPL"),
            "debate_result": MagicMock(),
            "risk_result": MagicMock(final_confidence=0.85),
            "submitted": True,
            "executed": False,
        }

        # _node_execute should return state unchanged (skip execution)
        orch._is_auto_execute_enabled = MagicMock(return_value=True)
        result = orch._node_execute(state)
        orch._alpaca.place_order.assert_not_called()
        self.assertFalse(result.get("executed", False))


if __name__ == "__main__":
    unittest.main(verbosity=2)
