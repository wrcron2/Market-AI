"""TCA friction dashboard (plan v2.4 §6.5) — the node's written acceptance
test, including §6's required simulated failure: the cost-model breach.

The alert spec, verbatim from the plan: "Alert if realized friction
exceeds model by >2 bps over any 20-trade window." The same condition is a
§9 live-pilot kill criterion, so the alert is CRITICAL on email +
dashboard. The model side arrives as plain data priced by the shared
fortress/cost_model.py — ops/ stays standalone (G0), pinned by
tests/test_ops_standalone_no_journal.py's AST scan.

Cost constants: every trade below is 100 sh x $100.00 = $10,000 notional,
so $1.00 of cost is exactly 1 bp.

Run from the repo root:  python -m pytest tests/test_tca_friction_alert.py -v
"""

from datetime import date, timedelta

import pytest

from ops.alerts import AlertSink
from ops.tca_friction import (FrictionMonitor, TOLERANCE_BPS, WINDOW_TRADES,
                              trades_from_csv, trades_from_dicts)

BASE = date(2026, 7, 1)


def _trade(i, sleeve="m1", realized=11.0, model=10.0):
    # $1.00 commission+spread on both sides; the rest lands in slippage,
    # so realized/model totals are exactly the arguments (in USD).
    return {"fill_id": f"{sleeve}-{i}", "sleeve": sleeve, "symbol": "AAA",
            "side": "buy", "qty": 100, "price": 100.0,
            "traded_on": (BASE + timedelta(days=i)).isoformat(),
            "commission": 0.40, "spread_cost": 0.60,
            "slippage": realized - 1.00,
            "model_commission": 0.40, "model_spread_cost": 0.60,
            "model_slippage": model - 1.00}


def _run(rows, sink=None):
    sink = sink or AlertSink()
    report = FrictionMonitor(sink).evaluate(trades_from_dicts(rows))
    return report, sink


# --- §6 simulated failure: the cost-model breach ------------------------------

def test_cost_model_breach_over_20_trade_window_alerts():
    """20 trades, each 2.5 bps richer than the model -> the specified
    CRITICAL alert on email + dashboard, citing the window and the kill
    criterion."""
    report, sink = _run([_trade(i, realized=12.5) for i in range(20)])

    assert sink.has("tca_friction", "CRITICAL")
    alerts = sink.for_service("tca_friction")
    assert len(alerts) == 1                        # one alert per sleeve
    alert = alerts[0]
    assert set(alert.channels) == {"email", "dashboard"}
    assert "2.50 bps" in alert.message and "20-trade window" in alert.message
    assert alert.details["sleeve"] == "m1"
    assert alert.details["worst_excess_bps"] == pytest.approx(2.5)
    assert alert.details["window_trades"] == WINDOW_TRADES == 20
    assert alert.details["tolerance_bps"] == TOLERANCE_BPS == 2.0
    assert alert.details["kill_criterion"] is True

    sleeve = report["sleeves"]["m1"]
    assert sleeve["windows_evaluated"] == 1
    assert sleeve["breaching_windows"] == 1
    window = sleeve["windows"][0]
    assert window["breach"] is True
    assert window["realized_bps"] == pytest.approx(12.5)
    assert window["model_bps"] == pytest.approx(10.0)
    assert report["alerts_raised"] == 1


# --- the quiet paths ------------------------------------------------------------

def test_realized_inside_model_bands_stays_quiet():
    """§6 acceptance: the first 20 paper trades landing inside the model's
    bands produce a full dashboard report and zero alerts."""
    report, sink = _run([_trade(i, realized=11.0) for i in range(20)])

    assert sink.for_service("tca_friction") == []
    assert report["alerts_raised"] == 0
    sleeve = report["sleeves"]["m1"]
    assert sleeve["windows_evaluated"] == 1
    assert sleeve["breaching_windows"] == 0
    assert sleeve["windows"][0]["excess_bps"] == pytest.approx(1.0)
    assert sleeve["windows"][0]["breach"] is False
    assert len(sleeve["trade_rows"]) == 20


def test_exactly_2_bps_excess_does_not_alert():
    """The plan says 'exceeds ... by >2 bps': exactly at the tolerance is
    inside the bands, not a breach."""
    report, sink = _run([_trade(i, realized=12.0) for i in range(20)])

    assert sink.for_service("tca_friction") == []
    window = report["sleeves"]["m1"]["windows"][0]
    assert window["excess_bps"] == pytest.approx(2.0)
    assert window["breach"] is False


def test_partial_window_never_alerts():
    """19 trades at a 5 bps excess: no complete 20-trade window exists, so
    no alert — but the trades still land on the dashboard."""
    report, sink = _run([_trade(i, realized=15.0) for i in range(19)])

    assert sink.for_service("tca_friction") == []
    sleeve = report["sleeves"]["m1"]
    assert sleeve["windows_evaluated"] == 0 and sleeve["windows"] == []
    assert sleeve["trades"] == 19
    assert sleeve["mean_realized_bps"] == pytest.approx(15.0)


def test_any_20_trade_window_catches_a_localized_breach():
    """'Any' window means sliding: 25 trades, clean for the first 5 then
    2.5 bps hot — the head window stays clean, later windows catch it."""
    rows = [_trade(i, realized=10.4) for i in range(5)]
    rows += [_trade(i, realized=12.5) for i in range(5, 25)]
    report, sink = _run(rows)

    sleeve = report["sleeves"]["m1"]
    assert sleeve["windows_evaluated"] == 6
    assert sleeve["windows"][0]["breach"] is False      # 1.975 bps excess
    assert sleeve["windows"][0]["excess_bps"] == pytest.approx(1.975)
    assert sleeve["breaching_windows"] == 5
    assert sleeve["windows"][-1]["breach"] is True
    assert sink.has("tca_friction", "CRITICAL")
    assert sink.for_service("tca_friction")[0].details[
        "worst_excess_bps"] == pytest.approx(2.5)


def test_sleeves_are_evaluated_independently():
    """Rolling bps are by sleeve: a clean m1 does not dilute a breaching
    d1, and only d1 alerts."""
    rows = [_trade(i, sleeve="m1", realized=11.0) for i in range(20)]
    rows += [_trade(i, sleeve="d1", realized=12.5) for i in range(20)]
    report, sink = _run(rows)

    alerts = sink.for_service("tca_friction")
    assert len(alerts) == 1
    assert alerts[0].details["sleeve"] == "d1"
    assert report["sleeves"]["m1"]["breaching_windows"] == 0
    assert report["sleeves"]["d1"]["breaching_windows"] == 1


# --- per-trade math and ingestion ----------------------------------------------

def test_per_trade_bps_math_and_components():
    """Commission, spread capture and slippage sum to the per-trade cost;
    bps normalize by notional; negative slippage (price improvement) is
    preserved, not clamped."""
    trades = trades_from_dicts([{
        "fill_id": "x1", "sleeve": "m1", "symbol": "AAA", "side": "sell",
        "qty": 10, "price": 250.0, "traded_on": "2026-07-01",
        "commission": 0.35, "spread_cost": 0.50, "slippage": -0.25,
        "model_commission": 0.35, "model_spread_cost": 0.375,
        "model_slippage": 0.0}])
    t = trades[0]
    assert t.notional == 2500.0
    assert t.realized_cost == pytest.approx(0.60)
    assert t.realized_bps == pytest.approx(2.4)         # 0.60 / 2500 x 1e4
    assert t.model_cost == pytest.approx(0.725)
    assert t.model_bps == pytest.approx(2.9)
    assert t.excess_bps == pytest.approx(-0.5)          # beat the model

    report, sink = _run([{
        "fill_id": "x1", "sleeve": "m1", "symbol": "AAA", "side": "sell",
        "qty": 10, "price": 250.0, "traded_on": "2026-07-01",
        "commission": 0.35, "spread_cost": 0.50, "slippage": -0.25,
        "model_commission": 0.35, "model_spread_cost": 0.375}])
    comp = report["sleeves"]["m1"]["components_bps"]
    assert comp["commission"]["realized"] == pytest.approx(1.4)
    assert comp["spread"]["realized"] == pytest.approx(2.0)
    assert comp["slippage"]["realized"] == pytest.approx(-1.0)
    assert comp["slippage"]["model"] == pytest.approx(0.0)
    assert sink.for_service("tca_friction") == []       # 1 trade, no window


def test_trades_from_csv_and_defaults(tmp_path):
    """CSV ingestion mirrors the other ops modules; missing cost columns
    default to 0.0 so a minimal fills export still parses."""
    path = tmp_path / "trades.csv"
    path.write_text(
        "fill_id,sleeve,symbol,side,qty,price,traded_on,commission\n"
        "c1,m1,AAA,buy,100,100.0,2026-07-01,0.40\n")
    trades = trades_from_csv(path)
    assert len(trades) == 1
    assert trades[0].traded_on == date(2026, 7, 1)
    assert trades[0].slippage == 0.0 and trades[0].model_cost == 0.0
    assert trades[0].realized_bps == pytest.approx(0.4)


def test_invalid_trades_fail_loud():
    with pytest.raises(ValueError, match="qty x price"):
        trades_from_dicts([{"sleeve": "m1", "symbol": "AAA", "side": "buy",
                            "qty": 0, "price": 100.0,
                            "traded_on": "2026-07-01"}])
    with pytest.raises(ValueError, match=r"buy\|sell"):
        trades_from_dicts([{"sleeve": "m1", "symbol": "AAA", "side": "hold",
                            "qty": 100, "price": 100.0,
                            "traded_on": "2026-07-01"}])
