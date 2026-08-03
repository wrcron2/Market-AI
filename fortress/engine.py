"""Validation fortress — walk-forward runner (Trading System v2.4 plan, §4.3).

Productionized re-implementation of the Item Zero research runner
(item_zero/run_expectancy.py) against the frozen pre-registered specification
in item_zero_decision_rule.md §2–§3. The G1 gate (plan §9) is that this
engine, fed the Item Zero price cache, reproduces item_zero_results.csv
within that CSV's own rounding — pinned by
tests/test_fortress_reproduces_item_zero.py.

Structure:
- Strictly walk-forward: each rotation's signal uses only data up to the last
  trading day before the rebalance day; execution at the rebalance day's raw
  open. No look-ahead anywhere in the loop.
- Embargoed evaluation folds (make_folds / evaluate_folds): contiguous test
  blocks of rotations with an embargo gap dropped before each block, so
  holdings overlapping a boundary never leak into two evaluation segments.
  The frozen spec has no fitted parameters, so folds are evaluation-only —
  the headline reproduction number is always the full-window run.
- Circular block bootstrap CI (block 3, 20,000 resamples, seed 42) — the
  pre-registered machinery, identical to Item Zero's.
- All costs flow through the shared cost model (fortress.cost_model, §4.1) —
  the identical code path used by Item Zero and, later, live TCA.

Data source: the Item Zero price cache (item_zero/data/prices.csv). The §3
data layer carries point-in-time membership / symbol_map / corporate actions
but no price series yet; `membership_provider` is the hook for the
survivorship-free re-run once price history for departed names exists. It is
a callable(signal_day) -> set of allowed tickers, applied to entry
candidates only (index membership is not an exit signal).
"""

import math
from dataclasses import dataclass, field, replace
from pathlib import Path

import numpy as np
import pandas as pd

from fortress import cost_model

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PRICES = ROOT / "item_zero" / "data" / "prices.csv"


@dataclass(frozen=True)
class Spec:
    """Backtest specification. Defaults are the frozen, pre-registered Item
    Zero parameters (item_zero_decision_rule.md §2–§3). The fortress
    reproduces Item Zero under exactly these values; any other value is a new
    experiment requiring its own pre-registration (plan §4.4)."""
    window_start: str = "2024-08-01"
    window_end: str = "2026-07-31"
    lookback: int = 90                 # trading days
    band_lo: float = 20.0
    band_hi: float = 150.0
    top_n: int = 5
    sleeve_usd: float = 5000.0
    target_per_position: float = 1000.0
    max_missing_frac: float = 0.05     # drop ticker if >5% bars missing over fetch span
    ffill_limit: int = 5
    boot_block: int = 3
    boot_b: int = 20000
    boot_seed: int = 42
    periods_per_year: dict = field(default_factory=lambda: {"monthly": 12, "weekly": 52})


FROZEN_ITEM_ZERO = Spec()


def load_price_matrices(csv_path=DEFAULT_PRICES, spec=FROZEN_ITEM_ZERO):
    """Long CSV (date,ticker,open,close,adj_close) -> wide matrices + quality.

    Cleaning rules identical to Item Zero: drop tickers missing >5% of bars
    over the fetch span, forward-fill the remaining gaps (limit 5), derive
    split-adjusted opens as raw_open x (adj_close/close). Signals and returns
    consume adjusted prices; share counts, the price band, and notionals
    consume raw prices (the plan §3 adjustment boundary).
    """
    px = pd.read_csv(csv_path, parse_dates=["date"])
    mats = {}
    for f in ("open", "close", "adj_close"):
        mats[f] = px.pivot(index="date", columns="ticker", values=f).sort_index()
    calendar = mats["adj_close"].index

    presence = mats["adj_close"].notna().mean()
    dropped = sorted(presence[presence < 1 - spec.max_missing_frac].index.tolist())
    kept = sorted(presence[presence >= 1 - spec.max_missing_frac].index.tolist())
    for f in mats:
        mats[f] = mats[f][kept]

    ffills = 0
    for f in ("open", "close", "adj_close"):
        before = int(mats[f].isna().sum().sum())
        mats[f] = mats[f].ffill(limit=spec.ffill_limit)
        ffills += before - int(mats[f].isna().sum().sum())

    factor = mats["adj_close"] / mats["close"]
    mats["adj_open"] = mats["open"] * factor
    mats["adj_open"] = mats["adj_open"].ffill(limit=spec.ffill_limit)
    return mats, calendar, {"dropped_tickers": dropped, "ffilled_cells": ffills,
                            "kept_tickers": kept}


def rotation_days(calendar, cadence, spec=FROZEN_ITEM_ZERO):
    """Rebalance days: the first trading day of each calendar month / ISO week
    inside the window, plus the final window day as liquidation boundary."""
    win = calendar[(calendar >= pd.Timestamp(spec.window_start))
                   & (calendar <= pd.Timestamp(spec.window_end))]
    days = pd.Series(win)
    if cadence == "monthly":
        groups = days.dt.to_period("M")
    else:
        groups = days.dt.to_period("W-SUN")
    firsts = days.groupby(groups).min().tolist()
    last_day = win[-1]
    return [d for d in firsts if d < last_day], last_day


def block_bootstrap_ci(net, periods_per_year, spec=FROZEN_ITEM_ZERO):
    """Circular block bootstrap 95% CI of annualized mean net return.

    The pre-registered machinery (item_zero_decision_rule.md §3): block length
    3 because momentum holdings overlap between consecutive rotations, 20,000
    resamples, seed 42, percentile method.
    """
    n = len(net)
    rng = np.random.default_rng(spec.boot_seed)
    n_blocks = math.ceil(n / spec.boot_block)
    stats = np.empty(spec.boot_b)
    for b in range(spec.boot_b):
        starts = rng.integers(0, n, size=n_blocks)
        idx = (starts[:, None] + np.arange(spec.boot_block)[None, :]).ravel() % n
        stats[b] = net[idx[:n]].mean() * periods_per_year
    return np.percentile(stats, [2.5, 97.5])


def make_folds(n_rotations, n_folds, embargo):
    """Contiguous test-fold rotation indices with an embargo gap.

    Fold boundaries are spaced evenly over 0..n_rotations-1; the `embargo`
    rotations immediately preceding each test block (all but the first) are
    excluded from every fold, so holdings overlapping a boundary never appear
    in two evaluation segments. Evaluation-only: the frozen spec trains
    nothing, so there is no train split to embargo from.
    """
    if n_folds < 2:
        raise ValueError("n_folds must be >= 2")
    if embargo < 0:
        raise ValueError("embargo must be >= 0")
    bounds = [round(i * n_rotations / n_folds) for i in range(n_folds + 1)]
    folds = []
    for k in range(n_folds):
        lo = bounds[k] + (embargo if k > 0 else 0)
        folds.append(list(range(lo, bounds[k + 1])))
    return folds


def evaluate_folds(net, folds, periods_per_year, spec=FROZEN_ITEM_ZERO):
    """Per-fold annualized mean net + block-bootstrap 95% CI (seeded per fold,
    so fold statistics are deterministic and independently reproducible)."""
    out = []
    for k, idx in enumerate(folds):
        sub = np.asarray(net, dtype=float)[idx]
        lb, ub = block_bootstrap_ci(sub, periods_per_year,
                                    replace(spec, boot_seed=spec.boot_seed + 1 + k))
        out.append({"fold": k, "n": len(idx),
                    "net_annual_pct": float(sub.mean() * periods_per_year) * 100,
                    "ci95_lb_net_annual_pct": float(lb) * 100,
                    "ci95_ub_net_annual_pct": float(ub) * 100})
    return out


def run_cadence(mats, calendar, cadence, spec=FROZEN_ITEM_ZERO, membership_provider=None):
    """One cadence of the frozen M1 spec, walked forward rotation by rotation.

    Signal day = last trading day before the rebalance day; execution at the
    rebalance day's raw open; membership-change-only trading; costs charged on
    entry and exit and attributed to the rotation period they open; positions
    open at the window end are liquidated at the final close.
    """
    adj, adj_open = mats["adj_close"], mats["adj_open"]
    raw_open, raw_close = mats["open"], mats["close"]
    tickers = list(adj.columns)
    pos = {d: i for i, d in enumerate(calendar)}

    rots, last_day = rotation_days(calendar, cadence, spec)
    boundaries = rots + [last_day]

    holdings = {}  # ticker -> shares
    periods, trades, eligible_counts, turnovers = [], [], [], []
    skips = {"entry_no_raw_open": 0, "exit_no_raw_open": 0}
    total_costs = 0.0

    for k, e in enumerate(boundaries[:-1]):
        e_next = boundaries[k + 1]
        is_final = e_next == last_day
        s_idx = pos[e] - 1                     # signal day: last trading day before rebalance
        s = calendar[s_idx]

        close_s = raw_close.loc[s]
        mom = adj.iloc[s_idx] / adj.iloc[s_idx - spec.lookback] - 1.0
        elig = [t for t in tickers
                if np.isfinite(mom[t]) and spec.band_lo <= close_s.get(t, np.nan) <= spec.band_hi]
        if membership_provider is not None:    # §3 point-in-time membership hook
            members = membership_provider(s)
            elig = [t for t in elig if t in members]
        eligible_counts.append(len(elig))
        ranked = sorted(elig, key=lambda t: mom[t], reverse=True)

        target, seen = [], set()
        for t in ranked:
            if len(target) == spec.top_n:
                break
            if t in holdings or np.isfinite(raw_open.at[e, t]):
                target.append(t)               # tradable at the rebalance open
                seen.add(t)
            else:
                skips["entry_no_raw_open"] += 1

        exits, entries = [], []
        for t in list(holdings):
            if t not in seen:
                if np.isfinite(raw_open.at[e, t]):
                    exits.append(t)
                else:
                    skips["exit_no_raw_open"] += 1  # cannot sell: carry the position
        for t in target:
            if t not in holdings:
                entries.append(t)

        costs = 0.0
        traded_notional = 0.0
        for t in exits:
            sh = holdings.pop(t)
            costs += cost_model.trade_cost(sh, raw_open.at[e, t])
            traded_notional += sh * raw_open.at[e, t]
            trades.append((str(e.date()), "sell", t, sh))
        for t in entries:
            sh = math.floor(spec.target_per_position / raw_open.at[e, t])
            if sh <= 0:
                skips["entry_no_raw_open"] += 1
                continue
            holdings[t] = sh
            costs += cost_model.trade_cost(sh, raw_open.at[e, t])
            traded_notional += sh * raw_open.at[e, t]
            trades.append((str(e.date()), "buy", t, sh))

        pnl = 0.0
        for t, sh in holdings.items():
            if is_final:
                exit_px = mats["adj_close"].at[last_day, t]
            else:
                exit_px = adj_open.at[e_next, t]
            pnl += sh * (exit_px - adj_open.at[e, t])

        if is_final:  # liquidate at the final close, full exit costs charged
            for t, sh in holdings.items():
                costs += cost_model.trade_cost(sh, raw_close.at[last_day, t])
                traded_notional += sh * raw_close.at[last_day, t]
                trades.append((str(last_day.date()), "final-sell", t, sh))
            holdings = {}

        total_costs += costs
        turnovers.append(traded_notional / spec.sleeve_usd)
        periods.append({"rotation": str(e.date()), "gross": pnl / spec.sleeve_usd,
                        "net": (pnl - costs) / spec.sleeve_usd, "costs": costs,
                        "n_eligible": eligible_counts[-1]})

    net = np.array([p["net"] for p in periods])
    gross = np.array([p["gross"] for p in periods])
    P = spec.periods_per_year[cadence]
    n = len(periods)

    ci_lb, ci_ub = block_bootstrap_ci(net, P, spec)

    gross_ann = gross.mean() * P
    drag_ann = total_costs / n * P / spec.sleeve_usd
    net_ann = net.mean() * P
    rule_a = bool(ci_lb > 0)
    rule_b = bool(net_ann > 2 * drag_ann)
    untestable = (np.array(eligible_counts) < spec.top_n).mean() > 0.25
    cadence_pass = "untestable" if untestable else bool(rule_a and rule_b)

    row = {
        "cadence": cadence,
        "window_start": spec.window_start,
        "window_end": spec.window_end,
        "n_rotations": n,
        "n_trades": len(trades),
        "avg_eligible": float(np.mean(eligible_counts)),
        "min_eligible": int(np.min(eligible_counts)),
        "avg_turnover_pct_per_rotation": float(np.mean(turnovers)) * 100,
        "total_costs_usd": float(total_costs),
        "gross_bps_per_rotation": float(gross.mean()) * 1e4,
        "net_bps_per_rotation": float(net.mean()) * 1e4,
        "gross_annual_pct": float(gross_ann) * 100,
        "friction_drag_annual_pct": float(drag_ann) * 100,
        "net_annual_pct": float(net_ann) * 100,
        "ci95_lb_net_annual_pct": float(ci_lb) * 100,
        "ci95_ub_net_annual_pct": float(ci_ub) * 100,
        "rule_a_ci95_lb_gt_0": rule_a,
        "rule_b_net_gt_2x_friction": rule_b,
        "cadence_pass": cadence_pass,
    }
    detail = {"periods": periods, "n_trades": len(trades), "skips": skips,
              "eligible_min": int(np.min(eligible_counts)),
              "eligible_max": int(np.max(eligible_counts))}
    return row, detail


def run(csv_path=DEFAULT_PRICES, spec=FROZEN_ITEM_ZERO, cadences=("monthly", "weekly"),
        membership_provider=None):
    """Full fortress pass: load prices, walk every cadence forward, return
    (rows, details, quality). Aborts per the pre-registered rule if the
    universe fails to load."""
    mats, calendar, quality = load_price_matrices(csv_path, spec)
    if len(quality["kept_tickers"]) < 80:
        raise RuntimeError(
            f"ABORT (pre-registered): only {len(quality['kept_tickers'])} tickers loaded")
    rows, details = [], {}
    for cadence in cadences:
        row, detail = run_cadence(mats, calendar, cadence, spec, membership_provider)
        rows.append(row)
        details[cadence] = detail
    return rows, details, quality
