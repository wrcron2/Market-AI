"""Ops layer standalone proof (plan v2.4 §9 gate G0: "park trading build;
keep ops/tax tooling only").

If Item Zero parks the trading build, ops/ is what survives — so the package
must not depend on the execution lane, the fortress, or their §5.2 intent
journal. Three machine checks pin that:

  1. static: no ops module imports execution_lane, fortress, or anything
     journal-shaped (AST scan — fails at review time, not at runtime);
  2. dynamic: every ops module imports and runs end-to-end in a fresh
     interpreter where importing execution_lane/fortress raises;
  3. functional: the §6.4 tax export runs from a plain fills CSV — the
     "journal" it is completeness-checked against is any iterable of fill
     dicts; nothing journal-shaped is required.

Run from the repo root:  python -m pytest tests/test_ops_standalone_no_journal.py -v
"""

import ast
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OPS = ROOT / "ops"

FORBIDDEN_ROOTS = {"execution_lane", "fortress"}


def _imported_roots(path: Path):
    tree = ast.parse(path.read_text())
    roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                roots.add(node.module.split(".")[0])
    return roots


def test_ops_modules_import_nothing_from_execution_lane_or_fortress():
    offenders = {}
    for py in sorted(OPS.glob("*.py")):
        bad = _imported_roots(py) & FORBIDDEN_ROOTS
        if bad:
            offenders[py.name] = sorted(bad)
    assert not offenders, f"ops/ must stay standalone: {offenders}"


def test_ops_has_no_journal_module_or_import():
    assert not list(OPS.glob("journal*.py")), "ops/ must not grow a journal"
    for py in sorted(OPS.glob("*.py")):
        journaly = [r for r in _imported_roots(py) if "journal" in r]
        assert not journaly, f"{py.name} imports journal-shaped modules: {journaly}"


ISOLATION_SCRIPT = r"""
import importlib
import sys

BLOCKED = {"execution_lane", "fortress"}


class _Blocker:
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".")[0] in BLOCKED:
            raise ImportError(
                fullname + " blocked: ops/ must be standalone (G0)")
        return None


sys.meta_path.insert(0, _Blocker())

for mod in ("ops", "ops.alerts", "ops.dst_scheduler", "ops.w8ben_monitor",
            "ops.ca_daily_job", "ops.tax_exports"):
    importlib.import_module(mod)

# end-to-end smoke across all four services, plain data only
from datetime import date

from ops.alerts import AlertSink
from ops.ca_daily_job import CaDailyJob, StaticCalendarProvider
from ops.dst_scheduler import DstScheduler
from ops.tax_exports import (RealizedGainsLedger, StaticFxProvider,
                             fills_from_dicts, h1)
from ops.w8ben_monitor import W8BenMonitor

w = DstScheduler().session_window(date(2026, 3, 10))
assert (w.il_open.hour, w.il_open.minute) == (15, 30), w.il_open

sink = AlertSink()
alerts = W8BenMonitor(signed=date(2023, 6, 15), sink=sink).check(date(2026, 12, 20))
assert alerts and alerts[0].severity == "URGENT", alerts

sink2 = AlertSink()
cal = StaticCalendarProvider([{"symbol": "AAA", "ex_date": "2026-08-05",
                               "action_type": "split", "ratio": 2.0}])
report = CaDailyJob(cal, sink2).run(
    date(2026, 8, 4), positions=[{"symbol": "AAA", "qty": 10}], watchlist=[],
    open_orders=[{"order_id": "o1", "symbol": "AAA"}])
assert [u["order_id"] for u in report["unmatched"]] == ["o1"], report

fills = fills_from_dicts([
    {"fill_id": "b1", "symbol": "AAA", "side": "buy", "qty": 10,
     "price": 100.0, "commission": 0.40, "traded_on": "2026-01-05"},
    {"fill_id": "s1", "symbol": "AAA", "side": "sell", "qty": 10,
     "price": 130.0, "commission": 0.40, "traded_on": "2026-03-10"},
])
export = RealizedGainsLedger(fills).export(
    *h1(2026), fx=StaticFxProvider({"2026-03-10": 3.65}))
assert export.complete and export.totals["gain_usd"] == 299.20, export.totals
assert export.totals["gain_ils"] == 1092.08, export.totals

print("STANDALONE-OK")
"""


def test_ops_import_and_run_with_execution_lane_and_fortress_blocked():
    proc = subprocess.run([sys.executable, "-c", ISOLATION_SCRIPT],
                          cwd=str(ROOT), capture_output=True, text=True,
                          timeout=120)
    assert proc.returncode == 0, (
        f"ops/ is not standalone:\n{proc.stdout}\n{proc.stderr}")
    assert "STANDALONE-OK" in proc.stdout


def test_tax_exports_run_from_plain_csv_without_any_journal(tmp_path):
    """§6.4 functional proof: fills CSV in -> semi-annual exports + annual
    Form 1301 pack out, with correct FIFO lots, window partitioning, and BoI
    FX conversion. No journal object anywhere in sight."""
    from ops.tax_exports import (RealizedGainsLedger, StaticFxProvider,
                                 fills_from_csv, h1, h2)

    csv_path = tmp_path / "fills.csv"
    csv_path.write_text(
        "fill_id,symbol,side,qty,price,commission,traded_on\n"
        "b1,AAA,buy,10,100,0.40,2026-01-05\n"
        "b2,AAA,buy,10,110,0.40,2026-02-01\n"
        "s1,AAA,sell,15,130,0.45,2026-03-10\n"   # FIFO: b1 lot + half of b2
        "b3,BBB,buy,5,50,0.40,2026-07-01\n"
        "s2,BBB,sell,5,60,0.40,2026-07-15\n")    # H2 window
    fx = StaticFxProvider({"2026-03-10": 3.65, "2026-07-15": 3.60})

    ledger = RealizedGainsLedger(fills_from_csv(csv_path))

    e1 = ledger.export(*h1(2026), fx=fx)
    assert e1.complete and len(e1.rows) == 2
    assert [r["symbol"] for r in e1.rows] == ["AAA", "AAA"]
    first, second = e1.rows
    assert first["qty"] == 10 and first["gain_usd"] == 299.30
    assert first["gain_ils"] == 1092.45            # 299.30 x 3.65
    assert second["qty"] == 5 and second["gain_usd"] == 99.65
    assert second["gain_ils"] == 363.72            # 99.65 x 3.65
    assert e1.totals["gain_usd"] == 398.95
    assert e1.totals["gain_ils"] == 1456.17
    assert e1.totals["cgt_usd"] == 99.74           # 25% of 398.95

    e2 = ledger.export(*h2(2026), fx=fx)
    assert e2.complete and [r["symbol"] for r in e2.rows] == ["BBB"]
    assert e2.rows[0]["gain_usd"] == 49.20
    assert e2.rows[0]["gain_ils"] == 177.12        # 49.20 x 3.60
    assert all(r["sold_on"] > "2026-06-30" for r in e2.rows)
    assert all(r["sold_on"] <= "2026-06-30" for r in e1.rows)

    pack = ledger.export_annual_pack(2026, fx=fx)
    assert pack["complete"] is True
    assert len(pack["annual"].rows) == 3
    assert pack["annual"].totals["gain_usd"] == 448.15     # H1 + H2

    out = tmp_path / "h1_export.csv"
    e1.write_csv(out)
    lines = out.read_text().splitlines()
    assert lines[0].startswith("symbol,acquired_on,sold_on")
    assert len(lines) == 1 + 2 + 1                 # header + rows + TOTAL
    assert lines[-1].startswith("TOTAL,")
