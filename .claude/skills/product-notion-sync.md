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
