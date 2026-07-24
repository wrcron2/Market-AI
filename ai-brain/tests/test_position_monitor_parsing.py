"""
test_position_monitor_parsing.py — Regression tests for the 2026-07 monitor bug.

Root cause found 2026-07-24: LLMRouter forced format="json" on EVERY Ollama call,
so the position monitor's plain-text "HOLD./SELL./UNCERTAIN." prompt could never
be answered — the model emitted a JSON echo of the position, _parse_decision saw
"{" and returned UNCERTAIN on every cycle. The monitor's LLM layers (2/3/4) were
dead; only hard rules (stop-loss / take-profit / SMA20) ever fired.

These tests pin down:
  1. _resolve_format: plain-text calls must NOT be forced into JSON mode.
  2. _parse_decision: robust to <think> blocks, JSON echoes, and prose.

All tests use mocked Ollama — no real LLM calls.
Run: python -m pytest ai-brain/tests/test_position_monitor_parsing.py -v
"""
import os
import sys
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agents.router import _resolve_format          # noqa: E402
from agents.position_monitor import _parse_decision  # noqa: E402


# ── Router format resolution ──────────────────────────────────────────────────

class TestResolveFormat(unittest.TestCase):
    def test_schema_wins(self):
        schema = MagicMock()
        schema.model_json_schema.return_value = {"type": "object"}
        self.assertEqual(_resolve_format(schema, json_mode=True), {"type": "object"})

    def test_json_mode_default(self):
        self.assertEqual(_resolve_format(None, json_mode=True), "json")

    def test_plain_text_returns_none(self):
        """The monitor bug: plain-text callers must get format=None, not 'json'."""
        self.assertIsNone(_resolve_format(None, json_mode=False))


# ── Decision parsing ──────────────────────────────────────────────────────────

class TestParseDecision(unittest.TestCase):
    def test_compliant_hold(self):
        self.assertEqual(_parse_decision("HOLD. Momentum intact, no exit signal."), "HOLD")

    def test_compliant_sell(self):
        self.assertEqual(_parse_decision("SELL. Trend broken below SMA20."), "SELL")

    def test_compliant_uncertain(self):
        self.assertEqual(_parse_decision("UNCERTAIN. Mixed signals."), "UNCERTAIN")

    def test_lowercase_and_punctuation(self):
        self.assertEqual(_parse_decision("hold, momentum fine"), "HOLD")

    def test_deepseek_think_block_stripped(self):
        raw = "<think>The position is up 4%, trend intact...</think>\nHOLD. Trend intact."
        self.assertEqual(_parse_decision(raw), "HOLD")

    def test_json_echo_is_uncertain(self):
        """The exact live failure mode: model echoes the position as JSON."""
        raw = '{\n  "Symbol": "XLE",\n  "Side": "LONG",\n  "Quantity": 160\n}'
        self.assertEqual(_parse_decision(raw), "UNCERTAIN")

    def test_json_with_decision_key(self):
        self.assertEqual(_parse_decision('{"decision": "SELL", "reason": "stop"}'), "SELL")

    def test_json_with_action_key(self):
        self.assertEqual(_parse_decision('{"action": "hold."}'), "HOLD")

    def test_empty_is_uncertain(self):
        self.assertEqual(_parse_decision(""), "UNCERTAIN")
        self.assertEqual(_parse_decision("   "), "UNCERTAIN")

    def test_prose_single_decision_word(self):
        self.assertEqual(
            _parse_decision("Given the strong momentum I would HOLD this position."),
            "HOLD",
        )

    def test_prose_conflicting_words_is_uncertain(self):
        """'I would not SELL, HOLD instead' must never be read as SELL."""
        self.assertEqual(
            _parse_decision("I would not SELL here; HOLD instead."),
            "UNCERTAIN",
        )

    def test_unrelated_text_is_uncertain(self):
        self.assertEqual(_parse_decision("The market looks fine today."), "UNCERTAIN")


if __name__ == "__main__":
    unittest.main()
