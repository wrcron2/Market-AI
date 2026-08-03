# External Confirmations — Tracked Tasks (v2.4, Phase 0)

**Issued:** 2026-08-03 · **Source:** Implementation Plan §2 ("Parallel external confirmations")
and §10 (open questions Q1, Q2).

These are **tracked tasks with owners and due dates**, not "send an email and forget." Each
task stays open until the counterparty's answer is recorded in the Resolution field. The
tracking protocol at the bottom defines follow-up and escalation. A slow counterparty blocks
only the phase listed under "Blocks" — nothing else.

## Task register

| ID | Counterparty & question | Owner | Sent | Due | Status | Resolution |
|---|---|---|---|---|---|---|
| CONF-1 | Israeli accountant — confirm semi-annual filing deadlines, required forms, and the FX-conversion rule (Bank of Israel rate on trade date) for the realized-gains ledger | Ron Leibovitch (developer) | 2026-08-03 | 2026-08-10 | Open | — |
| CONF-2 | IBKR support — confirm this account's Rule 4210 implementation status (brokers may phase in until October 20, 2027), so D1 feasibility rests on a ticket answer, not an assumption | Ron Leibovitch (developer) | 2026-08-03 | 2026-08-17 | Open | — |

## CONF-1 — Israeli accountant

- **Question (sent):** (a) exact semi-annual reporting deadlines for the Jan–Jun and Jul–Dec
  windows; (b) which forms each window requires, alongside the annual Form 1301 pack;
  (c) the FX-conversion rule for per-lot realized gains (USD → ILS at the Bank of Israel rate
  on trade date, per plan §6.4).
- **Owner:** Ron Leibovitch (developer) — responsible for sending, follow-up, and recording
  the answer.
- **Sent:** 2026-08-03 · **Response due:** 2026-08-10.
- **Blocks:** §6.4 tax-export **format sign-off only** (open question Q1). Construction of the
  export service is NOT blocked and proceeds in parallel.
- **Out of scope for this task:** surtax exposure (2% capital-source / 3% high-income) rides
  the first annual review (open question Q5), not this confirmation.

## CONF-2 — IBKR support

- **Question (sent):** is Pattern-Day-Trader Rule 4210 currently enforced on this account as
  implemented by IBKR, given brokers may phase in the amended rule until October 20, 2027?
- **Owner:** Ron Leibovitch (developer) — responsible for the ticket, follow-up, and recording
  the answer.
- **Sent:** 2026-08-03 · **Response due:** 2026-08-17 (support-ticket latency budget).
- **Blocks:** **D1 feasibility only** (open question Q2). No other phase depends on this
  answer; M1/M2 work proceeds regardless.

## Tracking protocol

1. **Follow-up:** if no reply by T+3 business days, the owner sends one nudge (accountant:
   phone/email; IBKR: ticket bump).
2. **Due-date breach:** if still unanswered at the due date, the task flips to `Escalated` and
   the blocked item (CONF-1: §6.4 format sign-off; CONF-2: D1 feasibility) is flagged at the
   next phase gate. No other work waits.
3. **Resolution:** when an answer arrives, record the date and a one-line summary in the
   Resolution column, flip Status to `Closed`, and — for CONF-1 — attach the confirmed deadline
   table to the §6.4 export spec.
4. **Both tasks were sent in Phase 0 week (2026-08-03)** and are non-blocking for Item Zero and
   the v2.4 doc patch, which completed the same week.
