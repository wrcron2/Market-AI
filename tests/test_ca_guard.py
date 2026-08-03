"""Corporate-actions guard (plan v2.4 §5.5 — the section's written
acceptance test: a simulated split on a held name leaves no orphaned
bracket).

The scenario mirrors the plan: a position is held with live brackets; a 2:1
split goes ex overnight, re-terming the position (10 @ $120 -> 20 @ $60) at
the broker while the resting brackets keep their pre-split terms. Before the
session opens, the guard (execution_lane/ca_guard.py) must cancel every
stale order on the name and rebuild full-coverage brackets from the
post-split position — because the §9 kill criteria halt the sleeve on any
orphaned bracket after a corporate action.

Calendar rows use the §3 data layer's corporate_actions shape
(symbol, ex_date, action_type, ratio), so the guard consumes
data_layer.query.corporate_actions_between output unchanged when wired up.

Run from the repo root:  python -m pytest tests/test_ca_guard.py -v
"""

from conftest import entry

DAY1 = "2026-08-03"
DAY2 = "2026-08-04"
SPLIT = {"symbol": "AAA", "ex_date": DAY2, "action_type": "split", "ratio": 2.0}


def test_simulated_split_leaves_no_orphaned_bracket(lane, broker, clock):
    lane.start_session(DAY1)
    lane.submit(entry("AAA", 10, 120.0))                    # TP 129.60 / SL 115.20
    stale_ids = {o["order_id"] for o in broker.open_orders()}
    assert len(stale_ids) == 2

    # overnight the split goes ex: the position is re-termed at the broker,
    # the resting brackets are NOT (venues do not rewrite your orders)
    broker.apply_split("AAA", 2.0, DAY2)
    assert broker.positions()[0]["qty"] == 20
    assert broker.positions()[0]["avg_cost"] == 60.0
    assert {o["qty"] for o in broker.open_orders()} == {10}  # stale terms

    clock.set(DAY2 + "T09:30:00")
    _, ca_report, coverage = lane.start_session(DAY2, ca_calendar=[SPLIT])

    # every stale order cancelled, full-coverage brackets rebuilt in
    # post-split terms, and the §9 invariant holds: no orphaned bracket
    assert set(ca_report["cancelled"]) == stale_ids
    assert ca_report["handled"] == ["AAA|2026-08-04|split"]
    legs = [o for o in broker.open_orders() if o["symbol"] == "AAA"]
    assert stale_ids.isdisjoint(o["order_id"] for o in legs)
    assert {o["role"] for o in legs} == {"take_profit", "stop_loss"}
    assert all(o["qty"] == 20 for o in legs)
    prices = {o["role"]: o["limit_price"] for o in legs}
    assert prices["take_profit"] == 64.8 and prices["stop_loss"] == 57.6
    assert lane.find_orphaned_orders() == []
    assert coverage.ok and lane.armed

    replay = lane.journal.replay()
    ca_cancels = [e for e in replay if e["kind"] == "ca_cancel"]
    assert {e["order_id"] for e in ca_cancels} == stale_ids
    handled = [e for e in replay if e["kind"] == "ca_handled"]
    assert len(handled) == 1 and handled[0]["action"] == "cancel_rebuild"


def test_guard_is_idempotent_across_restart(lane, broker, clock, journal_path,
                                            clamps, bracket_spec):
    from execution_lane.lane import ExecutionLane
    lane.start_session(DAY1)
    lane.submit(entry("AAA", 10, 120.0))
    broker.apply_split("AAA", 2.0, DAY2)
    clock.set(DAY2 + "T09:30:00")
    lane.start_session(DAY2, ca_calendar=[SPLIT])
    stable = broker.open_orders()

    # restart and run the same session again: the CA is already handled, so
    # nothing is cancelled, rebuilt, or re-journaled
    del lane
    lane2 = ExecutionLane(broker, journal_path, clamps, bracket_spec, clock)
    _, ca_report2, coverage2 = lane2.start_session(DAY2, ca_calendar=[SPLIT])
    assert ca_report2["cancelled"] == [] and ca_report2["rebuilt"] == []
    assert ca_report2["handled"] == []
    assert broker.open_orders() == stable
    assert coverage2.ok
    handled = [e for e in lane2.journal.replay() if e["kind"] == "ca_handled"]
    assert len(handled) == 1


def test_crash_mid_guard_resumes_safely(lane, broker, clock, journal_path,
                                        clamps, bracket_spec):
    """Crash after the cancel but before ca_handled: the CA key was never
    marked handled, so the restarted guard re-runs it — cancellation is
    idempotent at the broker and the rebuild still lands exactly once."""
    from execution_lane.lane import ExecutionLane
    lane.start_session(DAY1)
    lane.submit(entry("AAA", 10, 120.0))
    broker.apply_split("AAA", 2.0, DAY2)
    clock.set(DAY2 + "T09:30:00")

    # simulate dying mid-guard: one stale leg cancelled + journaled, no
    # ca_handled marker, then the process is gone
    stale_tp = [o for o in broker.open_orders() if o["role"] == "take_profit"][0]
    broker.cancel_order(stale_tp["order_id"])
    lane.journal.append("ca_cancel", ca_key="AAA|2026-08-04|split",
                        order_id=stale_tp["order_id"], symbol="AAA",
                        stale_terms={"qty": 10, "limit_price": 129.6,
                                     "role": "take_profit"})
    lane.journal.flush()
    del lane

    lane2 = ExecutionLane(broker, journal_path, clamps, bracket_spec, clock)
    _, ca_report, coverage = lane2.start_session(DAY2, ca_calendar=[SPLIT])
    assert ca_report["handled"] == ["AAA|2026-08-04|split"]
    assert coverage.ok and lane2.armed
    legs = [o for o in broker.open_orders() if o["symbol"] == "AAA"]
    assert {o["role"] for o in legs} == {"take_profit", "stop_loss"}
    assert all(o["qty"] == 20 for o in legs)
    assert lane2.find_orphaned_orders() == []


def test_split_on_a_name_with_no_exposure_is_handled_without_action(
        lane, broker, clock):
    lane.start_session(DAY1)
    lane.submit(entry("AAA", 10, 120.0))
    before = broker.open_orders()
    clock.set(DAY2 + "T09:30:00")
    _, ca_report, _ = lane.start_session(
        DAY2, ca_calendar=[{"symbol": "ZZZ", "ex_date": DAY2,
                            "action_type": "split", "ratio": 3.0}])
    assert ca_report["handled"] == ["ZZZ|2026-08-04|split"]
    assert ca_report["cancelled"] == [] and ca_report["rebuilt"] == []
    assert broker.open_orders() == before
    handled = [e for e in lane.journal.replay() if e["kind"] == "ca_handled"]
    assert handled[0]["action"] == "none"


def test_non_split_actions_are_informational_only(lane, broker, clock):
    lane.start_session(DAY1)
    lane.submit(entry("AAA", 10, 120.0))
    before = broker.open_orders()
    dividend = {"symbol": "AAA", "ex_date": DAY2, "action_type": "dividend",
                "ratio": None}
    clock.set(DAY2 + "T09:30:00")
    _, ca_report, _ = lane.start_session(DAY2, ca_calendar=[dividend])
    assert ca_report["handled"] == []
    assert ca_report["cancelled"] == [] and ca_report["rebuilt"] == []
    assert ca_report["informational"][0]["action_type"] == "dividend"
    assert broker.open_orders() == before          # brackets untouched
