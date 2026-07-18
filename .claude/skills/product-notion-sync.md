---
description: How to keep the MarketFlow AI Notion workspace in sync with code changes — what to update, where, and in what format
---

# Product & Notion Sync

## Rule: Always update Notion at end of a work session

After any session where code was changed, bugs were fixed, or new features were added, update Notion. Do not wait to be asked — offer to update or update when the user says "sync Notion" or "update Notion".

## What maps to where

| Change type | Primary Notion page | Secondary page |
|---|---|---|
| Bug found & fixed | Current Flow & Status → Bugs section | Component page (e.g. Debate Agent) |
| New feature added | Current Flow & Status → Status table | Component page if it affects one component |
| Efficiency improvement | Current Flow & Status → Status table or Open Issues | Component page |
| Architecture change | Architecture Documentation | Current Flow & Status |
| New skill/knowledge rule | Current Flow & Status → Skills & Tools table | — |
| Open issue resolved | Current Flow & Status → Open Issues table (mark ✅ Done) | — |
| Config / env var change | Current Flow & Status → Status table | — |

## Current Flow & Status page — update format

### Status table row format
```
| Component name | ✅ Working / ⚠️ Partial / ❌ Not working | Short note |
```
- Only add a new row if the component is new
- Update the Status and Notes columns in place for existing components

### Bug entry format
```
### Bug N — Short descriptive title ✅ Fixed
One or two sentences: what was wrong, what the fix was.
```
- N = current highest bug number + 1 (fetch the page first to check)
- Keep it factual and concise — no implementation details, just what broke and how it was fixed

### Open Issues table format
```
| ✅ Done / 🟡 Medium / 🔴 High / 🟢 Low | Issue description |
```
- When an issue is resolved, change Priority to ✅ Done and update the description with what was done
- Add new rows for newly discovered issues

## Component page update format

When a specific component changed significantly, update its Notion page with:
1. What changed (prompt improvement, new parameter, new method)
2. Why (what problem it solved)
3. Current state (what it does now)

Keep it in plain English — the Notion pages are product/technical docs, not code comments.

## Superseded content — archive it, don't just annotate it in place

**The failure mode this section exists to prevent:** a page describes something that was true once. Real work supersedes it — a strategy is retired, a deployment approach is abandoned, a backtest number is proven wrong. The natural move is to add a warning banner ("⚠️ SUPERSEDED", "⚠️ NOT IMPLEMENTED") at the top and leave the page exactly where it is. **That is not enough.** The page still sits at the same visual weight as current truth — same place in the subpage list, same rank in search results, one click away for anyone (including a future Claude session) who lands on it without reading top-to-bottom first. A banner is necessary but not sufficient. This exact pattern caused a real incident: a corrected Sharpe ratio (0.79 → 0.356, after a measurement bug was fixed) kept living on 3 separate pages for weeks after the correction, because each page was annotated in isolation instead of the correction being propagated or the stale page archived.

**The rule:**
- **Whole page superseded** (a plan never implemented, a design concept never built, an approach abandoned) → move it into **🗄️ Archive** (`notion-move-pages`). Moving preserves the page and Notion's own edit history — nothing is deleted, and it is always reversible with another move. The banner stays on the page as context; the move is what actually removes it from the reader's default path.
- **A specific fact inside an otherwise-current page is proven wrong** (a stat, a status, a "gate passed" claim) → correct it **in place** with a short dated note on what changed and why (e.g. "corrected 2026-07-18 — see Bug N"). Don't leave the wrong number sitting next to a note saying it's wrong — state the right one, and search for *other* pages repeating the same now-wrong fact before considering the correction done.
- **Before either action**, if there's any doubt, duplicate the page (`notion-duplicate-page`) as a timestamped backup — cheap insurance on top of Notion's native page history.
- **A sub-page of a superseded parent needs its own disclaimer.** A warning banner on the parent does not protect its children from being read standalone — search surfaces child pages directly, with no guarantee the reader ever saw the parent's banner first. Give each child a one-line flag, or make sure it moves with the parent.

**Watch for this specifically when:** a trading strategy changes (the single biggest source so far), a deployment approach changes, or a backtest/validation result is corrected — these are the claims most likely to be repeated verbatim across multiple pages.

## Do NOT update Notion for

- Minor refactors with no behaviour change
- Intermediate debugging steps
- Changes that were immediately reverted
- Formatting-only changes

## Workflow when user says "update Notion" or "sync Notion"

1. Fetch the Current Flow & Status page to get current bug count and open issues state
2. Compile the list of changes from the session
3. Update the Status table for changed components
4. Add new Bug entries (numbered correctly)
5. Update Open Issues (mark resolved ones ✅ Done, add new ones)
6. Update any component-specific pages that had significant changes
7. Confirm to user what was updated and on which pages
