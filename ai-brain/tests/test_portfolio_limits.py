"""
Unit tests for the deterministic portfolio limits (agents/portfolio_limits.py).
Reproduces the 2026-07-09 QQQ incident: 112 shares @ $721 on $100k equity
must be capped to ~13 shares (10% = $10k), regardless of upstream agents.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.portfolio_limits import PortfolioLimits


class FakeAlpaca:
    def __init__(self, equity=100_000.0, positions=None, fail=False):
        self.equity = equity
        self.positions = positions or []
        self.fail = fail

    def get_account(self):
        if self.fail:
            raise RuntimeError("alpaca down")
        return {"equity": str(self.equity)}

    def get_all_positions(self):
        if self.fail:
            raise RuntimeError("alpaca down")
        return self.positions


def _limits(alpaca):
    lim = PortfolioLimits(alpaca)
    lim.max_position_pct = 0.10
    lim.max_sector_pct = 0.30
    lim.max_open = 10
    lim.drawdown_suspend = 0.15
    lim.initial_equity = 100_000.0
    return lim


def test_qqq_incident_is_capped():
    """The exact live failure: 112 QQQ @ $721 → must shrink to 10% of equity."""
    lim = _limits(FakeAlpaca(equity=100_000))
    v = lim.enforce("QQQ", "BUY", quantity=112, price=721.06)
    assert not v.blocked
    assert v.adjusted_quantity == 13  # floor(10_000 / 721.06)
    assert v.adjusted_quantity * 721.06 <= 10_000


def test_existing_exposure_counts_against_cap():
    lim = _limits(FakeAlpaca(
        equity=100_000,
        positions=[{"symbol": "QQQ", "market_value": "8000"}],
    ))
    v = lim.enforce("QQQ", "BUY", quantity=100, price=100.0)
    assert not v.blocked
    assert v.adjusted_quantity == 20  # only $2k of the $10k cap remains


def test_cap_reached_blocks():
    lim = _limits(FakeAlpaca(
        equity=100_000,
        positions=[{"symbol": "QQQ", "market_value": "10500"}],
    ))
    v = lim.enforce("QQQ", "BUY", quantity=10, price=700.0)
    assert v.blocked
    assert "cap" in v.reason


def test_max_open_positions_blocks_new_symbol():
    positions = [{"symbol": f"SYM{i}", "market_value": "1000"} for i in range(10)]
    lim = _limits(FakeAlpaca(equity=100_000, positions=positions))
    v = lim.enforce("NEWSYM", "BUY", quantity=5, price=100.0)
    assert v.blocked
    assert "open positions" in v.reason


def test_drawdown_suspends_buys_but_allows_sells():
    lim = _limits(FakeAlpaca(equity=84_000))  # below the $85k floor
    buy = lim.enforce("SPY", "BUY", quantity=10, price=100.0)
    sell = lim.enforce("SPY", "SELL", quantity=10, price=100.0)
    assert buy.blocked and "drawdown" in buy.reason
    assert not sell.blocked and sell.adjusted_quantity == 10


def test_fail_closed_when_state_unavailable():
    lim = _limits(FakeAlpaca(fail=True))
    buy = lim.enforce("SPY", "BUY", quantity=10, price=100.0)
    sell = lim.enforce("SPY", "SELL", quantity=10, price=100.0)
    assert buy.blocked and "fail-closed" in buy.reason
    assert not sell.blocked  # closing risk is always allowed


def test_sector_cap_binds_across_symbols():
    lim = _limits(FakeAlpaca(
        equity=100_000,
        positions=[{"symbol": "XLK", "market_value": "25000"}],  # tech sector
    ))
    # QQQ is also 'tech' → only $5k of the $30k sector cap remains
    v = lim.enforce("QQQ", "BUY", quantity=100, price=100.0)
    assert not v.blocked
    assert v.adjusted_quantity == 50


def test_within_all_limits_passes_unchanged():
    lim = _limits(FakeAlpaca(equity=100_000))
    v = lim.enforce("XLE", "BUY", quantity=50, price=57.0)  # $2,850 ≪ caps
    assert not v.blocked
    assert v.adjusted_quantity == 50
    assert v.reason == ""
