"""Loader + evaluator for the pre-registered hurdle config (plan v2.4 §4.2).

`fortress/hurdles.yaml` is the single file holding every sleeve's thresholds;
it is printed at the top of every fortress report. Evaluation uses strict
greater-than throughout, matching the Item Zero decision rule exactly.
"""

from pathlib import Path

import yaml

DEFAULT_PATH = Path(__file__).resolve().parent / "hurdles.yaml"


def load_hurdles(path=DEFAULT_PATH):
    """The parsed hurdle config (version, statistics, per-sleeve rules)."""
    with open(path) as fh:
        return yaml.safe_load(fh)


def evaluate(metrics, rules):
    """Evaluate a metrics row against one sleeve's hurdle rules.

    `metrics` must carry the keys the rules reference:
    ci95_lb_net_annual_pct / net_annual_pct / friction_drag_annual_pct for the
    net-expectancy rules, gross_bps_per_trade for the D1 gross rule.

    Returns {"checks": {rule_name: bool}, "verdict": "pass" | "fail"}.
    """
    checks = {}
    if "ci95_lb_net_annual_gt" in rules:
        checks["ci95_lb_net_annual_gt"] = bool(
            metrics["ci95_lb_net_annual_pct"] > rules["ci95_lb_net_annual_gt"])
    if "net_annual_gt_x_friction" in rules:
        checks["net_annual_gt_x_friction"] = bool(
            metrics["net_annual_pct"]
            > rules["net_annual_gt_x_friction"] * metrics["friction_drag_annual_pct"])
    if "gross_per_trade_bps_gt" in rules:
        checks["gross_per_trade_bps_gt"] = bool(
            metrics["gross_bps_per_trade"] > rules["gross_per_trade_bps_gt"])
    verdict = "pass" if checks and all(checks.values()) else "fail"
    return {"checks": checks, "verdict": verdict}


def hurdle_label(rules):
    """Compact one-cell rendering of a rule set for the verdict table."""
    parts = []
    if "ci95_lb_net_annual_gt" in rules:
        parts.append(f"ci95_lb>{rules['ci95_lb_net_annual_gt']:g}")
    if "net_annual_gt_x_friction" in rules:
        parts.append(f"net>{rules['net_annual_gt_x_friction']:g}x_friction_drag")
    if "gross_per_trade_bps_gt" in rules:
        parts.append(f"gross_per_trade>{rules['gross_per_trade_bps_gt']:g}bps")
    return " & ".join(parts)
