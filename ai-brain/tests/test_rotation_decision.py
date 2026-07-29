"""
Unit tests for the dual_momentum rotation decision (main.rotation_decision).

Reproduces the 2026-07-29 incident: XLE was simultaneously the strongest ETF and
the held position, so rotation reduced candidates to [XLE] and the dedup gate then
removed it — 289 consecutive bars with zero candidates, 16 days without a signal,
while every infra check reported healthy.

Also covers the second defect found in the same investigation: when a DIFFERENT
ETF became strongest, the old code bought it without closing the incumbent,
accumulating correlated ETFs in a strategy documented as single-position.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import main
from main import rotation_decision


NOW = 1_800_000_000.0  # fixed clock so held_days is deterministic


def _pos(symbol: str, held_days: float, pos_id: str = "sig-1") -> dict:
    """Build an open-position row as the Go backend returns it (entry_time in ms)."""
    return {
        "id": pos_id,
        "symbol": symbol,
        "entry_time": int((NOW - held_days * 86_400) * 1000),
        "quantity": 160,
        "entry_price": 56.91,
        "status": "OPEN",
    }


class TestNoPosition:
    def test_enter_when_nothing_held(self):
        verdict, incumbent, _ = rotation_decision("XLE", [], {"XLE": 1.0}, now=NOW)
        assert verdict == "enter"
        assert incumbent is None


class TestIncumbentRetained:
    def test_strongest_is_already_held_holds_instead_of_deadlocking(self):
        """The 2026-07-29 bug: this case previously fell through to the dedup
        gate and looked identical to a dead pipeline."""
        verdict, incumbent, _ = rotation_decision(
            "XLE", [_pos("XLE", held_days=16)], {"XLE": 1.05}, now=NOW
        )
        assert verdict == "incumbent_retained"
        assert incumbent["symbol"] == "XLE"


class TestMultiPositionGuard:
    def test_two_open_positions_flagged(self):
        verdict, _, detail = rotation_decision(
            "GLD", [_pos("XLE", 10, "a"), _pos("QQQ", 10, "b")], {"GLD": 2.0}, now=NOW
        )
        assert verdict == "multi_position"
        assert detail["open_symbols"] == ["QQQ", "XLE"]

    def test_multi_position_wins_over_incumbent_retained(self):
        """qa finding 3 regression: a corrupt two-position state whose candidate
        is one of the held symbols must still surface as multi_position, not be
        masked as a routine hold."""
        verdict, _, _ = rotation_decision(
            "XLE", [_pos("XLE", 10, "a"), _pos("QQQ", 10, "b")], {"XLE": 2.0}, now=NOW
        )
        assert verdict == "multi_position"


class TestRotationGuards:
    def test_blocked_when_incumbent_unscoreable(self):
        """Never rotate on an unfair comparison — incumbent missing from scores."""
        verdict, _, _ = rotation_decision(
            "GLD", [_pos("XLE", held_days=10)], {"GLD": 2.0}, now=NOW
        )
        assert verdict == "blocked_unscoreable"

    def test_blocked_before_min_hold_elapsed(self):
        verdict, _, detail = rotation_decision(
            "GLD", [_pos("XLE", held_days=1)], {"XLE": 1.0, "GLD": 5.0}, now=NOW
        )
        assert verdict == "blocked_min_hold"
        assert detail["held_days"] < main.ROTATION_MIN_HOLD_DAYS

    def test_blocked_when_edge_insufficient(self):
        """Challenger is stronger but not by ROTATION_MIN_EDGE — holding beats
        paying the spread on noise."""
        verdict, _, _ = rotation_decision(
            "GLD", [_pos("XLE", held_days=10)],
            {"XLE": 1.00, "GLD": 1.01}, now=NOW,   # +1% < 2% required
        )
        assert verdict == "blocked_edge"

    def test_rotates_when_edge_and_hold_period_satisfied(self):
        verdict, incumbent, detail = rotation_decision(
            "GLD", [_pos("XLE", held_days=10)],
            {"XLE": 1.00, "GLD": 1.50}, now=NOW,
        )
        assert verdict == "rotate"
        assert incumbent["symbol"] == "XLE"
        assert detail["candidate"] == "GLD"

    def test_edge_boundary_is_inclusive(self):
        """Exactly ROTATION_MIN_EDGE clears the bar."""
        required = 1.0 * (1 + main.ROTATION_MIN_EDGE)
        verdict, _, _ = rotation_decision(
            "GLD", [_pos("XLE", held_days=10)],
            {"XLE": 1.0, "GLD": required}, now=NOW,
        )
        assert verdict == "rotate"


class TestHeldDaysMath:
    def test_entry_time_ms_converts_to_days(self):
        """entry_time is milliseconds; a precedence slip here would silently
        disable the min-hold guard."""
        _, _, detail = rotation_decision(
            "GLD", [_pos("XLE", held_days=5)], {"XLE": 1.0, "GLD": 1.0}, now=NOW
        )
        assert abs(detail["held_days"] - 5.0) < 0.001

    def test_defaults_to_wall_clock_when_now_omitted(self):
        recent = {"id": "s", "symbol": "XLE", "entry_time": int(time.time() * 1000)}
        verdict, _, detail = rotation_decision("GLD", [recent], {"XLE": 1.0, "GLD": 9.0})
        assert verdict == "blocked_min_hold"
        assert detail["held_days"] < 0.01
