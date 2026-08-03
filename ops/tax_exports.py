"""Israeli tax exports (plan v2.4 §6.4, doc edit §2.3).

Realized-gains ledger, per-lot FIFO, USD throughout, with ILS conversion at
the Bank of Israel rate on the trade date. Reporting obligations per the
v2.4 correction: SEMI-ANNUAL exports (Jan–Jun and Jul–Dec windows) plus the
annual Form 1301 pack — the telemetry must exist before the first live
trade, not as a retrofit.

Standalone by design (G0): fills arrive as plain dicts (fills_from_dicts /
fills_from_csv), not via the execution lane's §5.2 intent journal. When the
execution lane exists, a thin adapter OUTSIDE this package maps journal fill
entries to the same dict shape; this module never imports it.

Known simplifications, tied to open questions in the plan (§10) so they are
revisited rather than fossilized:
  - ILS conversion rule: gain_usd x BoI rate on the SALE date (Q1 — the
    accountant must confirm the FX-conversion rule before the first live
    year; the rate provider is injected so the rule can change here alone).
  - CGT estimated at 25% (plan §2.3); surtax flags (Q5: 2% capital-source /
    3% high-income) are NOT computed — flagged for the first annual review.
  - short sales are unsupported (cash-only system): a sell with no open buy
    lots is a completeness gap, not a position.

Money is Decimal internally (tax numbers must round deterministically);
rows expose floats rounded HALF_UP to cents, and totals are sums of the
rounded rows — the ledger convention that keeps CSV exports self-consistent.

Completeness check (§6.4 alerting): every export reconciles the sells in the
window against the lots they consumed and the FX rates they needed; any gap
(unmatched sell, missing BoI rate) marks the export incomplete and raises an
URGENT alert — a partial tax export that looks complete is worse than none.
"""

from __future__ import annotations

import csv
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from ops.alerts import EMAIL_DASHBOARD, Alert, AlertSink

CGT_RATE = Decimal("0.25")         # Israeli capital-gains rate, plan §2.3
CENT = Decimal("0.01")

FILL_KEYS = ("fill_id", "symbol", "side", "qty", "price", "commission",
             "traded_on")


def _d(x) -> Decimal:
    return Decimal(str(x))


def _money(x: Decimal) -> float:
    return float(x.quantize(CENT, rounding=ROUND_HALF_UP))


def _as_date(value) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


@dataclass(frozen=True)
class Fill:
    fill_id: str
    symbol: str
    side: str                      # "buy" | "sell"
    qty: int
    price: float
    commission: float
    traded_on: date


def fills_from_dicts(rows):
    """Plain-dict ingestion — the standalone boundary. Extra keys are
    ignored, so an execution-lane journal entry maps with one projection."""
    fills = []
    for i, r in enumerate(rows):
        fills.append(Fill(
            fill_id=str(r.get("fill_id", f"fill-{i + 1}")),
            symbol=r["symbol"], side=r["side"],
            qty=int(r["qty"]), price=float(r["price"]),
            commission=float(r.get("commission", 0.0)),
            traded_on=_as_date(r["traded_on"])))
    return fills


def fills_from_csv(path):
    """Fills from a CSV with (a superset of) FILL_KEYS columns."""
    with open(path, newline="") as fh:
        return fills_from_dicts(list(csv.DictReader(fh)))


class StaticFxProvider:
    """BoI USD/ILS rates as {'YYYY-MM-DD': rate}. Any callable with the same
    signature works (e.g. a cached BoI client) — injected per the Q1 rule."""

    def __init__(self, rates):
        self.rates = dict(rates)

    def __call__(self, day) -> float:
        return self.rates.get(_as_date(day).isoformat())


def h1(year: int):
    """Semi-annual window 1: Jan–Jun (plan §2.3)."""
    return date(year, 1, 1), date(year, 6, 30)


def h2(year: int):
    """Semi-annual window 2: Jul–Dec (plan §2.3)."""
    return date(year, 7, 1), date(year, 12, 31)


def annual(year: int):
    return date(year, 1, 1), date(year, 12, 31)


@dataclass
class TaxExport:
    start: date
    end: date
    rows: list                     # per-lot realized-gain dicts (rounded)
    totals: dict                   # sums of the rounded rows + CGT estimate
    complete: bool
    gaps: list = field(default_factory=list)

    COLUMNS = ("symbol", "acquired_on", "sold_on", "qty", "cost_usd",
               "proceeds_usd", "gain_usd", "fx_rate", "gain_ils")

    def write_csv(self, path):
        """The artifact the accountant reviews (format sign-off is Q1's
        gate, not this module's construction)."""
        with open(path, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(self.COLUMNS))
            w.writeheader()
            for row in self.rows:
                w.writerow({k: row.get(k) for k in self.COLUMNS})
            w.writerow({k: (self.totals.get(k) if k != "symbol" else "TOTAL")
                        for k in self.COLUMNS})


class RealizedGainsLedger:
    """Per-lot FIFO ledger over an iterable of Fill (any iterable — list,
    generator, CSV rows — nothing journal-shaped is required)."""

    def __init__(self, fills):
        lots = defaultdict(deque)    # symbol -> open buy lots (FIFO)
        self._realized = []          # dicts with exact Decimal amounts
        self._sells = []             # every sell Fill seen (completeness)
        self._gaps = []              # unmatched sell portions
        for f in sorted(fills, key=lambda f: (f.traded_on, f.fill_id)):
            if f.side == "buy":
                lots[f.symbol].append({"qty": Decimal(f.qty),
                                       "price": _d(f.price),
                                       "comm": _d(f.commission),
                                       "acquired_on": f.traded_on})
            elif f.side == "sell":
                self._sells.append(f)
                self._consume(f, lots[f.symbol])
            else:
                raise ValueError(f"unknown fill side {f.side!r}")

    def _consume(self, sell: Fill, book):
        remaining = Decimal(sell.qty)
        sell_qty = Decimal(sell.qty)
        sell_comm = _d(sell.commission)
        while remaining > 0 and book:
            lot = book[0]
            take = min(remaining, lot["qty"])
            # commissions allocate pro-rata so partial lot consumption stays exact
            cost = take * lot["price"] + lot["comm"] * (take / lot["qty"])
            proceeds = take * _d(sell.price) - sell_comm * (take / sell_qty)
            lot["comm"] -= lot["comm"] * (take / lot["qty"])
            lot["qty"] -= take
            remaining -= take
            self._realized.append({
                "symbol": sell.symbol, "acquired_on": lot["acquired_on"],
                "sold_on": sell.traded_on, "qty": int(take),
                "_cost": cost, "_proceeds": proceeds})
            if lot["qty"] == 0:
                book.popleft()
        if remaining > 0:
            self._gaps.append({
                "symbol": sell.symbol, "sold_on": sell.traded_on,
                "fill_id": sell.fill_id, "qty_unmatched": int(remaining),
                "reason": "no open buy lots (missing buy history or short sale)"})

    def export(self, start: date, end: date, fx=None, sink: AlertSink = None
               ) -> TaxExport:
        """Realized gains with sale date in [start, end], ILS at the BoI rate
        on each sale date. Incomplete if any sell in the window went unmatched
        or any sale date lacks an FX rate; incompleteness alerts URGENT."""
        rows, gaps = [], []
        for r in self._realized:
            if not (start <= r["sold_on"] <= end):
                continue
            gain = r["_proceeds"] - r["_cost"]
            rate = fx(r["sold_on"]) if fx else None
            row = {"symbol": r["symbol"],
                   "acquired_on": r["acquired_on"].isoformat(),
                   "sold_on": r["sold_on"].isoformat(), "qty": r["qty"],
                   "cost_usd": _money(r["_cost"]),
                   "proceeds_usd": _money(r["_proceeds"]),
                   "gain_usd": _money(gain),
                   "fx_rate": rate,
                   "gain_ils": (_money(gain * _d(rate)) if rate is not None
                                else None)}
            if rate is None:
                gaps.append({"symbol": r["symbol"],
                             "sold_on": r["sold_on"].isoformat(),
                             "reason": "missing Bank of Israel FX rate for the "
                                       "trade date"})
            rows.append(row)
        gaps.extend({**g, "sold_on": g["sold_on"].isoformat()}
                    for g in self._gaps if start <= g["sold_on"] <= end)

        totals = {
            "proceeds_usd": _money(sum((_d(r["proceeds_usd"]) for r in rows),
                                       Decimal(0))),
            "cost_usd": _money(sum((_d(r["cost_usd"]) for r in rows),
                                   Decimal(0))),
            "gain_usd": _money(sum((_d(r["gain_usd"]) for r in rows),
                                   Decimal(0))),
            "gain_ils": _money(sum((_d(r["gain_ils"]) for r in rows
                                    if r["gain_ils"] is not None), Decimal(0))),
        }
        totals["cgt_usd"] = (_money(_d(totals["gain_usd"]) * CGT_RATE)
                             if totals["gain_usd"] > 0 else 0.0)

        sells_in_window = sum(1 for f in self._sells
                              if start <= f.traded_on <= end)
        complete = not gaps
        export = TaxExport(start=start, end=end, rows=rows, totals=totals,
                           complete=complete, gaps=gaps)
        if not complete and sink is not None:
            sink.emit(Alert(
                service="tax_exports", severity="URGENT",
                message=(f"tax export {start.isoformat()}..{end.isoformat()} "
                         f"is INCOMPLETE: {len(gaps)} gap(s) vs "
                         f"{sells_in_window} sell(s) in window — reconcile "
                         f"before filing"),
                channels=EMAIL_DASHBOARD, raised_on=end.isoformat(),
                details={"start": start.isoformat(), "end": end.isoformat(),
                         "sells_in_window": sells_in_window,
                         "gaps": [dict(g) for g in gaps]}))
        return export

    def export_annual_pack(self, year: int, fx=None, sink: AlertSink = None):
        """The Form 1301 pack: both semi-annual windows + the annual rollup."""
        e1 = self.export(*h1(year), fx=fx)
        e2 = self.export(*h2(year), fx=fx)
        eall = self.export(*annual(year), fx=fx, sink=sink)
        return {"year": year, "h1": e1, "h2": e2, "annual": eall,
                "complete": e1.complete and e2.complete and eall.complete}
