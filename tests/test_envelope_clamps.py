"""Clamp unit tests at boundary values (plan v2.4 §5.3 — the section's
written acceptance test).

The envelope (execution_lane/envelope.py) carries the three clamps the plan
names — max order notional, trades per day, per-sleeve caps — and these
tests pin their behavior AT the boundaries: exactly-at-cap passes, one cent
over clamps, whole-share rounding lands exactly on multiples, zero-headroom
rejects, and no clamp ever touches an exit. If one of these fails because a
semantic was changed, the change belongs in a pre-registration amendment
(preregistration_template.md), not in this file.

Run from the repo root:  python -m pytest tests/test_envelope_clamps.py -v
"""

import pytest

from execution_lane import envelope as env
from execution_lane.envelope import ClampConfig, OrderIntent

from conftest import entry, exit_intent


def decide(intent, config, trades_today=0, sleeve_exposure=0.0):
    return env.clamp(intent, config, trades_today=trades_today,
                     sleeve_exposure=sleeve_exposure)


CFG = ClampConfig(max_order_notional=1_000.0, max_trades_per_day=3,
                  sleeve_caps={"m1": 5_000.0})


class TestMalformedIntents:
    def test_zero_qty_rejected(self):
        d = decide(entry(qty=0), CFG)
        assert not d.allowed and d.reasons == ("nonpositive_qty",)

    @pytest.mark.parametrize("qty", [-1, -100])
    def test_negative_qty_rejected(self, qty):
        d = decide(entry(qty=qty), CFG)
        assert not d.allowed and d.reasons == ("nonpositive_qty",)

    @pytest.mark.parametrize("price", [0.0, -0.01, -150.0])
    def test_nonpositive_price_rejected_without_crashing(self, price):
        d = decide(entry(price=price), CFG)
        assert not d.allowed and d.reasons == ("nonpositive_price",)

    def test_malformed_exit_still_rejected(self):
        d = decide(exit_intent(qty=0), CFG)
        assert not d.allowed and d.reasons == ("nonpositive_qty",)


class TestNotionalClampBoundaries:
    def test_notional_exactly_at_cap_passes_unclamped(self):
        # 10 x $100.00 == $1,000.00 cap exactly: boundary is inclusive
        d = decide(entry(qty=10, price=100.0), CFG)
        assert d.allowed and d.qty == 10 and not d.clamped and d.reasons == ()

    def test_one_share_over_cap_clamps_to_cap(self):
        d = decide(entry(qty=11, price=100.0), CFG)   # $1,100 > $1,000
        assert d.allowed and d.qty == 10 and d.clamped
        assert d.reasons == ("order_notional",)

    def test_one_cent_over_cap_clamps(self):
        # 10 x $100.01 = $1,000.10 > cap -> 9 shares ($900.09)
        d = decide(entry(qty=10, price=100.01), CFG)
        assert d.allowed and d.qty == 9 and d.reasons == ("order_notional",)

    def test_exact_division_lands_exactly_despite_ieee_repr(self):
        # 1.00 / 0.10 is 9.999999999999998 in doubles; the floor epsilon must
        # make the pinned decimal boundary exact, not shave a share off
        cfg = ClampConfig(max_order_notional=1.0, max_trades_per_day=3)
        d = decide(entry(qty=10, price=0.10), cfg)
        assert d.allowed and d.qty == 10 and not d.clamped

    def test_price_above_cap_clamps_to_zero_and_rejects(self):
        d = decide(entry(qty=1, price=1_200.0), CFG)   # one share already over
        assert not d.allowed and d.qty == 0
        assert d.reasons == ("order_notional", "zeroed")

    def test_m1_band_cap_consequence(self):
        # $1,000 order cap into the $150 band cap buys 6 shares ($900), not 7
        d = decide(entry(qty=7, price=150.0), CFG)
        assert d.allowed and d.qty == 6 and d.qty * 150.0 == 900.0

    def test_below_cap_untouched(self):
        d = decide(entry(qty=3, price=100.0), CFG)
        assert d.allowed and d.qty == 3 and not d.clamped


class TestTradesPerDayBoundaries:
    def test_below_limit_passes(self):
        assert decide(entry(), CFG, trades_today=2).allowed          # max 3

    def test_at_limit_rejects(self):
        d = decide(entry(), CFG, trades_today=3)
        assert not d.allowed and d.reasons == ("trades_per_day",)

    def test_above_limit_rejects(self):
        d = decide(entry(), CFG, trades_today=10)
        assert not d.allowed and d.reasons == ("trades_per_day",)

    def test_limit_of_one(self):
        cfg = ClampConfig(max_order_notional=1_000.0, max_trades_per_day=1)
        assert decide(entry(), cfg, trades_today=0).allowed
        assert not decide(entry(), cfg, trades_today=1).allowed

    def test_limit_of_zero_rejects_everything(self):
        cfg = ClampConfig(max_order_notional=1_000.0, max_trades_per_day=0)
        assert not decide(entry(), cfg, trades_today=0).allowed

    def test_trades_check_fires_before_notional_clamp(self):
        # an over-cap order on a maxed day reports the day limit, nothing else
        d = decide(entry(qty=1, price=1_200.0), CFG, trades_today=3)
        assert d.reasons == ("trades_per_day",)


class TestSleeveCapBoundaries:
    def test_exact_headroom_passes(self):
        # $4,000 used of $5,000; 10 x $100 fills the sleeve exactly
        d = decide(entry(qty=10, price=100.0), CFG, sleeve_exposure=4_000.0)
        assert d.allowed and d.qty == 10 and not d.clamped

    def test_one_share_over_headroom_clamps(self):
        # 10 x $100 passes the $1,000 order clamp; sleeve headroom allows 9
        d = decide(entry(qty=10, price=100.0), CFG, sleeve_exposure=4_100.0)
        assert d.allowed and d.qty == 9 and d.reasons == ("sleeve_cap",)

    def test_sleeve_full_rejects(self):
        d = decide(entry(qty=1, price=100.0), CFG, sleeve_exposure=5_000.0)
        assert not d.allowed and d.reasons == ("sleeve_cap", "zeroed")

    def test_sleeve_over_full_rejects(self):
        d = decide(entry(qty=1, price=100.0), CFG, sleeve_exposure=6_000.0)
        assert not d.allowed and d.reasons == ("sleeve_cap", "zeroed")

    def test_headroom_below_one_share_rejects(self):
        # $50 of headroom at a $100 name cannot buy a whole share
        d = decide(entry(qty=1, price=100.0), CFG, sleeve_exposure=4_950.0)
        assert not d.allowed and d.reasons == ("sleeve_cap", "zeroed")

    def test_unlisted_sleeve_is_uncapped(self):
        d = decide(entry(qty=100, price=100.0, sleeve="other"), CFG,
                   sleeve_exposure=1_000_000.0)
        # only the $1,000 order-notional clamp applies
        assert d.allowed and d.qty == 10 and d.reasons == ("order_notional",)


class TestExitsAreNeverClamped:
    def test_exit_passes_on_a_maxed_day_over_full_sleeve_over_order_cap(self):
        d = decide(exit_intent(qty=500, price=200.0), CFG,
                   trades_today=3, sleeve_exposure=9_000.0)
        assert d.allowed and d.qty == 500 and not d.clamped and d.reasons == ()

    def test_exit_keeps_full_qty_when_sleeve_is_over_cap(self):
        # a clamped exit would strand residue; the whole position must go
        d = decide(exit_intent(qty=37, price=64.0), CFG, sleeve_exposure=8_888.0)
        assert d.allowed and d.qty == 37


class TestClampComposition:
    def test_tightest_clamp_wins_and_both_reasons_reported(self):
        # notional allows 10 ($1,000 cap), sleeve headroom allows only 6
        d = decide(entry(qty=20, price=100.0), CFG, sleeve_exposure=4_400.0)
        assert d.allowed and d.qty == 6 and d.clamped
        assert d.reasons == ("order_notional", "sleeve_cap")

    def test_sleeve_tighter_than_notional_when_headroom_is_small(self):
        d = decide(entry(qty=20, price=100.0), CFG, sleeve_exposure=4_900.0)
        assert d.allowed and d.qty == 1
        assert d.reasons == ("order_notional", "sleeve_cap")


class TestLaneIntegration:
    """The clamps wired into the lane's submit flow (journal + broker)."""

    def test_clamped_order_is_placed_at_clamped_qty_and_journaled(self, lane):
        lane.resync("2026-08-03")
        result = lane.submit(entry(qty=999, price=100.0))   # $99,900 intent
        assert result.allowed and result.qty == 100           # $10,000 cap
        assert result.reasons == ("order_notional",)
        parent = lane.broker.open_orders()  # children only; parent filled
        assert all(o["qty"] == 100 for o in parent)
        cmd = [e for e in lane.journal.replay() if e["kind"] == "command"][-1]
        assert cmd["intent"]["qty"] == 999                    # intent as sent
        assert cmd["envelope"]["decision"]["qty"] == 100      # envelope verdict
        assert cmd["envelope"]["decision"]["reasons"] == ["order_notional"]

    def test_rejected_order_never_reaches_the_broker(self, lane):
        lane.resync("2026-08-03")
        result = lane.submit(entry(qty=1, price=20_000.0))    # over cap: zero
        assert not result.allowed
        assert result.reasons == ("order_notional", "zeroed")
        assert lane.broker.known_intent_ids() == set()
        kinds = [e["kind"] for e in lane.journal.replay()]
        assert kinds[-2:] == ["command", "rejected"]

    def test_trades_per_day_is_derived_from_broker_truth_and_resets_by_day(
            self, lane, clock):
        lane.resync("2026-08-03")
        for _ in range(10):                                   # the day's max
            assert lane.submit(entry(qty=1, price=100.0)).allowed
        blocked = lane.submit(entry(qty=1, price=100.0))
        assert not blocked.allowed and blocked.reasons == ("trades_per_day",)
        # a new session derives a fresh count from broker truth — a crash
        # cannot reset the guard, and a new day always does
        clock.set("2026-08-04T09:30:00")
        lane.resync("2026-08-04")
        assert lane.submit(entry(qty=1, price=100.0)).allowed

    def test_exit_is_allowed_even_on_a_maxed_day(self, lane):
        lane.resync("2026-08-03")
        for _ in range(10):
            lane.submit(entry(qty=1, price=100.0))
        result = lane.submit(exit_intent(qty=1, price=101.0))
        assert result.allowed and result.qty == 1

    def test_disarmed_lane_refuses_orders(self, lane):
        result = lane.submit(entry())
        assert not result.allowed and result.reasons == ("disarmed",)
