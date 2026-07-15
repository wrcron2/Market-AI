"""
eod_report.py — End-of-Day Narrative Report Generator
=======================================================
Runs once per trading day, after the post-market EOD position sweep, and
writes a Markdown report to the Go backend for the dashboard to display.

Design: only the LLM writes prose (a short summary + watch items). Every
number in the report comes straight from the backend DB / Alpaca — the model
is told exactly what to say and instructed never to invent figures.

Idempotent: checks the backend for an existing report for today's date before
doing any work, so it is safe to call repeatedly during the post_market window.
Never raises — a failed report must not affect trading.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx
import structlog

from agents.router import Complexity, LLMRouter

log = structlog.get_logger(__name__)

EOD_SYSTEM = """You are a trading desk analyst writing the narrative portion of a daily
end-of-day report for a paper trading account.

You will be given today's account and trading data. Write exactly two sections:

## Summary
2-4 sentences on how the day went: overall P&L direction, notable winners/losers,
and how active the signal pipeline was. Plain, factual tone — no hype.

## Watch Items
A short bullet list (1-4 bullets) of anything that deserves attention tomorrow
(e.g. concentration risk, a losing streak, low signal activity, pending signals
piling up). If nothing stands out, write a single bullet: "Nothing notable today."

Rules:
- Use ONLY the numbers given to you below. Never invent, estimate, or round
  figures that weren't provided.
- Do not repeat the raw data tables — those are rendered separately.
- Output only the two Markdown sections above, nothing else."""


def _fmt_usd(n: float) -> str:
    sign = "+" if n >= 0 else "-"
    return f"{sign}${abs(n):,.2f}"


def _trades_table(trades: list[dict[str, Any]]) -> str:
    if not trades:
        return "_No trades closed today._"
    rows = ["| Symbol | Side | P&L | Return | Reason |", "|---|---|---|---|---|"]
    for t in trades:
        rows.append(
            f"| {t['symbol']} | {t['direction']} | {_fmt_usd(t['pnl'])} | "
            f"{t['pnl_pct']:+.2f}% | {t.get('reason') or '--'} |"
        )
    return "\n".join(rows)


def _positions_table(positions: list[dict[str, Any]]) -> str:
    if not positions:
        return "_No open positions._"
    rows = [
        "| Symbol | Side | Qty | Entry | Current | Unrealized P&L |",
        "|---|---|---|---|---|---|",
    ]
    for p in positions:
        pl = float(p.get("unrealized_pl", 0))
        rows.append(
            f"| {p['symbol']} | {p.get('side', 'long').upper()} | {float(p['qty']):.0f} | "
            f"${float(p['avg_entry_price']):.2f} | ${float(p['current_price']):.2f} | "
            f"{_fmt_usd(pl)} |"
        )
    return "\n".join(rows)


def _why_no_trades(
    auto_exec: bool,
    pending_count: int,
    trade_count: int,
    open_positions: list[dict[str, Any]],
) -> str:
    """Deterministic explanation of why the balance did or didn't move today.
    Rendered verbatim in the report — no LLM involved, so it can't hallucinate."""
    lines: list[str] = []
    if trade_count == 0:
        lines.append("**No buys or sells were executed today.** Here is exactly why:")
        if pending_count > 0:
            if auto_exec:
                lines.append(
                    f"- **{pending_count} signal(s) were staged but did not clear the auto-execute bar** "
                    "(confidence threshold, market-hours check, duplicate-position guard, or daily loss limit). "
                    "They are PENDING at the Green Light gate — approve them there, or see the Brain "
                    "Activity feed for each skip reason."
                )
            else:
                lines.append(
                    f"- **{pending_count} signal(s) are PENDING your approval at the Green Light gate.** "
                    "Auto-execute is OFF, so they will not trade until you approve them."
                )
        else:
            lines.append(
                "- **The AI staged no new trade signals today.** It scanned the market every bar, but each "
                "candidate was filtered out before becoming an order — most often because the strategy "
                "already holds the strongest ETF (rotation + deduplication gates), or because no setup "
                "cleared the confidence bar. The Brain Activity feed on the dashboard shows the reason "
                "for every skip."
            )
            lines.append(
                f"- Auto-execute is **{'ON' if auto_exec else 'OFF'}**, but that was not the blocker today — "
                "there were no staged signals to " + ("execute." if auto_exec else "approve.")
            )
    if open_positions:
        lines.append(
            f"- The account still holds **{len(open_positions)} open position(s)** "
            f"({', '.join(p['symbol'] for p in open_positions)}). Their value moves with the market — "
            "that changes *equity* and *unrealized P&L*, but **realized P&L only changes when a position "
            "is closed**, which is why the realized balance can stay flat on a day with no sells."
        )
    else:
        lines.append("- The account holds no open positions — it is fully in cash.")
    return "\n".join(lines)


def _build_data_summary(
    account: dict[str, Any],
    today_data: dict[str, Any],
    open_positions: list[dict[str, Any]],
    auto_exec: bool,
    pending_count: int,
) -> str:
    trades = today_data.get("today_trades") or []
    order_stats = today_data.get("order_stats") or {}
    return (
        f"Auto-execute: {'ON' if auto_exec else 'OFF — trades need manual Green Light approval'}\n"
        f"Signals currently PENDING approval: {pending_count}\n"
        f"Unrealized P&L on open positions: {_fmt_usd(sum(float(p.get('unrealized_pl', 0)) for p in open_positions))}\n"
        f"Equity: ${float(account.get('equity', 0)):,.2f}\n"
        f"Today's realized P&L: {_fmt_usd(today_data.get('today_realized_pnl', 0))}\n"
        f"Today's trade count: {today_data.get('today_trade_count', 0)}\n"
        f"Today's closed-trade win rate: {today_data.get('today_win_rate', 0):.1f}%\n"
        f"All-time realized P&L: {_fmt_usd(today_data.get('all_time_pnl', 0))}\n"
        f"Open positions: {len(open_positions)}\n"
        f"Closed trades today: "
        + (", ".join(f"{t['symbol']} {_fmt_usd(t['pnl'])}" for t in trades) or "none")
        + "\n"
        f"Signals generated today (all-time pipeline totals — treat as directional): "
        f"{order_stats.get('totalSignals', 0)} generated, "
        f"{order_stats.get('approved', 0)} approved, "
        f"{order_stats.get('rejected', 0)} rejected, "
        f"{order_stats.get('executed', 0)} executed"
    )


def maybe_generate_eod_report(backend_url: str, alpaca: Any, router: LLMRouter) -> None:
    """Best-effort: generate and post today's EOD report if not already done."""
    try:
        _generate(backend_url, alpaca, router)
    except Exception as exc:
        log.error("eod_report.generation_failed", error=str(exc))


def _generate(backend_url: str, alpaca: Any, router: LLMRouter) -> None:
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Idempotency: skip if today's report already exists.
    try:
        existing = httpx.get(f"{backend_url}/api/reports/eod", params={"date": date}, timeout=5)
        if existing.status_code == 200:
            log.debug("eod_report.already_exists", date=date)
            return
    except Exception as exc:
        log.warning("eod_report.existence_check_failed", error=str(exc))
        return  # don't risk duplicate work if we can't tell

    log.info("eod_report.generating", date=date)

    today_data = httpx.get(f"{backend_url}/api/reports/eod/today-data", timeout=10).json()
    account = alpaca.get_account()
    try:
        open_positions = alpaca.get_all_positions()
    except Exception as exc:
        log.warning("eod_report.positions_fetch_failed", error=str(exc))
        open_positions = []

    try:
        auto_exec = httpx.get(f"{backend_url}/api/auto-execute", timeout=5).json().get("enabled", False)
    except Exception:
        auto_exec = False
    try:
        pending = httpx.get(f"{backend_url}/api/orders/pending", timeout=5).json()
        pending_count = int(pending.get("total") or len(pending.get("orders") or []))
    except Exception:
        pending_count = 0

    data_summary = _build_data_summary(account, today_data, open_positions, auto_exec, pending_count)

    narrative = router.complete(
        system=EOD_SYSTEM,
        user=data_summary,
        complexity=Complexity.HIGH_REASON,
        max_tokens=500,
    )

    equity = float(account.get("equity", 0))
    daily_pnl = float(today_data.get("today_realized_pnl", 0))
    daily_pnl_pct = (daily_pnl / equity * 100) if equity else 0.0

    markdown = f"""# MarketFlow AI — End of Day Report
### {date} · Paper Trading (Alpaca)

{narrative.strip()}

## Trading Activity Explained

{_why_no_trades(auto_exec, pending_count, int(today_data.get('today_trade_count', 0)), open_positions)}

## Account Snapshot

| Metric | Value |
|---|---|
| Equity | ${equity:,.2f} |
| Buying Power | ${float(account.get('buying_power', 0)):,.2f} |
| Today's Realized P&L | {_fmt_usd(daily_pnl)} ({daily_pnl_pct:+.2f}%) |
| All-Time Realized P&L | {_fmt_usd(float(today_data.get('all_time_pnl', 0)))} |

## Today's Trades ({today_data.get('today_trade_count', 0)})

{_trades_table(today_data.get('today_trades') or [])}

## Open Positions ({len(open_positions)})

{_positions_table(open_positions)}

## Signal Pipeline

| Metric | Value |
|---|---|
| Generated | {(today_data.get('order_stats') or {}).get('totalSignals', 0)} |
| Approved | {(today_data.get('order_stats') or {}).get('approved', 0)} |
| Rejected | {(today_data.get('order_stats') or {}).get('rejected', 0)} |
| Executed | {(today_data.get('order_stats') or {}).get('executed', 0)} |
"""

    payload = {
        "date": date,
        "markdown": markdown,
        "equity": equity,
        "daily_pnl": daily_pnl,
        "daily_pnl_pct": daily_pnl_pct,
        "trades_count": today_data.get("today_trade_count", 0),
        "win_rate": today_data.get("today_win_rate", 0),
        "open_positions_count": len(open_positions),
    }
    resp = httpx.post(f"{backend_url}/api/reports/eod", json=payload, timeout=10)
    resp.raise_for_status()
    log.info("eod_report.saved", date=date, daily_pnl=daily_pnl)
