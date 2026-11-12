from datetime import date, timedelta

import pytest

from qingdan.core import workdays


@pytest.mark.parametrize("year,off_count,work_count", [(2026, 33, 6), (2027, 29, 5)])
def test_calendar_partition_and_adjustments(year, off_count, work_count):
    record = workdays.calendar_year(year)
    assert len(record.days_off) == off_count
    assert len(record.working_weekends) == work_count
    assert not record.days_off & record.working_weekends
    start = date(year, 1, 1)
    for i in range((date(year + 1, 1, 1) - start).days):
        day = start + timedelta(days=i)
        expected = day in record.working_weekends or (day.weekday() < 5 and day not in record.days_off)
        assert workdays.is_workday(day) == expected
        if day in record.days_off:
            assert not workdays.is_workday(day)
        if day in record.working_weekends:
            assert workdays.is_workday(day)


@pytest.mark.parametrize("start,result", [
    ("2026-01-01", "2026-01-04"),  # adjusted Sunday
    ("2026-02-15", "2026-02-24"),  # Spring Festival
    ("2026-10-01", "2026-10-08"),
    ("2026-10-10", "2026-10-10"),  # adjusted Saturday
    ("2027-01-01", "2027-01-04"),
    ("2027-02-05", "2027-02-14"),  # adjusted Sunday
    ("2027-06-09", "2027-06-10"),
    ("2027-12-31", "2027-12-31"),
])
def test_next_workday_uses_official_adjustments(start, result):
    assert workdays.on_or_after(date.fromisoformat(start)).isoformat() == result


@pytest.mark.parametrize("day", [date(2025, 12, 31), date(2028, 1, 1), date(2028, 1, 3)])
def test_unknown_year_fails_for_weekends_and_weekdays(day):
    with pytest.raises(workdays.CalendarUnavailable) as caught:
        workdays.on_or_after(day)
    assert caught.value.year == day.year


def test_snapshot_is_explicit_and_metadata_has_provenance():
    with pytest.raises(workdays.CalendarUnavailable):
        workdays.is_workday(date(2026, 1, 4), "CN-FUTURE")
    record = workdays.calendar_year(2026)
    with pytest.raises(AttributeError):
        record.days_off.add(date(2026, 1, 4))
    info = workdays.metadata()
    assert info["id"] == workdays.CALENDAR_ID
    assert info["years"] == [2026, 2027]
    assert all("gov.cn/" in row["url"] for row in info["sources"])
