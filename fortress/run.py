"""Fortress report runner — `python -m fortress.run` from the repo root.

Plan §4.2 acceptance: the pre-registered hurdles (fortress/hurdles.yaml) are
printed at the top of every fortress report. Plan §4.5: the verdict table is
one page of numbers — per strategy and variant: net expectancy, CI, hurdle,
pass/fail — written to fortress_verdict_table.csv at the repo root. No
interpretation prose is allowed in the CSV; commentary belongs in memos.
"""

import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fortress import engine, hurdles as hurdles_mod  # noqa: E402

VERDICT_CSV = ROOT / "fortress_verdict_table.csv"
VERDICT_COLUMNS = ["strategy", "variant", "n_rotations", "n_trades",
                   "gross_annual_pct", "friction_drag_annual_pct", "net_annual_pct",
                   "ci95_lb_net_annual_pct", "ci95_ub_net_annual_pct",
                   "hurdle", "verdict"]


def verdict_rows(rows, cfg):
    """Map engine rows onto the standardized verdict-table schema (§4.5)."""
    rules = cfg["sleeves"]["m1"]["rules"]
    out = []
    for row in rows:
        if row["cadence_pass"] == "untestable":
            verdict = "untestable"   # pre-registered abort class, not a fail
        else:
            verdict = hurdles_mod.evaluate(row, rules)["verdict"]
        out.append({
            "strategy": "m1",
            "variant": row["cadence"],
            "n_rotations": row["n_rotations"],
            "n_trades": row["n_trades"],
            "gross_annual_pct": round(row["gross_annual_pct"], 2),
            "friction_drag_annual_pct": round(row["friction_drag_annual_pct"], 2),
            "net_annual_pct": round(row["net_annual_pct"], 2),
            "ci95_lb_net_annual_pct": round(row["ci95_lb_net_annual_pct"], 2),
            "ci95_ub_net_annual_pct": round(row["ci95_ub_net_annual_pct"], 2),
            "hurdle": hurdles_mod.hurdle_label(rules),
            "verdict": verdict,
        })
    return out


def write_verdict_table(vrows, path=VERDICT_CSV):
    with open(path, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=VERDICT_COLUMNS)
        writer.writeheader()
        writer.writerows(vrows)


def build_report(vrows, cfg):
    """The full fortress report text: hurdles first, always (§4.2)."""
    stats = cfg["statistics"]
    lines = [
        "FORTRESS HURDLES (pre-registered, plan v2.4 §4.2 — fortress/hurdles.yaml, "
        f"pinned {cfg['pinned']})",
    ]
    for sleeve, sc in cfg["sleeves"].items():
        label = hurdles_mod.hurdle_label(sc["rules"])
        status = f"  [status: {sc['status']}]" if sc.get("status") else ""
        lines.append(f"  {sleeve}: {label}{status}")
    lines.append(
        f"  statistics: {stats['ci_method']}, block {stats['block_length']}, "
        f"B={stats['resamples']}, seed {stats['seed']}, level {stats['level']}")
    lines.append("")
    lines.append("VERDICT TABLE (plan §4.5 — also written to fortress_verdict_table.csv)")
    lines.append(",".join(VERDICT_COLUMNS))
    for r in vrows:
        lines.append(",".join(str(r[c]) for c in VERDICT_COLUMNS))
    return "\n".join(lines)


def main():
    cfg = hurdles_mod.load_hurdles()
    rows, details, quality = engine.run()
    vrows = verdict_rows(rows, cfg)
    write_verdict_table(vrows)
    print(build_report(vrows, cfg))


if __name__ == "__main__":
    main()
