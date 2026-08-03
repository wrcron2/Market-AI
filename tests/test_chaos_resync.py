"""Chaos test and resync protocol v2 (plan v2.4 §5.1 + §5.6 — the section's
written acceptance tests, and the G2 gate's machine-checkable core).

§5.1: a cold start with live positions must rebuild exact broker state —
the local cache is rebuilt from the broker on every startup and reconnect
and has no authority of its own.

§5.6: kill the VM mid-trade — position open, brackets live, journal
unflushed — and restart. Required outcome, asserted below:
  (a) system state == broker state, exactly;
  (b) zero naked positions (missing/partial bracket legs rebuilt at the
      broker before re-arm);
  (c) the journal gap is explained — broker events the journal never
      recorded are re-journaled as recovered, journaled intents that never
      reached the broker are named as lost, and a single gap_report entry
      ties it together.

The "crash" is real about the thing being tested: the journal's write-behind
buffer dies with the abandoned lane object, while the SimulatedBroker's state
file (the "other machine") persists. Run from the repo root:

    python -m pytest tests/test_chaos_resync.py -v
"""

from execution_lane.lane import ExecutionLane

from conftest import entry, exit_intent

DAY = "2026-08-03"


def journaled_order_ids(entries):
    ids = set()
    for e in entries:
        if e["kind"] == "ack":
            ids.add(e["order"]["order_id"])
            ids.update(c["order_id"] for c in e.get("children", []))
        elif e["kind"] == "protective_rebuild":
            ids.add(e["take_profit"]["order_id"])
            ids.add(e["stop_loss"]["order_id"])
        elif e["kind"] in ("recovered_order", "cancel"):
            ids.add(e["order_id"])
    return ids


def assert_cache_is_broker_truth(lane):
    assert lane.cache.positions == lane.broker.positions()
    assert lane.cache.open_orders == lane.broker.open_orders()
    assert lane.cache.executions == lane.broker.executions()


# --- §5.1: cold start with live positions rebuilds exact broker state --------

def seed_broker_with_live_state(broker):
    broker.connect()
    filled = broker.place_bracket("AAA", "buy", 10, 100.0, 108.0, 96.0,
                                  intent_id="PRE-1", sleeve="m1")
    working = broker.place_bracket("BBB", "buy", 5, 200.0, 216.0, 192.0,
                                   intent_id="PRE-2", sleeve="m2", fill=False)
    return filled, working


def test_cold_start_rebuilds_exact_broker_state(lane, broker):
    seed_broker_with_live_state(broker)
    # a brand-new lane knows nothing: empty cache, empty journal, disarmed
    assert not lane.armed
    assert lane.submit(entry()).reasons == ("disarmed",)

    report = lane.resync(DAY)

    assert_cache_is_broker_truth(lane)
    assert report.positions == 1          # AAA; BBB's parent is still working
    assert report.open_orders == 5        # AAA tp/sl + BBB parent + BBB tp/sl
    assert report.executions == 1
    assert lane.armed
    assert lane.verify_coverage().ok
    assert lane.find_orphaned_orders() == []

    # the pre-journal broker state is explained, not silently adopted: the
    # fill and every resting order predate the journal, so all are recovered
    replay = lane.journal.replay()
    gap_reports = [e for e in replay if e["kind"] == "gap_report"]
    assert len(gap_reports) == 1
    assert report.recovered_fills == [broker.executions()[0]["exec_id"]]
    assert set(report.recovered_orders) == {o["order_id"] for o in broker.open_orders()}
    assert report.lost_intents == []


def test_reconnect_resync_is_idempotent(lane, broker):
    seed_broker_with_live_state(broker)
    lane.resync(DAY)
    first = lane.resync(DAY)   # same-day reconnect: nothing left to explain
    assert first.recovered_fills == [] and first.recovered_orders == []
    assert first.lost_intents == []
    gap_reports = [e for e in lane.journal.replay() if e["kind"] == "gap_report"]
    assert len(gap_reports) == 1        # only the startup resync produced one
    assert_cache_is_broker_truth(lane)
    assert lane.armed


# --- §5.6: kill the VM mid-trade ----------------------------------------------

def test_chaos_kill_mid_trade_restart(lane, broker, journal_path, clamps,
                                      bracket_spec, clock):
    lane.resync(DAY)
    assert lane.submit(entry("AAA", 10, 100.0)).allowed      # TP 108 / SL 96
    assert lane.submit(entry("BBB", 20, 50.0)).allowed       # TP 54 / SL 48

    # (i) a command journaled + flushed, then the crash BEFORE placement:
    # the broker never saw it — this becomes the lost intent
    lane.journal.append(
        "command", intent_id="I-LOST",
        intent={"symbol": "DDD", "side": "buy", "qty": 5, "limit_price": 40.0,
                "sleeve": "m1", "kind": "entry", "reason": ""},
        envelope={})
    lane.journal.flush()

    # (ii) the mid-trade crash window: command/ack/fill for CCC all happen
    # (the broker fills it) but the journal is never flushed — the write-
    # behind buffer is exactly what the §5.6 scenario says is unflushed
    iid = lane._next_intent_id()
    lane.journal.append(
        "command", intent_id=iid,
        intent={"symbol": "CCC", "side": "buy", "qty": 8, "limit_price": 25.0,
                "sleeve": "m1", "kind": "entry", "reason": ""},
        envelope={})
    ccc_parent = broker.place_bracket("CCC", "buy", 8, 25.0, 27.0, 24.0,
                                      intent_id=iid, sleeve="m1")
    ccc_children = [o for o in broker.open_orders()
                    if o["parent_id"] == ccc_parent["order_id"]]
    lane.journal.append("ack", intent_id=iid, order=ccc_parent,
                        children=ccc_children)
    ccc_exec = [e for e in broker.executions()
                if e["order_id"] == ccc_parent["order_id"]][0]
    lane.journal.append("fill", **ccc_exec)

    # (iii) the desync bug class the review named: BBB's stop-loss vanishes
    # at the broker while the local journal still believes it is live
    bbb_sl = [o for o in broker.open_orders()
              if o["symbol"] == "BBB" and o["role"] == "stop_loss"][0]
    broker.cancel_order(bbb_sl["order_id"])

    # KILL THE VM: the lane and its unflushed journal buffer die here; the
    # broker's state file (the other machine) persists
    del lane
    lane2 = ExecutionLane(broker, journal_path, clamps, bracket_spec, clock)
    assert not lane2.armed
    assert lane2.submit(entry("ZZZ", 1, 30.0)).reasons == ("disarmed",)

    report = lane2.resync(DAY)

    # (a) system state == broker state, exactly
    assert_cache_is_broker_truth(lane2)

    # (b) zero naked positions: BBB's vanished stop was detected and the
    # whole bracket rebuilt from broker truth before re-arm
    assert report.naked_before == ["BBB"]
    assert report.rebuilt_protection == ["BBB"]
    assert lane2.verify_coverage().ok
    assert lane2.find_orphaned_orders() == []
    bbb_legs = [o for o in broker.open_orders() if o["symbol"] == "BBB"]
    assert {o["role"] for o in bbb_legs} == {"take_profit", "stop_loss"}
    assert all(o["qty"] == 20 for o in bbb_legs)
    assert lane2.armed

    # (c) the journal gap is explained
    assert report.recovered_fills == [ccc_exec["exec_id"]]
    assert set(report.recovered_orders) == {o["order_id"] for o in ccc_children}
    assert report.lost_intents == ["I-LOST"]
    replay = lane2.journal.replay()
    gap_reports = [e for e in replay if e["kind"] == "gap_report"]
    assert len(gap_reports) == 1
    gap = gap_reports[0]
    assert gap["recovered_fills"] == [ccc_exec["exec_id"]]
    assert gap["lost_intents"] == ["I-LOST"]
    lost = [e for e in replay if e["kind"] == "lost_intent"]
    assert len(lost) == 1 and lost[0]["intent_id"] == "I-LOST"
    assert lost[0]["symbol"] == "DDD"

    # replay completeness (§5.2: the journal replays any session for
    # forensics and TCA): after resync, every broker execution and every
    # resting broker order appears in the durable journal
    journaled_execs = {e["exec_id"] for e in replay
                       if e["kind"] in ("fill", "recovered_fill")}
    assert journaled_execs == {e["exec_id"] for e in broker.executions()}
    assert {o["order_id"] for o in broker.open_orders()} <= journaled_order_ids(replay)

    # and the lane is usable again, against broker truth
    assert lane2.submit(entry("EEE", 4, 30.0)).allowed


def test_naked_position_is_protected_before_rearm(lane, broker):
    # position at the broker with NO bracket legs at all (e.g. protection
    # vanished in a desync) — resync must re-protect before arming
    broker.connect()
    parent = broker.place_bracket("AAA", "buy", 10, 100.0, 108.0, 96.0,
                                  intent_id="PRE-1", sleeve="m1")
    for o in broker.open_orders():
        if o["parent_id"] == parent["order_id"]:
            broker.cancel_order(o["order_id"])

    report = lane.resync(DAY)

    assert report.naked_before == ["AAA"]
    assert report.rebuilt_protection == ["AAA"]
    legs = [o for o in broker.open_orders() if o["symbol"] == "AAA"]
    assert {o["role"] for o in legs} == {"take_profit", "stop_loss"}
    assert all(o["qty"] == 10 for o in legs)
    prices = {o["role"]: o["limit_price"] for o in legs}
    assert prices["take_profit"] == 108.0 and prices["stop_loss"] == 96.0
    assert lane.armed
    assert lane.submit(entry("EEE", 1, 50.0)).allowed


def test_exits_never_leave_orphaned_brackets(lane, broker):
    lane.resync(DAY)
    lane.submit(entry("AAA", 10, 100.0))

    # partial exit: protection is re-sized to the remaining position
    assert lane.submit(exit_intent("AAA", 4, 105.0)).allowed
    legs = [o for o in broker.open_orders() if o["symbol"] == "AAA"]
    assert {o["role"] for o in legs} == {"take_profit", "stop_loss"}
    assert all(o["qty"] == 6 for o in legs)
    assert lane.find_orphaned_orders() == []

    # full exit: the now-positionless brackets are cancelled, not left resting
    assert lane.submit(exit_intent("AAA", 6, 106.0)).allowed
    assert broker.positions() == []
    assert [o for o in broker.open_orders() if o["symbol"] == "AAA"] == []
    assert lane.find_orphaned_orders() == []
    cancels = [e for e in lane.journal.replay()
               if e["kind"] == "cancel" and e.get("reason") == "position_closed"]
    assert len(cancels) == 2
    assert lane.verify_coverage().ok
