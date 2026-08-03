"""Item Zero — M1 net-of-cost expectancy test (Trading System v2.4 plan, §1).

Implements the pre-registered specification in ../item_zero_decision_rule.md
verbatim: S&P 100 static proxy universe, 90-trading-day momentum, $20–150 price
band, top-5 equal weight, $5,000 sleeve / $1,000-per-position whole-share
sizing, membership-change-only trading, cost model v1, monthly AND weekly
cadences, circular block bootstrap CI (block 3, B=20000, seed 42).

Inputs:  data/prices.csv  (produced by fetch_data.py)
Outputs: ../item_zero_results.csv  (one row per cadence)
         data/run_report.json    (run metadata + per-rotation series)

Usage: python3 run_expectancy.py   (from the item_zero directory)
"""

import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
# Plan §4.1: the shared fortress cost model is the single source of truth.
# The local ./cost_model.py this runner originally used was promoted verbatim
# to fortress/cost_model.py; this import keeps Item Zero on the identical
# code path as the fortress (and later live TCA).
from fortress import cost_model  # noqa: E402

# --- frozen parameters (item_zero_decision_rule.md §2–§3; do not edit) ---
WINDOW_START = pd.Timestamp("2024-08-01")
WINDOW_END = pd.Timestamp("2026-07-31")
LOOKBACK = 90                 # trading days
BAND_LO, BAND_HI = 20.0, 150.0
TOP_N = 5
SLEEVE_USD = 5000.0
TARGET_PER_POSITION = 1000.0
MAX_MISSING_FRAC = 0.05       # drop ticker if >5% bars missing over fetch span
FFILL_LIMIT = 5
BOOT_BLOCK = 3
BOOT_B = 20000
BOOT_SEED = 42
PERIODS_PER_YEAR = {"monthly": 12, "weekly": 52}


def load_matrices():
    px = pd.read_csv(HERE / "data" / "prices.csv", parse_dates=["date"])
    mats = {}
    for field in ("open", "close", "adj_close"):
        mats[field] = px.pivot(index="date", columns="ticker", values=field).sort_index()
    calendar = mats["adj_close"].index

    presence = mats["adj_close"].notna().mean()
    dropped = sorted(presence[presence < 1 - MAX_MISSING_FRAC].index.tolist())
    kept = sorted(presence[presence >= 1 - MAX_MISSING_FRAC].index.tolist())
    for f in mats:
        mats[f] = mats[f][kept]

    ffills = 0
    for f in ("open", "close", "adj_close"):
        before = int(mats[f].isna().sum().sum())
        mats[f] = mats[f].ffill(limit=FFILL_LIMIT)
        ffills += before - int(mats[f].isna().sum().sum())

    factor = mats["adj_close"] / mats["close"]
    mats["adj_open"] = mats["open"] * factor
    mats["adj_open"] = mats["adj_open"].ffill(limit=FFILL_LIMIT)
    return mats, calendar, {"dropped_tickers": dropped, "ffilled_cells": ffills, "kept_tickers": kept}


def rotation_days(calendar, cadence):
    win = calendar[(calendar >= WINDOW_START) & (calendar <= WINDOW_END)]
    days = pd.Series(win)
    if cadence == "monthly":
        groups = days.dt.to_period("M")
    else:
        groups = days.dt.to_period("W-SUN")
    firsts = days.groupby(groups).min().tolist()
    last_day = win[-1]
    return [d for d in firsts if d < last_day], last_day


def run_cadence(mats, calendar, cadence):
    adj, adj_open = mats["adj_close"], mats["adj_open"]
    raw_open, raw_close = mats["open"], mats["close"]
    tickers = list(adj.columns)
    pos = {d: i for i, d in enumerate(calendar)}

    rots, last_day = rotation_days(calendar, cadence)
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
        mom = adj.iloc[s_idx] / adj.iloc[s_idx - LOOKBACK] - 1.0
        elig = [t for t in tickers
                if np.isfinite(mom[t]) and BAND_LO <= close_s.get(t, np.nan) <= BAND_HI]
        eligible_counts.append(len(elig))
        ranked = sorted(elig, key=lambda t: mom[t], reverse=True)

        target, seen = [], set()
        for t in ranked:
            if len(target) == TOP_N:
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
            sh = math.floor(TARGET_PER_POSITION / raw_open.at[e, t])
            if sh <= 0:
                skips["entry_no_raw_open"] += 1
                continue
            holdings[t] = sh
            costs += cost_model.trade_cost(sh, raw_open.at[e, t])
            traded_notional += sh * raw_open.at[e, t]
            trades.append((str(e.date()), "buy", t, sh))

        pnl = 0.0
        for t, sh in holdings.items():
            exit_px = adj_close_at = None
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
        turnovers.append(traded_notional / SLEEVE_USD)
        periods.append({"rotation": str(e.date()), "gross": pnl / SLEEVE_USD,
                        "net": (pnl - costs) / SLEEVE_USD, "costs": costs,
                        "n_eligible": eligible_counts[-1]})

    net = np.array([p["net"] for p in periods])
    gross = np.array([p["gross"] for p in periods])
    P = PERIODS_PER_YEAR[cadence]
    n = len(periods)

    rng = np.random.default_rng(BOOT_SEED)
    n_blocks = math.ceil(n / BOOT_BLOCK)
    stats = np.empty(BOOT_B)
    for b in range(BOOT_B):
        starts = rng.integers(0, n, size=n_blocks)
        idx = (starts[:, None] + np.arange(BOOT_BLOCK)[None, :]).ravel() % n
        stats[b] = net[idx[:n]].mean() * P
    ci_lb, ci_ub = np.percentile(stats, [2.5, 97.5])

    gross_ann = gross.mean() * P
    drag_ann = total_costs / n * P / SLEEVE_USD
    net_ann = net.mean() * P
    rule_a = bool(ci_lb > 0)
    rule_b = bool(net_ann > 2 * drag_ann)
    untestable = (np.array(eligible_counts) < TOP_N).mean() > 0.25
    cadence_pass = "untestable" if untestable else bool(rule_a and rule_b)

    row = {
        "cadence": cadence,
        "window_start": str(WINDOW_START.date()),
        "window_end": str(WINDOW_END.date()),
        "n_rotations": n,
        "n_trades": len(trades),
        "avg_eligible": round(float(np.mean(eligible_counts)), 1),
        "min_eligible": int(np.min(eligible_counts)),
        "avg_turnover_pct_per_rotation": round(float(np.mean(turnovers)) * 100, 1),
        "total_costs_usd": round(float(total_costs), 2),
        "gross_bps_per_rotation": round(float(gross.mean()) * 1e4, 2),
        "net_bps_per_rotation": round(float(net.mean()) * 1e4, 2),
        "gross_annual_pct": round(float(gross_ann) * 100, 2),
        "friction_drag_annual_pct": round(float(drag_ann) * 100, 2),
        "net_annual_pct": round(float(net_ann) * 100, 2),
        "ci95_lb_net_annual_pct": round(float(ci_lb) * 100, 2),
        "ci95_ub_net_annual_pct": round(float(ci_ub) * 100, 2),
        "rule_a_ci95_lb_gt_0": rule_a,
        "rule_b_net_gt_2x_friction": rule_b,
        "cadence_pass": cadence_pass,
    }
    detail = {"periods": periods, "n_trades": len(trades), "skips": skips,
              "eligible_min": int(np.min(eligible_counts)),
              "eligible_max": int(np.max(eligible_counts))}
    return row, detail


def main():
    mats, calendar, quality = load_matrices()
    if len(quality["kept_tickers"]) < 80:
        raise SystemExit(f"ABORT (pre-registered): only {len(quality['kept_tickers'])} tickers loaded")

    rows, details = [], {}
    for cadence in ("monthly", "weekly"):
        row, detail = run_cadence(mats, calendar, cadence)
        rows.append(row)
        details[cadence] = detail

    out = pd.DataFrame(rows)
    out.to_csv(ROOT / "item_zero_results.csv", index=False)
    report = {"run_at": pd.Timestamp.now(tz="UTC").isoformat(),
              "data_quality": quality, "cadences": details}
    (HERE / "data" / "run_report.json").write_text(json.dumps(report, indent=2, default=str))

    print(out.to_string(index=False))
    go = any(r["cadence_pass"] is True for r in rows)
    print("\nVERDICT PER PRE-REGISTERED RULE:", "GO" if go else "PARK")


if __name__ == "__main__":
    main()
