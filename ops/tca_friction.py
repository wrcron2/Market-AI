"""Friction dashboard / TCA (plan v2.4 §6.5; same condition is a §9
live-pilot kill criterion).

Per-trade realized cost vs the §4 cost model — commission, spread capture,
slippage — as rolling bps by sleeve. The alert, verbatim from the plan:
"Alert if realized friction exceeds model by >2 bps over any 20-trade
window." At G4 that same breach halts the sleeve (§9 kill criteria), so
the alert is CRITICAL on email + dashboard, never a log line.

Standalone by design (G0): this module never imports the fortress or the
execution lane. The model side of the comparison arrives as plain data —
an adapter OUTSIDE ops/ prices each fill with the shared
fortress/cost_model.py and passes model_commission / model_spread_cost /
model_slippage next to the realized components. §4.1's rule — if live TCA
ever measures friction differently from the backtest, the comparison is
meaningless — is honored by construction: the single-source-of-truth
module does the model math; this module only compares dollars to dollars.

Conventions:
  - every component is USD per fill, one side; realized components may be
    negative (a fill inside the spread has negative spread cost — price
    improvement is real and must show up as improvement, not be clamped);
  - bps are per trade: cost / notional x 1e4; windows and summaries
    average per-trade bps with equal weight per trade (documented choice,
    not notional-weighted);
  - a window needs the full 20 trades: the first 19 paper trades fill the
    dashboard but can never raise the alert (§6's acceptance: "the first
    20 paper trades land inside the model's friction bands").
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date

from ops.alerts import EMAIL_DASHBOARD, Alert, AlertSink

WINDOW_TRADES = 20         # plan §6.5 / §9: "any 20-trade window"
TOLERANCE_BPS = 2.0        # alert when realized - model > 2 bps over a window

REALIZED_KEYS = ("commission", "spread_cost", "slippage")
MODEL_KEYS = ("model_commission", "model_spread_cost", "model_slippage")

TRADE_KEYS = ("fill_id", "sleeve", "symbol", "side", "qty", "price",
              "traded_on") + REALIZED_KEYS + MODEL_KEYS


def _as_date(value) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


@dataclass(frozen=True)
class TradeFriction:
    """One fill's realized cost components vs the model's, all USD."""

    fill_id: str
    sleeve: str
    symbol: str
    side: str                      # "buy" | "sell"
    qty: int
    price: float
    traded_on: date
    commission: float = 0.0
    spread_cost: float = 0.0
    slippage: float = 0.0
    model_commission: float = 0.0
    model_spread_cost: float = 0.0
    model_slippage: float = 0.0

    @property
    def notional(self) -> float:
        return self.qty * self.price

    @property
    def realized_cost(self) -> float:
        return self.commission + self.spread_cost + self.slippage

    @property
    def model_cost(self) -> float:
        return (self.model_commission + self.model_spread_cost
                + self.model_slippage)

    @property
    def realized_bps(self) -> float:
        return self.realized_cost / self.notional * 1e4

    @property
    def model_bps(self) -> float:
        return self.model_cost / self.notional * 1e4

    @property
    def excess_bps(self) -> float:
        return self.realized_bps - self.model_bps


def trades_from_dicts(rows):
    """Plain-dict ingestion — the standalone boundary. Extra keys are
    ignored, so a §5.2 journal fill entry maps with one projection by an
    adapter outside this package."""
    trades = []
    for i, r in enumerate(rows):
        t = TradeFriction(
            fill_id=str(r.get("fill_id", f"fill-{i + 1}")),
            sleeve=r["sleeve"], symbol=r["symbol"], side=r["side"],
            qty=int(r["qty"]), price=float(r["price"]),
            traded_on=_as_date(r["traded_on"]),
            **{k: float(r.get(k, 0.0)) for k in REALIZED_KEYS + MODEL_KEYS})
        if t.side not in ("buy", "sell"):
            raise ValueError(
                f"{t.fill_id}: side must be buy|sell, got {t.side!r}")
        if t.notional <= 0:
            raise ValueError(
                f"{t.fill_id}: qty x price must be positive, got "
                f"{t.qty} x {t.price}")
        trades.append(t)
    return trades


def trades_from_csv(path):
    """Trades from a CSV with (a superset of) TRADE_KEYS columns."""
    with open(path, newline="") as fh:
        return trades_from_dicts(list(csv.DictReader(fh)))


def _bps(cost: float, notional: float) -> float:
    return cost / notional * 1e4


def _mean(values):
    values = list(values)
    return sum(values) / len(values)


class FrictionMonitor:
    """Rolling realized-vs-model comparison; raises the §6.5 alert.

    One evaluation pass over a set of fills produces the dashboard report
    (per-trade rows, per-sleeve means, every 20-trade window) and emits at
    most one CRITICAL alert per sleeve, citing the worst window.
    """

    def __init__(self, sink: AlertSink, window: int = WINDOW_TRADES,
                 tolerance_bps: float = TOLERANCE_BPS):
        if window < 1:
            raise ValueError("window must be >= 1 trade")
        self.sink = sink
        self.window = window
        self.tolerance_bps = tolerance_bps

    def evaluate(self, trades) -> dict:
        by_sleeve = {}
        for t in trades:
            by_sleeve.setdefault(t.sleeve, []).append(t)
        report = {"window_trades": self.window,
                  "tolerance_bps": self.tolerance_bps,
                  "sleeves": {}, "alerts_raised": 0}
        for sleeve in sorted(by_sleeve):
            ordered = sorted(by_sleeve[sleeve],
                             key=lambda t: (t.traded_on, t.fill_id))
            summary, alerts = self._evaluate_sleeve(sleeve, ordered)
            report["sleeves"][sleeve] = summary
            report["alerts_raised"] += len(alerts)
        return report

    def _evaluate_sleeve(self, sleeve, trades):
        windows = []
        for i in range(0, len(trades) - self.window + 1):
            w = trades[i:i + self.window]
            realized = _mean(t.realized_bps for t in w)
            model = _mean(t.model_bps for t in w)
            windows.append({
                "start_fill": w[0].fill_id, "end_fill": w[-1].fill_id,
                "start_date": w[0].traded_on.isoformat(),
                "end_date": w[-1].traded_on.isoformat(),
                "realized_bps": round(realized, 4),
                "model_bps": round(model, 4),
                "excess_bps": round(realized - model, 4),
                # strict: "exceeds model by >2 bps" — exactly 2.0 is inside
                "breach": realized - model > self.tolerance_bps})
        breaches = [w for w in windows if w["breach"]]
        alerts = []
        if breaches:
            worst = max(breaches, key=lambda w: w["excess_bps"])
            alerts.append(self._alert(sleeve, worst, len(breaches),
                                      len(windows)))
        components = {}
        for name, realized_key, model_key in (
                ("commission", "commission", "model_commission"),
                ("spread", "spread_cost", "model_spread_cost"),
                ("slippage", "slippage", "model_slippage")):
            components[name] = {
                "realized": round(_mean(
                    _bps(getattr(t, realized_key), t.notional)
                    for t in trades), 4),
                "model": round(_mean(
                    _bps(getattr(t, model_key), t.notional)
                    for t in trades), 4)}
        summary = {
            "trades": len(trades),
            "mean_realized_bps": round(_mean(
                t.realized_bps for t in trades), 4),
            "mean_model_bps": round(_mean(t.model_bps for t in trades), 4),
            "components_bps": components,
            "windows_evaluated": len(windows),
            "breaching_windows": len(breaches),
            "windows": windows,
            "trade_rows": [{
                "fill_id": t.fill_id, "traded_on": t.traded_on.isoformat(),
                "symbol": t.symbol, "side": t.side,
                "notional": round(t.notional, 2),
                "realized_bps": round(t.realized_bps, 4),
                "model_bps": round(t.model_bps, 4),
                "excess_bps": round(t.excess_bps, 4)} for t in trades]}
        return summary, alerts

    def _alert(self, sleeve, worst, breaching, evaluated):
        msg = (f"TCA friction breach [{sleeve}]: realized cost exceeded the "
               f"cost model by {worst['excess_bps']:.2f} bps over the "
               f"{self.window}-trade window {worst['start_fill']} -> "
               f"{worst['end_fill']} ({worst['start_date']} -> "
               f"{worst['end_date']}); tolerance {self.tolerance_bps:.1f} "
               f"bps, {breaching} of {evaluated} windows breaching — "
               f"plan §6.5 alert and §9 live-pilot kill criterion")
        alert = Alert(service="tca_friction", severity="CRITICAL",
                      message=msg, channels=EMAIL_DASHBOARD,
                      raised_on=worst["end_date"],
                      details={"sleeve": sleeve,
                               "worst_excess_bps": worst["excess_bps"],
                               "window_trades": self.window,
                               "tolerance_bps": self.tolerance_bps,
                               "window_start_fill": worst["start_fill"],
                               "window_end_fill": worst["end_fill"],
                               "window_start_date": worst["start_date"],
                               "window_end_date": worst["end_date"],
                               "breaching_windows": breaching,
                               "windows_evaluated": evaluated,
                               "kill_criterion": True})
        return self.sink.emit(alert)
