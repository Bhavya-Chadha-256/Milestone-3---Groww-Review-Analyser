from datetime import UTC, date, datetime

import pytest

from pulse.timeutil import (
    Window,
    format_range,
    iso_week_of,
    resolve_window,
    today_in_tz,
    window_bounds_utc,
    window_for_week,
)

IST = "Asia/Kolkata"


def test_resolve_window_on_monday() -> None:
    window = resolve_window(date(2026, 10, 19), weeks=12, timezone=IST)
    assert (window.start, window.end, window.iso_week) == (date(2026, 7, 27), date(2026, 10, 18), "2026-W42")
    assert window.latest_week_start == date(2026, 10, 12)


def test_resolve_window_on_sunday_uses_previous_complete_week() -> None:
    window = resolve_window(date(2026, 10, 18), weeks=12, timezone=IST)
    assert (window.end, window.iso_week) == (date(2026, 10, 11), "2026-W41")


def test_iso_week_uses_ist_boundary() -> None:  # AGG-1
    assert iso_week_of(datetime(2026, 10, 18, 18, 0, tzinfo=UTC), IST) == "2026-W42"  # 23:30 IST Sunday
    assert iso_week_of(datetime(2026, 10, 18, 19, 0, tzinfo=UTC), IST) == "2026-W43"  # 00:30 IST Monday


def test_iso_week_53_window() -> None:  # AGG-6
    window = resolve_window(date(2027, 1, 4), 12, IST)
    assert (window.iso_week, window.start, window.end) == ("2026-W53", date(2026, 10, 12), date(2027, 1, 3))
    assert window_for_week("2026-W53", 12, IST) == window


def test_iso_year_differs_from_calendar_year() -> None:  # AGG-6
    assert iso_week_of(datetime(2025, 12, 29, 6, 0, tzinfo=UTC), IST) == "2026-W01"


def test_window_for_week_matches_resolve() -> None:
    assert window_for_week("2026-W42", 12, IST) == resolve_window(date(2026, 10, 19), 12, IST)


@pytest.mark.parametrize("bad", ["2026-42", "2026-W54", "2025-W53", "W42", "2026-W00"])
def test_window_for_week_rejects_malformed(bad: str) -> None:  # OPS-2
    with pytest.raises(ValueError):
        window_for_week(bad, 12, IST)


def test_window_bounds_are_ist_midnights_in_utc() -> None:
    start, end = window_bounds_utc(resolve_window(date(2026, 10, 19), 12, IST))
    assert start == datetime(2026, 7, 26, 18, 30, tzinfo=UTC)
    assert end == datetime(2026, 10, 18, 18, 30, tzinfo=UTC)


def test_today_in_tz_uses_ist() -> None:  # OPS-8
    assert today_in_tz(IST, datetime(2026, 10, 18, 19, 30, tzinfo=UTC)) == date(2026, 10, 19)


def test_format_range_shows_both_years_only_when_they_differ() -> None:
    assert format_range(date(2026, 7, 27), date(2026, 10, 18)) == "27 Jul – 18 Oct 2026"
    assert format_range(date(2026, 10, 12), date(2027, 1, 3)) == "12 Oct 2026 – 3 Jan 2027"


def test_window_is_frozen() -> None:
    window = Window(start=date(2026, 7, 27), end=date(2026, 10, 18), iso_week="2026-W42", timezone=IST)
    with pytest.raises(AttributeError):
        window.iso_week = "2026-W01"  # type: ignore[misc]
