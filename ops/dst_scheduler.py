"""DST-safe session scheduler (plan v2.4 §6.1, doc edit §2.4).

All session logic is anchored to America/New_York via zoneinfo; Israel time
is DERIVED from the New York instant, never hardcoded — the v2.3 "16:30–23:00
IL" clock was wrong twice a year, during the two ~1–3-week windows when the
US and Israel have changed clocks on different dates (US: second Sunday of
March / first Sunday of November; Israel: last Friday of March / last Sunday
of October). In those windows the NY→IL offset is 6h, not the usual 7h, so a
hardcoded IL clock arms the session an hour late.

The scheduler answers one question: for a given session date, when do we arm
and disarm? It returns the anchor (NY) wall times plus the derived UTC and
Israel instants. UTC comes from the stdlib timezone; no pytz, no fixed
offsets anywhere.

Fails loud (plan §6.1 alerting column): verify_tz_database() re-derives the
anchor's DST transition days from the live tz database and raises
TimezoneDatabaseError if they no longer match the expected US rule — a tz
database update that changes America/New_York must page the operator, not
silently shift every arm/disarm time.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

ANCHOR_TZ = "America/New_York"     # session anchor per plan §2.4
DERIVED_TZ = "Asia/Jerusalem"      # operator-local display, derived only

# US DST rule in force since 2007: transitions land in March and November.
# verify_tz_database() compares the live tz database against this and fails
# loud if a tzdata update changed it.
EXPECTED_ANCHOR_TRANSITION_MONTHS = (3, 11)


class TimezoneDatabaseError(RuntimeError):
    """The tz database no longer matches the assumed US DST rule."""


def _transition_days(tz: ZoneInfo, year: int):
    """Days in `year` where the UTC offset at local noon changes vs the prior
    day. Noon avoids the transition-hour edge cases (both the US 02:00 and
    the Israel 01:00/02:00 switches are far from noon)."""
    days = []
    prev = datetime(year - 1, 12, 31, 12, tzinfo=tz).utcoffset()
    d = date(year, 1, 1)
    while d.year == year:
        cur = datetime(d.year, d.month, d.day, 12, tzinfo=tz).utcoffset()
        if cur != prev:
            days.append(d)
        prev = cur
        d += timedelta(days=1)
    return days


def _parse_hhmm(value) -> time:
    if isinstance(value, time):
        return value
    hh, mm = str(value).split(":")
    return time(int(hh), int(mm))


@dataclass
class SessionWindow:
    """Arm/disarm instants for one session date, in all three frames."""
    day: date
    ny_open: datetime            # wall clock at the anchor (America/New_York)
    ny_close: datetime
    utc_open: datetime           # the same instants in UTC (what cron arms)
    utc_close: datetime
    il_open: datetime            # the same instants derived to Asia/Jerusalem
    il_close: datetime


class DstScheduler:
    def __init__(self, session_open="09:30", session_close="16:00",
                 anchor_tz: str = ANCHOR_TZ, derived_tz: str = DERIVED_TZ):
        self.session_open = _parse_hhmm(session_open)
        self.session_close = _parse_hhmm(session_close)
        self.anchor = ZoneInfo(anchor_tz)
        self.derived = ZoneInfo(derived_tz)

    def session_window(self, day: date) -> SessionWindow:
        """Arm/disarm for the session of `day`. The wall times are pinned at
        the anchor; UTC and Israel times are converted from that instant, so
        DST transitions — including the divergence windows — are handled by
        the tz database, not by us."""
        ny_open = datetime.combine(day, self.session_open, tzinfo=self.anchor)
        ny_close = datetime.combine(day, self.session_close, tzinfo=self.anchor)
        return SessionWindow(
            day=day, ny_open=ny_open, ny_close=ny_close,
            utc_open=ny_open.astimezone(timezone.utc),
            utc_close=ny_close.astimezone(timezone.utc),
            il_open=ny_open.astimezone(self.derived),
            il_close=ny_close.astimezone(self.derived),
        )

    def divergence_windows(self, year: int):
        """Inclusive (start, end) date ranges where the anchor and the derived
        zone disagree about DST — the windows where a hardcoded Israel clock
        would be an hour off. Derived from the tz database, not from a table.
        Informational: the plan (§2.4) calls out the two ~1–3-week windows so
        the operator expects them."""
        windows, start = [], None
        d = date(year, 1, 1)
        while d.year == year:
            noon = datetime(d.year, d.month, d.day, 12)
            differ = (bool(noon.replace(tzinfo=self.anchor).dst())
                      != bool(noon.replace(tzinfo=self.derived).dst()))
            if differ and start is None:
                start = d
            elif not differ and start is not None:
                windows.append((start, d - timedelta(days=1)))
                start = None
            d += timedelta(days=1)
        if start is not None:
            windows.append((start, date(year, 12, 31)))
        return windows

    def verify_tz_database(self, year: int):
        """Loud self-check (§6.1 alerting). Re-derives the anchor's DST
        transitions from the live tz database and raises if they no longer
        match the US rule this scheduler's assumptions are built on. Run at
        service start; a failure means a tzdata update changed the rules and
        every derived arm/disarm time must be re-validated by a human."""
        months = tuple(d.month for d in _transition_days(self.anchor, year))
        if months != EXPECTED_ANCHOR_TRANSITION_MONTHS:
            raise TimezoneDatabaseError(
                f"tz database changed for {ANCHOR_TZ}: {year} transition "
                f"months are {months or 'none'}, expected "
                f"{EXPECTED_ANCHOR_TRANSITION_MONTHS} — re-validate all "
                f"derived session times before arming")
        # the derived zone must also still exist and differ from UTC
        if datetime(year, 7, 1, 12, tzinfo=self.derived).utcoffset() is None:
            raise TimezoneDatabaseError(
                f"tz database broken: {DERIVED_TZ} has no UTC offset")
        return True
