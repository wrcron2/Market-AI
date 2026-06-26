"""
notifier.py — Email Alert System
==================================
Sends email alerts for critical trading events via SMTP (Gmail or any provider).
Also posts alerts to the Go backend for dashboard display.

Configuration (env vars):
  ALERT_EMAIL_TO      — recipient email (e.g. wrcron1@gmail.com)
  ALERT_EMAIL_FROM    — sender email
  ALERT_SMTP_HOST     — SMTP host (default: smtp.gmail.com)
  ALERT_SMTP_PORT     — SMTP port (default: 587)
  ALERT_SMTP_USER     — SMTP username
  ALERT_SMTP_PASSWORD — SMTP password or app password

Severity levels:
  CRITICAL — email always
  HIGH     — email always
  MEDIUM   — email always
  INFO     — no email (dashboard only)
"""
from __future__ import annotations

import os
import smtplib
import time
from collections import defaultdict
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Literal

import httpx
import structlog

log = structlog.get_logger(__name__)

Severity = Literal["CRITICAL", "HIGH", "MEDIUM", "INFO"]

_RATE_LIMIT: dict[str, float] = defaultdict(float)   # title → last_sent_ts
_RATE_LIMIT_SECONDS = 3600  # same alert at most once per hour


class Notifier:
    """
    Sends email alerts and posts to the backend dashboard.
    Never raises — alert failure must not kill the trade loop.
    """

    def __init__(self, backend_url: str) -> None:
        self._backend_url   = backend_url
        self._to            = os.getenv("ALERT_EMAIL_TO", "")
        self._from          = os.getenv("ALERT_EMAIL_FROM", self._to)
        self._smtp_host     = os.getenv("ALERT_SMTP_HOST", "smtp.gmail.com")
        self._smtp_port     = int(os.getenv("ALERT_SMTP_PORT", "587"))
        self._smtp_user     = os.getenv("ALERT_SMTP_USER", self._from)
        self._smtp_password = os.getenv("ALERT_SMTP_PASSWORD", "")
        self._enabled       = bool(self._to and self._smtp_password)

        if not self._enabled:
            log.warning("notifier.email_disabled",
                        reason="ALERT_EMAIL_TO or ALERT_SMTP_PASSWORD not set")

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

        subject = f"{emoji} [{severity}] MarketFlow AI — {title}"

        html = f"""
        <html><body style="font-family:Arial,sans-serif;background:#0f1117;color:#e2e8f0;padding:24px;">
        <div style="max-width:600px;margin:0 auto;background:#1e293b;border-radius:12px;padding:24px;border-left:4px solid {'#ef4444' if severity=='CRITICAL' else '#f97316' if severity=='HIGH' else '#eab308'}">
          <h2 style="margin:0 0 8px;color:#fff;">{emoji} {title}</h2>
          <p style="color:#94a3b8;font-size:12px;margin:0 0 16px;">MarketFlow AI · {severity} · {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}</p>
          <div style="background:#0f1117;border-radius:8px;padding:16px;font-size:14px;line-height:1.6;color:#cbd5e1;">{body.replace(chr(10), '<br>')}</div>
        </div>
        </body></html>
        """

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = self._from
        msg["To"]      = self._to
        msg.attach(MIMEText(body, "plain"))
        msg.attach(MIMEText(html, "html"))

        try:
            with smtplib.SMTP(self._smtp_host, self._smtp_port, timeout=10) as server:
                server.starttls()
                server.login(self._smtp_user, self._smtp_password)
                server.sendmail(self._from, self._to, msg.as_string())
            log.info("notifier.email_sent", severity=severity, to=self._to, title=title)
        except Exception as exc:
            log.error("notifier.email_failed", error=str(exc))
