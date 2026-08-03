# Provenance manifest — point-in-time universe data layer (plan §3, task 3.4)

Every series below carries: source, retrieval date, as-of range, and stated
limitations. The same rows live in the built database's `provenance` table
(`python3 data_layer/ingest.py`); consumers (fortress, plan §4) must refuse
series that have no provenance row.

## Universe rule (task 3.1, summary)

- **Index proxied:** S&P 100 (101 tickers — 100 names + dual share classes).
- **Membership rule:** a symbol is in the universe on calendar date D iff it
  appears in the effective Wikipedia snapshot for D. Snapshot effective date =
  revision UTC date; when several revisions share a date, the last one wins.
- **Rebalance dates:** index-driven, as recorded by the snapshots (no fixed
  calendar); current recorded transitions: 2023-12-03 (vandalism, collapsed),
  2024-03-16, 2024-07-23 (edit wobble, collapsed), 2025-03-22, 2025-09-05,
  2025-09-07, 2025-09-21, 2026-03-22, 2026-05-22, 2026-06-29.
- **Delisting/departure policy:** symbols leaving the index keep
  `universe_membership` rows with `in_universe=0` through the coverage end —
  retained and marked, never deleted. Index removal is a membership event, NOT
  an exchange delisting; no exchange-level delisting occurred in-window.
- **Ticker normalization:** Wikipedia form uppercased, `.` → `-`
  (`BRK.B` → `BRK-B`, the Yahoo/price-layer form). No other transformations.

## Series: universe_membership

- **Source:** Wikipedia "S&P 100" page revision history via MediaWiki API
  (page_id 2658424); 85 revisions 2023-09-28 → 2026-07-27, collapsed to 14
  distinct constituent snapshots.
- **Recorded at:** `source/sp100_snapshots.json` (per-snapshot revid, UTC
  timestamp, full constituent list, company names, edit comment).
- **Retrieved:** 2026-08-03. **As-of range:** 2023-10-01 → 2026-08-03 (daily).
- **Cross-check:** latest snapshot equals `item_zero/universe.json` (101/101),
  an independent retrieval of the same page for Item Zero.
- **Limitations:**
  1. Wikipedia edits can lag or precede official index changes by days; at
     retrieval the page carried an `{{update inline}}` staleness flag (table
     marked "as of 2025-09-22" while HONA/BNY edits existed — page maintenance
     is uneven).
  2. Same-day vandalism/edit-wobble revisions (2023-12-03 "PIZZA",
     2024-07-23 "TJX/APPL") are recorded but collapse under the
     last-revision-of-day rule and never become effective.
  3. Sub-daily timing of index changes is not recoverable from this source.
  4. Free proxy per plan §1.1 fallback; a paid vendor (Q3 open question) would
     replace the source, not the schema.

## Series: symbol_map

- **Source:** curated from snapshot transitions (`source/symbol_map.csv`), each
  row carrying its snapshot revid.
- **Rows:**
  - `BK → BNY` effective 2026-05-22 — **verified** ticker-only rename
    (identical company name "BNY Mellon" across the transition; Yahoo likewise
    serves the pre-rename history under BNY).
  - `HON → HONA` effective 2026-06-29 — **inferred** index continuity
    (Honeywell → Honeywell Aerospace). Yahoo lists HONA as a new short-history
    symbol while HON continues trading, so this is likely a separation, not a
    pure rename. Do NOT stitch HON/HONA price series without verifying terms
    (task for the §6.2 corporate-actions job).

## Series: corporate_actions

- **Source:** Yahoo Finance `Ticker.actions` via yfinance
  (`source/corporate_actions.csv`, window 2023-10-01 → 2026-08-03) plus curated
  rows (`source/corporate_actions_curated.csv`).
- **Retrieved:** 2026-08-03. 1,087 rows: 1,073 dividends, 7 true splits, 7
  spin-off events (reclassified — see rule). Zero fetch failures; BK returns
  "not found" at Yahoo (its history is served under BNY — see symbol_map).
- **Split vs spin-off classification rule:** Yahoo encodes spin-offs in the
  `Stock Splits` field as a price-adjustment factor. Recorded rule: factor
  integer and ≥ 2 → `split` (true share-count change: AVGO 10, NVDA 10, WMT 3,
  LRCX 10, NFLX 10, NOW 5, BKNG 25); anything else → `spinoff` with the factor
  preserved in `raw_terms` (GE 1.253, MMM 1.196, DHR 1.128, HON 1.061,
  CMCSA 1.067, FDX 1.241, HON 0.9535).
- **Curated rows:** GE 2024-04-02 spin-off spawned GEV (listed 2024-04-02 per
  the GE Vernova article); reported terms 1 GEV per 4 GE — NOT verified against
  the filing. Other spin-off rows' spawned entities are unverified and left
  uncredited pending the §6.2 job.
- **Symbol keying:** rows are keyed to the symbol valid on ex_date per
  symbol_map (pre-2026-05-22 BNY rows are stored as BK); the source form is
  preserved in `raw_terms.source_symbol`.

## Adjustment-policy boundary (plan §3)

Signals consume **split-adjusted** prices; execution simulation consumes
**raw** prices. `corporate_actions.ratio` carries the split factor and
`raw_terms` the exact source row so both adjustments derive from one table.
The boundary is fixed here in the schema, not by convention.
