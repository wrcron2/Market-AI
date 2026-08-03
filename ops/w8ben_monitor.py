"""W-8BEN expiry monitor (plan v2.4 §6.3, doc edit §2.2).

Why this exists: a lapsed W-8BEN exposes gross sale PROCEEDS — not merely
dividends — to 24% backup withholding (rate per plan §2.1, TCJA; was 28%).
For a system that sells daily, that is the most expensive operational
failure available, so the form's expiry is a monitored alert, not a runbook
note.

Rules (all calendar-anchored, no rolling timer):
  - a form signed any time in year Y is valid through December 31 of Y+3;
  - T-90 days: WARNING alert; T-30 days: URGENT alert (email + dashboard);
  - expiry passed unrenewed: CRITICAL, escalated to a halt-on-sell
    recommendation — selling with a lapsed form withholds 24% of gross;
  - alerts may be snoozed, but never past T-7: inside the final week the
    alert always fires.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from ops.alerts import EMAIL_DASHBOARD, Alert, AlertSink

BACKUP_WITHHOLDING_RATE = 0.24     # plan §2.1 (TCJA; supersedes 28%)
T90_DAYS = 90
T30_DAYS = 30
SNOOZE_FLOOR_DAYS = 7              # cannot be snoozed past T-7

OK, T90, T30, EXPIRED = "OK", "T-90", "T-30", "EXPIRED"


def expiry_for_signature_year(year: int) -> date:
    """Calendar-anchored validity: December 31 of signature year + 3."""
    return date(year + 3, 12, 31)


def expiry_for(signed: date) -> date:
    return expiry_for_signature_year(signed.year)


@dataclass
class W8BenStatus:
    on: date
    expiry: date
    days_remaining: int
    state: str                     # OK | T-90 | T-30 | EXPIRED
    suppressed_by_snooze: bool


class W8BenMonitor:
    def __init__(self, signed: date, sink: AlertSink,
                 renewed_on: date = None, snoozed_until: date = None):
        self.signed = signed
        self.renewed_on = renewed_on
        self.snoozed_until = snoozed_until
        self.sink = sink

    @property
    def expiry(self) -> date:
        # a renewal restarts the calendar anchor from its own year
        return expiry_for(self.renewed_on or self.signed)

    def renew(self, on: date):
        self.renewed_on = on
        self.snoozed_until = None

    def snooze(self, until: date, on: date):
        """Suppress T-90/T-30 alerts until `until` — but never past T-7, and
        never an expired form."""
        if on > self.expiry:
            raise ValueError("an expired W-8BEN cannot be snoozed")
        floor = self.expiry - timedelta(days=SNOOZE_FLOOR_DAYS)
        if until > floor:
            raise ValueError(
                f"snooze until {until} would extend past T-7 ({floor}); "
                f"the final week before expiry always alerts")
        self.snoozed_until = until

    def status(self, on: date) -> W8BenStatus:
        days = (self.expiry - on).days
        state = (EXPIRED if days < 0 else
                 T30 if days <= T30_DAYS else
                 T90 if days <= T90_DAYS else OK)
        suppressed = (self.snoozed_until is not None and on <= self.snoozed_until
                      and state in (T90, T30))
        return W8BenStatus(on=on, expiry=self.expiry, days_remaining=days,
                           state=state, suppressed_by_snooze=suppressed)

    def check(self, on: date):
        """Evaluate the form on date `on`; emit the specified alert (if any)
        to the sink and return the alerts raised."""
        st = self.status(on)
        if st.state == OK or st.suppressed_by_snooze:
            return []
        if st.state == EXPIRED:
            msg = (f"W-8BEN expired {st.expiry.isoformat()} and is unrenewed — "
                   f"HALT-ON-SELL recommended: selling now exposes gross "
                   f"proceeds to {BACKUP_WITHHOLDING_RATE:.0%} backup "
                   f"withholding (plan §2.2)")
            severity = "CRITICAL"
        else:
            msg = (f"W-8BEN expires {st.expiry.isoformat()} "
                   f"({st.days_remaining} days, {st.state}) — renew before "
                   f"lapse; a lapsed form exposes gross proceeds to "
                   f"{BACKUP_WITHHOLDING_RATE:.0%} backup withholding")
            severity = "URGENT" if st.state == T30 else "WARNING"
        alert = Alert(service="w8ben_monitor", severity=severity, message=msg,
                      channels=EMAIL_DASHBOARD, raised_on=on.isoformat(),
                      details={"expiry": st.expiry.isoformat(),
                               "days_remaining": st.days_remaining,
                               "state": st.state,
                               "halt_on_sell": st.state == EXPIRED})
        return [self.sink.emit(alert)]
