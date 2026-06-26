"""
notifier.py — Email Alert System (via Resend API)
===================================================
Sends email alerts for critical trading events using Resend (free tier).
Also posts alerts to the Go backend for dashboard display.

Configuration (env vars):
  RESEND_API_KEY  — API key from resend.com (free, 100 emails/day)
  ALERT_EMAIL_TO  — recipient email (e.g. wrcron1@gmail.com)

Severity levels:
  CRITICAL — email always
  HIGH     — email always
  MEDIUM   — email always
  INFO     — no email (dashboard only)

Free tier: resend.com — 100 emails/day, 3,000/month, no credit card.
"""
from __future__ import annotations

import os
import time
from collections import defaultdict
from typing import Literal

import httpx
import structlog

log = structlog.get_logger(__name__)

Severity = Literal["CRITICAL", "HIGH", "MEDIUM", "INFO"]

_RATE_LIMIT: dict[str, float] = defaultdict(float)
_RATE_LIMIT_SECONDS = 3600  # same alert at most once per hour

RESEND_API_URL = "https://api.resend.com/emails"


class Notifier:
    """
    Sends email alerts via Resend API + posts to dashboard.
    Never raises — alert failure must not kill the trade loop.
    """

    def __init__(self, backend_url: str) -> None:
        self._backend_url = backend_url
        self._api_key     = os.getenv("RESEND_API_KEY", "")
        self._to          = os.getenv("ALERT_EMAIL_TO", "wrcron1@gmail.com")
        self._enabled     = bool(self._api_key)

        if not self._enabled:
            log.warning("notifier.email_disabled",
                        reason="RESEND_API_KEY not set — dashboard alerts only")

    # ── Public API ─────────────────────────────────────────────────────────────

    def critical(self, title: str, body: str) -> None:
        """CRITICAL alert — email + dashboard. Daily loss, position blow-up."""
        self._send("CRITICAL", title, body, email=True)

    def high(self, title: str, body: str) -> None:
        """HIGH alert — email + dashboard. LLM unreachable, big move."""
        self._send("HIGH", title, body, email=True)

    def medium(self, title: str, body: str) -> None:
        """MEDIUM alert — email + dashboard. Auto-execute toggled, restart."""
        self._send("MEDIUM", title, body, email=True)

    def info(self, title: str, body: str) -> None:
        """INFO alert — dashboard only. Daily close summary."""
        self._send("INFO", title, body, email=False)

    # ── Internal ───────────────────────────────────────────────────────────────

    def _send(self, severity: Severity, title: str, body: str, email: bool) -> None:
        # Rate limit: same title at most once per hour
        now = time.time()
        key = f"{severity}:{title}"
        if now - _RATE_LIMIT[key] < _RATE_LIMIT_SECONDS:
            return
        _RATE_LIMIT[key] = now

        log.info("notifier.alert", severity=severity, title=title)

        # Post to backend for dashboard
        self._post_to_dashboard(severity, title, body)

        # Send email
        if email and self._enabled:
            self._send_email(severity, title, body)

    def _post_to_dashboard(self, severity: Severity, title: str, body: str) -> None:
        try:
            httpx.post(
                f"{self._backend_url}/api/alerts",
                json={"severity": severity, "title": title, "body": body},
                timeout=3,
            )
        except Exception as exc:
            log.warning("notifier.dashboard_post_failed", error=str(exc))

    def _send_email(self, severity: Severity, title: str, body: str) -> None:
        severity_emoji = {"CRITICAL": "🔴", "HIGH": "🟠", "MEDIUM": "🟡", "INFO": "🟢"}
        emoji = severity_emoji.get(severity, "⚪")
        border = {"CRITICAL": "#ef4444", "HIGH": "#f97316", "MEDIUM": "#eab308", "INFO": "#60a5fa"}.get(severity, "#475569")

        subject = f"{emoji} [{severity}] MarketFlow AI — {title}"
        html = f"""
        <html><body style="font-family:Arial,sans-serif;background:#0f1117;color:#e2e8f0;padding:24px;">
        <div style="max-width:600px;margin:0 auto;background:#1e293b;border-radius:12px;padding:24px;border-left:4px solid {border}">
          <h2 style="margin:0 0 8px;color:#fff;">{emoji} {title}</h2>
          <p style="color:#94a3b8;font-size:12px;margin:0 0 16px;">MarketFlow AI · {severity} · {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}</p>
          <div style="background:#0f1117;border-radius:8px;padding:16px;font-size:14px;line-height:1.6;color:#cbd5e1;">{body.replace(chr(10), '<br>')}</div>
        </div>
        </body></html>
        """

        try:
            resp = httpx.post(
                RESEND_API_URL,
                headers={"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"},
                json={
                    "from":    "MarketFlow AI <onboarding@resend.dev>",
                    "to":      [self._to],
                    "subject": subject,
                    "html":    html,
                },
                timeout=10,
            )
            if resp.status_code == 200:
                log.info("notifier.email_sent", severity=severity, to=self._to)
            else:
                log.error("notifier.email_failed", status=resp.status_code, body=resp.text[:200])
        except Exception as exc:
            log.error("notifier.email_failed", error=str(exc))
