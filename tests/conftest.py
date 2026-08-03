"""Shared fixtures for the execution-lane tests (plan v2.4 §5).

Offline, no network: the "broker" is a SimulatedBroker persisted in tmp_path,
the journal is a JSONL file in tmp_path, and time is a FakeClock the test
advances by hand — including across simulated days and crashes.
"""

import pytest

from execution_lane.broker import SimulatedBroker
from execution_lane.envelope import ClampConfig, OrderIntent
from execution_lane.lane import BracketSpec, ExecutionLane


class FakeClock:
    def __init__(self, iso="2026-08-03T09:30:00"):
        self._iso = iso

    def __call__(self):
        return self._iso

    def set(self, iso):
        self._iso = iso


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def clamps():
    return ClampConfig(max_order_notional=10_000.0, max_trades_per_day=10,
                       sleeve_caps={"m1": 50_000.0, "m2": 50_000.0})


@pytest.fixture
def bracket_spec():
    return BracketSpec(take_profit_pct=0.08, stop_loss_pct=0.04)


@pytest.fixture
def broker(tmp_path, clock):
    return SimulatedBroker(tmp_path / "broker.json", clock)


@pytest.fixture
def journal_path(tmp_path):
    return tmp_path / "journal.jsonl"


@pytest.fixture
def lane(broker, journal_path, clamps, bracket_spec, clock):
    return ExecutionLane(broker, journal_path, clamps, bracket_spec, clock)


def entry(symbol="AAA", qty=10, price=100.0, sleeve="m1", kind="entry"):
    return OrderIntent(symbol=symbol, side="buy", qty=qty, limit_price=price,
                       sleeve=sleeve, kind=kind)


def exit_intent(symbol="AAA", qty=10, price=100.0, sleeve="m1"):
    return OrderIntent(symbol=symbol, side="sell", qty=qty, limit_price=price,
                       sleeve=sleeve, kind="exit")
