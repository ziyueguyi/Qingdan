"""Versioned, offline mainland-China work calendars.

Snapshots are immutable protocol data: a future release adds a new ID rather
than changing an existing one. Rules will carry that ID so two clients cannot
silently compute different dates. Unknown years are errors, including weekends:
an unpublished adjustment could turn a weekend into a working day.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from types import MappingProxyType


class CalendarUnavailable(ValueError):
    def __init__(self, calendar_id: str, year: int):
        self.calendar_id = calendar_id
        self.year = year
        super().__init__(f"工作日历 {calendar_id} 未收录 {year} 年，请更新日历或修改规则")


@dataclass(frozen=True)
class YearCalendar:
    year: int
    published: str
    notice: str
    source: str
    days_off: frozenset[date]
    working_weekends: frozenset[date]


def _year(year: int, published: str, notice: str, source: str,
          ranges: tuple[tuple[str, str], ...], working: tuple[str, ...]) -> YearCalendar:
    off: set[date] = set()
    for first, last in ranges:
        start = date.fromisoformat(f"{year}-{first}")
        end = date.fromisoformat(f"{year}-{last}")
        if start > end:
            raise ValueError("Invalid holiday range")
        off.update(start + timedelta(days=i) for i in range((end - start).days + 1))
    work = frozenset(date.fromisoformat(f"{year}-{d}") for d in working)
    if off & work or any(d.weekday() < 5 for d in work):
        raise ValueError("Invalid adjusted working weekend")
    return YearCalendar(year, published, notice, source, frozenset(off), work)


CALENDAR_ID = "CN-20261105"
_YEARS = MappingProxyType({
    2026: _year(2026, "2025-11-04", "GBFMD-2025-7",
        "https://www.beijing.gov.cn/cs/gncs/zcwj/202603/t20260327_4568275.html",
        (("01-01", "01-03"), ("02-15", "02-23"), ("04-04", "04-06"),
         ("05-01", "05-05"), ("06-19", "06-21"), ("09-25", "09-27"),
         ("10-01", "10-07")),
        ("01-04", "02-14", "02-28", "05-09", "09-20", "10-10")),
    2027: _year(2027, "2026-11-05", "GBFMD-2026-10",
        "https://www.forestry.gov.cn/lyj/1/szxx/20261105/695102.html",
        (("01-01", "01-03"), ("02-05", "02-13"), ("04-03", "04-05"),
         ("05-01", "05-05"), ("06-09", "06-09"), ("09-15", "09-15"),
         ("10-01", "10-07")),
        ("01-31", "02-14", "05-08", "09-26", "10-09")),
})


def calendar_year(year: int, calendar_id: str = CALENDAR_ID) -> YearCalendar:
    if calendar_id != CALENDAR_ID or year not in _YEARS:
        raise CalendarUnavailable(calendar_id, year)
    return _YEARS[year]


def is_workday(day: date, calendar_id: str = CALENDAR_ID) -> bool:
    record = calendar_year(day.year, calendar_id)
    return day in record.working_weekends or (day.weekday() < 5 and day not in record.days_off)


def on_or_after(day: date, calendar_id: str = CALENDAR_ID) -> date:
    """First known workday on/after day; never guess beyond calendar coverage."""
    for _ in range(366):
        if is_workday(day, calendar_id):
            return day
        day += timedelta(days=1)
    raise ValueError("Workday search exceeded one year")


def metadata(calendar_id: str = CALENDAR_ID) -> dict:
    # Resolve through the same validation path; callers cannot advertise an
    # unknown snapshot as if its metadata were known.
    records = [calendar_year(year, calendar_id) for year in sorted(_YEARS)]
    return {"id": calendar_id, "region": "CN", "years": [r.year for r in records],
            "sources": [{"year": r.year, "published": r.published,
                         "notice": r.notice, "url": r.source} for r in records]}
