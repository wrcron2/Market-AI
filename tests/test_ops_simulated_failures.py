"""Ops layer (plan v2.4 §6) — the section's written acceptance test: each
service has one simulated-failure test and produces the specified alert.

The simulated failures, per §6's acceptance paragraph:
  - expired W-8BEN          -> CRITICAL halt-on-sell recommendation (24% on
                               gross proceeds), email + dashboard, unsnoozable
  - divergence-window dates -> DST scheduler still arms/disarms at the right
                               instants (both the March and the Oct/Nov
                               windows), and a tz-database change fails loud
  - split announcement      -> CA daily job alerts on every open order near
                               the ex-date
  - tax-export gaps         -> unmatched sell / missing BoI FX rate marks the
                               export INCOMPLETE and alerts (the §6.4
                               "completeness check vs journal" alert)

(The fourth example in the plan, the cost-model breach, belongs to the §6.5
TCA dashboard — a different node, not built here.)

Run from the repo root:  python -m pytest tests/test_ops_simulated_failures.py -v
"""

from datetime import date

import pytest

import ops.dst_scheduler as dst_mod
from ops.alerts import AlertSink
from ops.ca_daily_job import CaDailyJob, StaticCalendarProvider
from ops.dst_scheduler import DstScheduler, TimezoneDatabaseError
from ops.tax_exports import (RealizedGainsLedger, StaticFxProvider,
                             fills_from_dicts, h1)
from ops.w8ben_monitor import W8BenMonitor

SIGNED = date(2023, 6, 15)          # expiry is calendar-anchored: 2026-12-31
EXPIRY = date(2026, 12, 31)


# --- §6.3 W-8BEN monitor ----------------------------------------------------

def test_expired_w8ben_escalates_to_halt_on_sell():
    sink = AlertSink()
    mon = W8BenMonitor(signed=SIGNED, sink=sink)
    assert mon.expiry == EXPIRY       # Dec 31 of signature year + 3

    alerts = mon.check(date(2027, 1, 10))          # 10 days past expiry
    assert len(alerts) == 1
    alert = alerts[0]
    assert alert.service == "w8ben_monitor" and alert.severity == "CRITICAL"
    assert set(alert.channels) == {"email", "dashboard"}
    assert alert.details["halt_on_sell"] is True
    assert "24%" in alert.message and "gross proceeds" in alert.message
    assert sink.has("w8ben_monitor", "CRITICAL")

    # an expired form cannot be snoozed into silence
    with pytest.raises(ValueError):
        mon.snooze(until=date(2027, 2, 1), on=date(2027, 1, 10))


def test_w8ben_t90_then_t30_alerts_and_snooze_suppression():
    sink = AlertSink()
    mon = W8BenMonitor(signed=SIGNED, sink=sink)

    a = mon.check(date(2026, 11, 20))              # 41 days out
    assert a[0].severity == "WARNING" and a[0].details["state"] == "T-90"

    mon.snooze(until=date(2026, 12, 1), on=date(2026, 11, 20))
    assert mon.check(date(2026, 11, 25)) == []     # suppressed by the snooze

    a = mon.check(date(2026, 12, 20))              # 11 days out, snooze over
    assert a[0].severity == "URGENT" and a[0].details["state"] == "T-30"

    st = mon.status(date(2026, 9, 1))              # > 90 days out: quiet
    assert st.state == "OK" and mon.check(date(2026, 9, 1)) == []


def test_w8ben_snooze_cannot_extend_past_t7():
    mon = W8BenMonitor(signed=SIGNED, sink=AlertSink())
    floor = date(2026, 12, 24)                     # expiry - 7 days
    mon.snooze(until=floor, on=date(2026, 12, 1))  # exactly T-7 is allowed
    with pytest.raises(ValueError):
        mon.snooze(until=date(2026, 12, 25), on=date(2026, 12, 1))


# --- §6.1 DST-safe scheduler --------------------------------------------------

def test_dst_march_divergence_window_arm_disarm():
    """US springs forward 2026-03-08, Israel not until 2026-03-27: inside the
    window 09:30 NY is 15:30 IL (6h offset), not the usual 16:30 (7h). A
    hardcoded '16:30 IL' clock would arm an hour late for three weeks."""
    sch = DstScheduler()
    assert (sch.session_window(date(2026, 3, 6)).il_open.hour
            == 16)                                     # before the window: 7h
    w = sch.session_window(date(2026, 3, 10))          # inside the window
    assert (w.il_open.hour, w.il_open.minute) == (15, 30)
    assert (w.utc_open.hour, w.utc_open.minute) == (13, 30)   # NY on EDT
    assert (w.il_close.hour, w.il_close.minute) == (22, 0)
    w = sch.session_window(date(2026, 3, 30))          # after: both on DST
    assert (w.il_open.hour, w.il_open.minute) == (16, 30)
    assert (w.utc_open.hour, w.utc_open.minute) == (13, 30)


def test_dst_october_divergence_window_arm_disarm():
    """Israel falls back 2026-10-25, the US not until 2026-11-01: inside the
    window 09:30 NY is again 15:30 IL, then 16:30 once both are on standard
    time."""
    sch = DstScheduler()
    w = sch.session_window(date(2026, 10, 23))         # before: both on DST
    assert (w.il_open.hour, w.il_open.minute) == (16, 30)
    w = sch.session_window(date(2026, 10, 27))         # inside the window
    assert (w.il_open.hour, w.il_open.minute) == (15, 30)
    assert (w.utc_open.hour, w.utc_open.minute) == (13, 30)
    w = sch.session_window(date(2026, 11, 3))          # after: both standard
    assert (w.il_open.hour, w.il_open.minute) == (16, 30)
    assert (w.utc_open.hour, w.utc_open.minute) == (14, 30)   # NY on EST

    # the two windows are derived from the tz database, not hardcoded
    assert sch.divergence_windows(2026) == [
        (date(2026, 3, 8), date(2026, 3, 26)),
        (date(2026, 10, 25), date(2026, 10, 31)),
    ]


def test_tz_database_update_fails_loud(monkeypatch):
    """§6.1 alerting: if a tzdata update changes the US DST rule, the
    scheduler must raise, not silently shift every arm/disarm time."""
    sch = DstScheduler()
    assert sch.verify_tz_database(2026) is True        # real tz db is sane

    # simulated failure: DST abolished (no transitions) ...
    monkeypatch.setattr(dst_mod, "_transition_days", lambda tz, year: [])
    with pytest.raises(TimezoneDatabaseError):
        sch.verify_tz_database(2026)

    # ... or the rule moved to different months
    monkeypatch.setattr(dst_mod, "_transition_days",
                        lambda tz, year: [date(2026, 4, 5), date(2026, 10, 25)])
    with pytest.raises(TimezoneDatabaseError):
        sch.verify_tz_database(2026)


# --- §6.2 corporate-actions daily job -----------------------------------------

def test_split_announcement_alerts_on_unmatched_open_order():
    sink = AlertSink()
    calendar = StaticCalendarProvider([
        {"symbol": "AAA", "ex_date": "2026-08-05", "action_type": "split",
         "ratio": 2.0},
        {"symbol": "BBB", "ex_date": "2026-08-06", "action_type": "dividend",
         "ratio": None},
        # not held or watched — the provider contract filters it out
        {"symbol": "QQQ", "ex_date": "2026-08-05", "action_type": "split",
         "ratio": 3.0},
        # outside the lookahead window — not "near" an ex-date
        {"symbol": "AAA", "ex_date": "2026-08-10", "action_type": "dividend",
         "ratio": None},
    ])
    job = CaDailyJob(calendar, sink, lookahead_days=2)
    report = job.run(
        date(2026, 8, 4),
        positions=[{"symbol": "AAA", "qty": 20}],
        watchlist=["BBB"],
        open_orders=[{"order_id": "o-tp", "symbol": "AAA"},
                     {"order_id": "o-sl", "symbol": "AAA"}])

    assert report["events"] == 2                       # QQQ and Aug-10 filtered
    unmatched = {u["order_id"]: u for u in report["unmatched"]}
    assert set(unmatched) == {"o-tp", "o-sl"}
    assert unmatched["o-tp"]["action_type"] == "split"
    assert "cancel and rebuild" in unmatched["o-tp"]["recommendation"]
    assert [i["symbol"] for i in report["informational"]] == ["BBB"]

    alerts = sink.for_service("ca_daily_job")
    assert len(alerts) == 2 and all(a.severity == "URGENT" for a in alerts)
    assert all(set(a.channels) == {"email", "dashboard"} for a in alerts)
    assert all("ex-date" in a.message for a in alerts)


# --- §6.4 Israeli tax exports ---------------------------------------------------

def test_tax_export_incomplete_on_ledger_gap_and_missing_fx():
    """Completeness check (§6.4 alerting): a sell with no open buy lots and a
    sale date with no BoI rate must mark the export incomplete and alert —
    a partial tax export that looks complete is worse than none."""
    fills = fills_from_dicts([
        {"fill_id": "b1", "symbol": "AAA", "side": "buy", "qty": 10,
         "price": 100.0, "commission": 0.40, "traded_on": "2026-01-05"},
        {"fill_id": "b2", "symbol": "CCC", "side": "buy", "qty": 3,
         "price": 75.0, "commission": 0.40, "traded_on": "2026-02-10"},
        # matched, but the FX provider below has no rate for this date
        {"fill_id": "s1", "symbol": "AAA", "side": "sell", "qty": 10,
         "price": 130.0, "commission": 0.40, "traded_on": "2026-03-10"},
        # only 3 shares of CCC exist: 2 of the 5 sold are unmatched
        {"fill_id": "s2", "symbol": "CCC", "side": "sell", "qty": 5,
         "price": 80.0, "commission": 0.40, "traded_on": "2026-04-01"},
    ])
    sink = AlertSink()
    ledger = RealizedGainsLedger(fills)
    export = ledger.export(*h1(2026), fx=StaticFxProvider({"2026-04-01": 3.60}),
                           sink=sink)

    assert export.complete is False
    reasons = {g["reason"].split(" (")[0] for g in export.gaps}
    assert reasons == {"missing Bank of Israel FX rate for the trade date",
                       "no open buy lots"}
    gap = next(g for g in export.gaps if "no open buy lots" in g["reason"])
    assert gap["symbol"] == "CCC" and gap["qty_unmatched"] == 2

    # matched portions still export: USD always, ILS only where a rate exists
    by_symbol = {r["symbol"]: r for r in export.rows}
    assert by_symbol["AAA"]["gain_usd"] == 299.20
    assert by_symbol["AAA"]["gain_ils"] is None
    assert by_symbol["CCC"]["gain_usd"] == 14.36
    assert by_symbol["CCC"]["gain_ils"] == 51.70      # 14.36 x 3.60
    assert export.totals["gain_usd"] == 313.56
    assert export.totals["gain_ils"] == 51.70
    assert export.totals["cgt_usd"] == 78.39          # 25% of 313.56

    alerts = sink.for_service("tax_exports")
    assert len(alerts) == 1 and alerts[0].severity == "URGENT"
    assert "INCOMPLETE" in alerts[0].message
    assert alerts[0].details["sells_in_window"] == 2
