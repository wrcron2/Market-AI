"""Corporate-actions daily job (plan v2.4 §6.2).

Once a day, for every held + watchlist name, look ahead at the corporate-
actions calendar and reconcile the resting open orders against it. A split
re-prices the position overnight but no venue rewrites resting orders, so an
open order near an ex-date is an orphaned bracket in waiting — the §9 kill
criteria halt the sleeve on exactly that. This job is the early-warning
layer: it detects and alerts; the actual cancel/rebuild around ex-dates is
the execution lane's §5.5 guard, which is a different component with broker
access.

Standalone by design (G0): the calendar is a provider CALLABLE with the
signature (symbols, start_date, end_date) -> iterable of rows in the §3 data
layer's corporate_actions shape (symbol, ex_date, action_type, ratio).
CsvCalendarProvider reads the committed §3 CSV by file contract — a file
path, not a module import — so this package keeps zero dependencies on the
rest of the repo.

Alerting (§6.2): one URGENT alert per unmatched open order near an ex-date.
"""

from __future__ import annotations

import csv
from datetime import date, timedelta

from ops.alerts import EMAIL_DASHBOARD, Alert, AlertSink

DEFAULT_LOOKAHEAD_DAYS = 2

# file contract of data_layer/source/corporate_actions.csv (plan §3 schema)
CSV_COLUMNS = ("symbol", "ex_date", "action_type", "ratio", "raw_terms",
               "source", "retrieved")


def _as_date(value) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


class StaticCalendarProvider:
    """Calendar from an in-memory list of §3-shaped rows (tests, fixtures)."""

    def __init__(self, rows):
        self._rows = list(rows)

    def __call__(self, symbols, start: date, end: date):
        return [r for r in self._rows
                if r["symbol"] in symbols
                and start <= _as_date(r["ex_date"]) <= end]


class CsvCalendarProvider:
    """Calendar from a corporate_actions CSV in the §3 schema (file contract,
    not a data_layer import — keeps ops/ standalone)."""

    def __init__(self, path):
        self._path = path

    def __call__(self, symbols, start: date, end: date):
        with open(self._path, newline="") as fh:
            rows = list(csv.DictReader(fh))
        return [r for r in rows
                if r["symbol"] in symbols
                and start <= _as_date(r["ex_date"]) <= end]


class CaDailyJob:
    def __init__(self, calendar_provider, sink: AlertSink,
                 lookahead_days: int = DEFAULT_LOOKAHEAD_DAYS):
        self.calendar = calendar_provider
        self.sink = sink
        self.lookahead_days = lookahead_days

    def run(self, on: date, positions, watchlist, open_orders) -> dict:
        """positions: [{'symbol', 'qty', ...}]; watchlist: [symbol];
        open_orders: [{'order_id', 'symbol', ...}] (plain dicts — broker
        state is fetched by the caller; this job never touches a broker)."""
        symbols = {p["symbol"] for p in positions} | set(watchlist)
        end = on + timedelta(days=self.lookahead_days)
        events = self.calendar(symbols, on, end)

        report = {"date": on.isoformat(),
                  "window": [on.isoformat(), end.isoformat()],
                  "events": len(events), "unmatched": [], "informational": []}

        for ev in sorted(events, key=lambda e: (_as_date(e["ex_date"]), e["symbol"])):
            ex = _as_date(ev["ex_date"])
            orders = [o for o in open_orders if o["symbol"] == ev["symbol"]]
            if not orders:
                report["informational"].append(
                    {"symbol": ev["symbol"], "ex_date": ex.isoformat(),
                     "action_type": ev["action_type"],
                     "note": "no open orders on the name; nothing to reconcile"})
                continue
            for order in orders:
                if ev["action_type"] == "split":
                    recommendation = ("cancel and rebuild the bracket around "
                                      "the ex-date per §5.5 — pre-split terms "
                                      "are stale the moment the ex-date opens")
                else:
                    recommendation = ("operator review: non-split action does "
                                      "not re-term orders, but confirm intent")
                entry = {"order_id": order["order_id"], "symbol": ev["symbol"],
                         "ex_date": ex.isoformat(),
                         "action_type": ev["action_type"],
                         "recommendation": recommendation}
                report["unmatched"].append(entry)
                self.sink.emit(Alert(
                    service="ca_daily_job", severity="URGENT",
                    message=(f"open order {order['order_id']} on {ev['symbol']} "
                             f"is unmatched near the {ex.isoformat()} ex-date "
                             f"({ev['action_type']}) — {recommendation}"),
                    channels=EMAIL_DASHBOARD, raised_on=on.isoformat(),
                    details=dict(entry)))

        return report
