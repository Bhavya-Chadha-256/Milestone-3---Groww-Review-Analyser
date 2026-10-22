"""Reporting windows and ISO weeks, always computed in the configured local timezone (IST).

A run reports on the latest *complete* ISO week (Monday–Sunday) before `today`, plus the
preceding weeks that make up the trailing window (architecture.md §3.1, edgecase AGG-1/AGG-6).
"""

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

ISO_WEEK_PATTERN = re.compile(r"^(\d{4})-W(\d{2})$")


@dataclass(frozen=True)
class Window:
    start: date  # first day (a Monday), inclusive
    end: date  # last day (a Sunday), inclusive
    iso_week: str  # the reported week, e.g. "2026-W42"
    timezone: str

    @property
    def latest_week_start(self) -> date:
        return self.end - timedelta(days=6)


def iso_week_label(day: date) -> str:
    year, week, _ = day.isocalendar()
    return f"{year}-W{week:02d}"


def resolve_window(today: date, weeks: int, timezone: str) -> Window:
    """Window ending on the Sunday before `today`'s ISO week, spanning `weeks` full weeks."""
    this_monday = today - timedelta(days=today.weekday())
    end = this_monday - timedelta(days=1)
    start = end - timedelta(days=7 * weeks - 1)
    return Window(start=start, end=end, iso_week=iso_week_label(end), timezone=timezone)


def window_for_week(iso_week: str, weeks: int, timezone: str) -> Window:
    match = ISO_WEEK_PATTERN.match(iso_week)
    if not match:
        raise ValueError(f"week must look like 2026-W42, got {iso_week!r}")
    year, week = int(match.group(1)), int(match.group(2))
    try:
        monday = date.fromisocalendar(year, week, 1)
    except ValueError as error:
        raise ValueError(f"{iso_week} does not exist in ISO year {year}") from error
    return resolve_window(monday + timedelta(days=7), weeks, timezone)


def iso_week_of(timestamp: datetime, timezone: str) -> str:
    return iso_week_label(timestamp.astimezone(ZoneInfo(timezone)).date())


def window_bounds_utc(window: Window) -> tuple[datetime, datetime]:
    """Half-open [start, end) in UTC: local midnight at the window start and after its last day."""
    zone = ZoneInfo(window.timezone)
    start = datetime.combine(window.start, time.min, tzinfo=zone)
    end = datetime.combine(window.end + timedelta(days=1), time.min, tzinfo=zone)
    return start.astimezone(UTC), end.astimezone(UTC)


def today_in_tz(timezone: str, now: datetime) -> date:
    return now.astimezone(ZoneInfo(timezone)).date()


def format_range(start: date, end: date) -> str:
    """'27 Jul – 18 Oct 2026', or '12 Oct 2026 – 3 Jan 2027' when the years differ."""
    end_text = f"{end.day} {end:%b %Y}"
    if start.year == end.year:
        return f"{start.day} {start:%b} – {end_text}"
    return f"{start.day} {start:%b %Y} – {end_text}"
