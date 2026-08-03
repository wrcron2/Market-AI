"""Boundary tests for the shared cost model (fortress/cost_model.py).

Plan v2.4 §1.3 pins the constants; §4.1 makes this module the single source of
truth for Item Zero, the fortress, and later live TCA. These tests pin the
boundary behavior: spread-bucket edges, the commission floor crossover, the
zero-share guards, and the whole-share sizing-band consequences (§8). If one
of these fails because a constant was tuned, the fix is to revert the
constant and file a pre-registration amendment — not to edit the test.

Run from the repo root:  python -m pytest tests/test_cost_model_boundaries.py -v
"""

import math

import pytest

from fortress import cost_model as cm


class TestPinnedConstants:
    def test_constants_are_the_preregistered_values(self):
        assert cm.COMMISSION_MIN_PER_SIDE == 0.35
        assert cm.COMMISSION_PER_SHARE == 0.0035
        assert cm.PASSTHROUGH_PER_SHARE == 0.0032
        assert cm.SPREAD_BPS_BUCKETS == (
            (20.0, 50.0, 5.0),
            (50.0, 100.0, 3.0),
            (100.0, 150.0, 2.0),
        )


class TestSpreadBucketBoundaries:
    @pytest.mark.parametrize("price", [0.0, 1.0, 19.99, 20.0, 35.0, 49.9999])
    def test_low_bucket_includes_below_band_clamp(self, price):
        assert cm.spread_bps(price) == 5.0

    @pytest.mark.parametrize("price", [50.0, 75.0, 99.9999])
    def test_mid_bucket(self, price):
        assert cm.spread_bps(price) == 3.0

    @pytest.mark.parametrize("price", [100.0, 125.0, 149.9999, 150.0, 500.0])
    def test_high_bucket_includes_above_band_clamp(self, price):
        assert cm.spread_bps(price) == 2.0

    def test_edges_flip_exactly_at_bucket_bounds(self):
        # Buckets are [lo, hi): the bound itself belongs to the upper bucket.
        assert cm.spread_bps(20.0 - 1e-9) == 5.0   # below band -> nearest bucket, no crash
        assert cm.spread_bps(20.0) == 5.0
        assert cm.spread_bps(50.0 - 1e-9) == 5.0
        assert cm.spread_bps(50.0) == 3.0
        assert cm.spread_bps(100.0 - 1e-9) == 3.0
        assert cm.spread_bps(100.0) == 2.0


class TestCommissionBoundaries:
    def test_zero_and_negative_share_orders_are_free(self):
        assert cm.commission(0) == 0.0
        assert cm.commission(-10) == 0.0

    def test_floor_binds_for_small_orders(self):
        # 1 share: per-share rate (0.0035) is far under the 0.35 floor.
        assert cm.commission(1) == pytest.approx(0.35 + 0.0032)

    def test_floor_per_share_crossover_at_exactly_100_shares(self):
        # 0.0035 * shares overtakes the 0.35 floor at shares = 100 exactly.
        assert cm.commission(100) == pytest.approx(0.35 + 100 * 0.0032)
        assert cm.commission(101) == pytest.approx(101 * 0.0035 + 101 * 0.0032)

    def test_floor_binds_across_the_whole_m1_order_size_range(self):
        # M1 sizing ($1,000 target into $20–150 names) yields 6–50 shares; the
        # $0.35 floor binds for every order up to 100 shares — the floor is the
        # whole story at this system's size, per the pinned spec.
        for shares in range(1, 101):
            # epsilon: 0.0035*100 is 0.35 in decimal but 0.35000000000000003
            # in IEEE doubles — the crossover is exact in the pinned decimals
            assert cm.COMMISSION_PER_SHARE * shares <= cm.COMMISSION_MIN_PER_SIDE + 1e-12
            assert cm.commission(shares) == pytest.approx(
                cm.COMMISSION_MIN_PER_SIDE + cm.PASSTHROUGH_PER_SHARE * shares)

    def test_strictly_increasing_in_shares(self):
        costs = [cm.commission(s) for s in range(1, 300)]
        assert all(b > a for a, b in zip(costs, costs[1:]))


class TestTradeCostBoundaries:
    def test_zero_and_negative_share_trades_are_free(self):
        assert cm.trade_cost(0, 75.0) == 0.0
        assert cm.trade_cost(-3, 75.0) == 0.0

    @pytest.mark.parametrize("price", [20.0, 49.9999, 50.0, 99.9999, 100.0, 150.0])
    def test_composition_at_bucket_edges(self, price):
        shares = 17
        expected = cm.commission(shares) + shares * price * cm.spread_bps(price) / 1e4
        assert cm.trade_cost(shares, price) == pytest.approx(expected)

    def test_spread_component_steps_down_at_bucket_boundary(self):
        # Same share count, price crossing $50: the 5 -> 3 bps bucket step must
        # appear in the total even though the price itself rose.
        below = cm.trade_cost(20, 49.9999)
        at = cm.trade_cost(20, 50.0)
        assert at < below
        assert below - at == pytest.approx(20 * (49.9999 * 5.0 - 50.0 * 3.0) / 1e4)

    def test_m1_band_positions_land_in_the_pinned_sizing_band(self):
        # $1,000 target, whole shares rounded down, any $20–150 entry price:
        # every position must land in the plan's $640–1,070 sizing band (§1.3).
        for cents in range(2000, 15001):
            price = cents / 100
            shares = math.floor(1000.0 / price)
            notional = shares * price
            assert 640.0 <= notional <= 1070.0, f"price {price}"
