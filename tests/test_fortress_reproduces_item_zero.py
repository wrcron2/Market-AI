"""G1 gate (plan v2.4 §4.3, §9): the validation fortress reproduces Item Zero.

Runs the fortress walk-forward engine (fortress/engine.py) on the Item Zero
price cache and requires every number published in item_zero_results.csv to be
reproduced within that CSV's own rounding. Also pins:

- §4.1 identical code path: item_zero has no local cost model anymore; the
  engine's cost calls resolve to fortress.cost_model, and the frozen research
  runner imports the same module.
- §4.2 hurdles: fortress/hurdles.yaml loads, carries the pre-registered
  thresholds, reproduces the published rule flags, and is printed at the top
  of the fortress report.
- §4.3 embargoed folds: make_folds drops exactly the embargo gap; fold
  evaluation is deterministic.
- §4.5 verdict table: the committed fortress_verdict_table.csv matches a fresh
  engine run exactly, with pass verdicts on both cadences.

Fully offline: reads only committed/cached files. Run from the repo root:
python -m pytest tests/test_fortress_reproduces_item_zero.py -v
"""

import csv
from pathlib import Path

import numpy as np
import pytest

from fortress import cost_model, engine, hurdles as hurdles_mod, run as run_mod

ROOT = Path(__file__).resolve().parents[1]

TOL_2DP = 0.005 + 1e-9   # half the rounding step of the 2-decimal CSV fields
TOL_1DP = 0.05 + 1e-9    # same for the 1-decimal fields

FIELDS_2DP = ["total_costs_usd", "gross_bps_per_rotation", "net_bps_per_rotation",
              "gross_annual_pct", "friction_drag_annual_pct", "net_annual_pct",
              "ci95_lb_net_annual_pct", "ci95_ub_net_annual_pct"]
FIELDS_1DP = ["avg_eligible", "avg_turnover_pct_per_rotation"]
FIELDS_INT = ["n_rotations", "n_trades", "min_eligible"]


@pytest.fixture(scope="session")
def engine_out():
    rows, details, quality = engine.run()
    return rows, details, quality


@pytest.fixture(scope="session")
def engine_rows(engine_out):
    return {r["cadence"]: r for r in engine_out[0]}


@pytest.fixture(scope="session")
def published():
    with open(ROOT / "item_zero_results.csv") as fh:
        return {r["cadence"]: r for r in csv.DictReader(fh)}


# --- §4.3: reproduction within rounding --------------------------------------

@pytest.mark.parametrize("cadence", ["monthly", "weekly"])
def test_reproduces_published_numbers(engine_rows, published, cadence):
    got, pub = engine_rows[cadence], published[cadence]
    assert got["window_start"] == pub["window_start"]
    assert got["window_end"] == pub["window_end"]
    for f in FIELDS_INT:
        assert got[f] == int(pub[f]), f
    for f in FIELDS_1DP:
        assert got[f] == pytest.approx(float(pub[f]), abs=TOL_1DP), f
    for f in FIELDS_2DP:
        assert got[f] == pytest.approx(float(pub[f]), abs=TOL_2DP), f


@pytest.mark.parametrize("cadence", ["monthly", "weekly"])
def test_reproduces_rule_flags_and_verdict(engine_rows, published, cadence):
    got, pub = engine_rows[cadence], published[cadence]
    assert got["rule_a_ci95_lb_gt_0"] is (pub["rule_a_ci95_lb_gt_0"] == "True")
    assert got["rule_b_net_gt_2x_friction"] is (pub["rule_b_net_gt_2x_friction"] == "True")
    expected_pass = True if pub["cadence_pass"] == "True" else pub["cadence_pass"]
    assert got["cadence_pass"] == expected_pass


def test_universe_quality_matches_the_frozen_run(engine_out):
    quality = engine_out[2]
    assert len(quality["kept_tickers"]) == 99          # 101 minus GEV, HONA
    assert quality["dropped_tickers"] == ["GEV", "HONA"]


# --- §4.1: identical cost-model code path -------------------------------------

def test_item_zero_has_no_local_cost_model():
    assert not (ROOT / "item_zero" / "cost_model.py").exists()


def test_engine_uses_the_shared_cost_model():
    assert engine.cost_model is cost_model


def test_frozen_research_runner_imports_the_shared_cost_model():
    source = (ROOT / "item_zero" / "run_expectancy.py").read_text()
    assert "from fortress import cost_model" in source
    assert "\nimport cost_model" not in source


# --- §4.2: hurdle config --------------------------------------------------------

def test_hurdles_config_loads_with_preregistered_thresholds():
    cfg = hurdles_mod.load_hurdles()
    m1 = cfg["sleeves"]["m1"]["rules"]
    assert m1["ci95_lb_net_annual_gt"] == 0.0
    assert m1["net_annual_gt_x_friction"] == 2.0
    assert cfg["statistics"]["seed"] == 42
    assert cfg["statistics"]["block_length"] == 3
    assert cfg["statistics"]["resamples"] == 20000
    # every sleeve in the config carries at least one evaluable rule
    for sleeve, sc in cfg["sleeves"].items():
        assert sc["rules"], sleeve


def test_hurdle_evaluation_reproduces_published_verdicts(engine_rows, published):
    cfg = hurdles_mod.load_hurdles()
    rules = cfg["sleeves"]["m1"]["rules"]
    for cadence in ("monthly", "weekly"):
        result = hurdles_mod.evaluate(engine_rows[cadence], rules)
        assert result["checks"]["ci95_lb_net_annual_gt"] is (
            published[cadence]["rule_a_ci95_lb_gt_0"] == "True")
        assert result["checks"]["net_annual_gt_x_friction"] is (
            published[cadence]["rule_b_net_gt_2x_friction"] == "True")
        assert result["verdict"] == "pass"


def test_report_prints_hurdles_before_the_table(engine_rows):
    cfg = hurdles_mod.load_hurdles()
    report = run_mod.build_report(run_mod.verdict_rows(engine_rows.values(), cfg), cfg)
    assert report.startswith("FORTRESS HURDLES")
    assert report.index("FORTRESS HURDLES") < report.index("VERDICT TABLE")


# --- §4.3: embargoed folds ------------------------------------------------------

def test_embargoed_folds_drop_exactly_the_gap():
    n, k, e = 24, 4, 3
    folds = engine.make_folds(n, k, e)
    assert sum(len(f) for f in folds) == n - (k - 1) * e
    used = sorted(i for f in folds for i in f)
    dropped = sorted(set(range(n)) - set(used))
    bounds = [round(i * n / k) for i in range(1, k)]
    expected_dropped = sorted(j for b in bounds for j in range(b, b + e))
    assert dropped == expected_dropped
    for f in folds:
        assert f == list(range(f[0], f[-1] + 1))       # contiguous blocks


def test_fold_evaluation_is_deterministic(engine_out):
    net = np.array([p["net"] for p in engine_out[1]["monthly"]["periods"]])
    folds = engine.make_folds(len(net), 4, 3)
    first = engine.evaluate_folds(net, folds, 12)
    second = engine.evaluate_folds(net, folds, 12)
    assert first == second
    assert [f["n"] for f in first] == [len(f) for f in folds]


# --- §4.5: committed verdict table matches a fresh run --------------------------

def test_committed_verdict_table_matches_fresh_run(engine_out):
    cfg = hurdles_mod.load_hurdles()
    fresh = run_mod.verdict_rows(engine_out[0], cfg)
    with open(ROOT / "fortress_verdict_table.csv") as fh:
        committed = list(csv.DictReader(fh))
    assert len(committed) == len(fresh) == 2
    for committed_row, fresh_row in zip(committed, fresh):
        assert list(committed_row.keys()) == run_mod.VERDICT_COLUMNS
        for key, value in fresh_row.items():
            assert committed_row[key] == str(value), key
    assert all(r["verdict"] == "pass" for r in committed)
