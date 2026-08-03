"""Shared alert primitives for the ops layer (plan §6 "Alerting" column).

Every §6 service raises alerts through one AlertSink instead of printing or
logging into the void — the review's point was that a silent ops failure
(lapsed W-8BEN, missed DST window) is the most expensive failure class this
system has. The sink here is in-memory on purpose: the ops layer is
standalone (G0), so durable delivery (email, dashboard banner) is a wiring
concern outside ops/. Each alert carries the channels it must be delivered
on, so the wiring layer cannot accidentally downgrade a CRITICAL to a log
line.
"""

from __future__ import annotations

from dataclasses import dataclass, field

SEVERITIES = ("INFO", "WARNING", "URGENT", "CRITICAL")

EMAIL_DASHBOARD = ("email", "dashboard")


@dataclass
class Alert:
    service: str                 # e.g. "w8ben_monitor"
    severity: str                # one of SEVERITIES
    message: str
    channels: tuple = ("dashboard",)
    raised_on: str = ""          # ISO date the alert condition was observed
    details: dict = field(default_factory=dict)

    def __post_init__(self):
        if self.severity not in SEVERITIES:
            raise ValueError(f"unknown severity {self.severity!r}")


class AlertSink:
    """Collects alerts; the outer wiring drains it to email/dashboard."""

    def __init__(self):
        self.alerts = []

    def emit(self, alert: Alert) -> Alert:
        self.alerts.append(alert)
        return alert

    def for_service(self, service: str):
        return [a for a in self.alerts if a.service == service]

    def by_severity(self, severity: str):
        return [a for a in self.alerts if a.severity == severity]

    def has(self, service: str, severity: str) -> bool:
        return any(a.service == service and a.severity == severity
                   for a in self.alerts)
