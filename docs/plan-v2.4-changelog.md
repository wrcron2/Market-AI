# Plan v2.3 → v2.4 — Changelog

**Issued:** 2026-08-03
**Basis:** Trading Plan Review — Verdict & Required Changes (review report §2), converted to
executable edits by the Implementation Plan — Trading System v2.4 Changes (§2, items 2.1–2.7).
**Rule:** This patch lands before any code is written against the plan. Runbook reminders, tax
provisioning, and the scheduler all inherit constants from the plan document; building on the
stale v2.3 constants would fossilize known errors into the system.

Every changed constant is listed below with its source. Nothing else in v2.3 changed.

---

## The four factual corrections

### 2.1 — Backup withholding rate: 28% → 24%

- **Old (v2.3):** US backup withholding stated as 28%.
- **New (v2.4):** Backup withholding rate is **24%**, as set by the TCJA.
- **Source:** Review report §2.
- **Downstream impact:** the W-8BEN lapse consequence (2.2) and the halt-on-sell escalation in
  the W-8BEN monitor both reference the corrected 24% figure.

### 2.2 — W-8BEN expiry: rolling timer → calendar-anchored, with monitored alerts

- **Old (v2.3):** W-8BEN treated as expiring on a rolling 3-year timer from signature; expiry
  tracked as a passive runbook note.
- **New (v2.4):** A W-8BEN is valid through **December 31 of year+3** (calendar-anchored to the
  signing year). The reminder is converted from a runbook note into a **monitored system alert
  at T-90 and T-30 days** before expiry.
- **Consequence of lapse (corrected):** an expired form exposes **gross sale proceeds** — not
  merely dividends — to **24% backup withholding**. For a system that sells daily this is the
  most expensive operational failure available; if expiry passes unrenewed, the monitor
  escalates to a halt-on-sell recommendation.
- **Source:** Review report §2.

### 2.3 — Israeli tax layer: annual-only → semi-annual reporting windows

- **Old (v2.3):** "Form 1301-ready annual export" — a single annual tax pack.
- **New (v2.4):** **Semi-annual** reporting obligation with two windows — **Jan–Jun** and
  **Jul–Dec** — plus the annual Form 1301 pack. Capital-gains tax rate 25%; surtax flags
  (2% capital-source, 3% high-income above threshold) recorded for accountant confirmation.
- **Consequence:** the realized-gains telemetry schema (per-lot, USD + ILS at the Bank of
  Israel rate on trade date) must exist **before the first live trade**, not as a retrofit.
- **Tracking:** accountant confirmation task created — see
  `docs/external-confirmations.md` (CONF-1).
- **Source:** Review report §2.

### 2.4 — §3.4 scheduling: hardcoded IL clock → anchored to America/New_York

- **Old (v2.3):** session window hardcoded as "16:30–23:00 IL".
- **New (v2.4):** all session logic is anchored to **America/New_York** (via `zoneinfo`);
  Israel time is **derived**, never hardcoded. The hardcoded "16:30–23:00 IL" string is
  deleted.
- **Why:** US and Israel switch DST on different dates, producing **two ~1–3-week divergence
  windows** each year (March and October/November) in which a hardcoded IL window arms/disarms
  at the wrong time. Unit tests must simulate both divergence windows and assert correct
  arm/disarm times.
- **Source:** Review report §2.

---

## The three one-line additions

### 2.5 — Pricing plan: tiered stays, crossover noted

Tiered pricing remains the default. Add crossover note: flip to **Fixed** above roughly
**150 shares/order**.
**Source:** Review report §2.

### 2.6 — D1 universe restriction (conditional)

If D1 is kept, restrict its universe to higher-priced liquid names (**$80+**). Rationale:
penny-spread asymmetry — below ~$45/share, friction alone exceeds the entire stylized ORB edge.
**Source:** Review report §2.

### 2.7 — New §2.4: statistical power

New plan section stating what can and cannot be concluded at 100 / 300 / 500 trades, and the
**CI pre-registration rule**: before any phase runs, the expected confidence-interval width and
the decision rule that survives it are written down. Distinguishing 8 from 12 bps takes ~2,400
trades; detecting that a true ~10 bps edge merely exists needs ~100–400 — phases may answer
"is there edge?" but never "is the edge ≥ hurdle with precision".
**Source:** Review report §2.

---

## Acceptance (per Implementation Plan §2)

- [x] Changelog lists every changed constant with its source.
- [x] The two external confirmations (Israeli accountant, IBKR support) are tracked tasks with
      owners and due dates — `docs/external-confirmations.md`.

*Tax mechanics (2.3) require confirmation by an Israeli-certified accountant before go-live;
this document is informational, not tax advice.*
