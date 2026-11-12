from datetime import date

import pytest

from qingdan.core import rrule, workdays
from qingdan.core.registry import QdError, call


def rule(freq="DAILY", policy="SKIP", end="COUNT=3"):
    return f"FREQ={freq};{end};X-QD-WORKDAY={policy};X-QD-CALENDAR={workdays.CALENDAR_ID}"


def test_skip_counts_effective_days_including_adjusted_sunday():
    assert list(rrule.occurrences("2026-01-01", rrule.parse(rule()))) == [
        date(2026, 1, 4), date(2026, 1, 5), date(2026, 1, 6)]


def test_next_collisions_count_once_and_preserve_nominal_month_anchor():
    assert list(rrule.occurrences("2026-02-15", rrule.parse(rule(policy="NEXT")))) == [
        date(2026, 2, 24), date(2026, 2, 25), date(2026, 2, 26)]
    assert list(rrule.occurrences("2026-01-31", rrule.parse(rule("MONTHLY", "NEXT")))) == [
        date(2026, 2, 2), date(2026, 2, 28), date(2026, 3, 31)]


def test_until_limits_effective_date_and_weekly_does_not_add_unselected_days():
    assert list(rrule.occurrences("2026-01-01", rrule.parse(rule(policy="NEXT", end="UNTIL=20260103")))) == []
    r = rrule.parse(rule("WEEKLY", end="BYDAY=MO,TU,WE,TH,FR;COUNT=2"))
    assert list(rrule.occurrences("2026-01-01", r)) == [date(2026, 1, 5), date(2026, 1, 6)]


@pytest.mark.parametrize("suffix", [
    "X-QD-WORKDAY=SKIP", "X-QD-CALENDAR=CN-20261105",
    "X-QD-WORKDAY=OTHER;X-QD-CALENDAR=CN-20261105",
    "X-QD-WORKDAY=NEXT;X-QD-CALENDAR=CN-UNKNOWN",
])
def test_reject_incomplete_or_unknown_calendar_rule(suffix):
    with pytest.raises(rrule.RuleError):
        rrule.parse("FREQ=DAILY;" + suffix)


def test_unknown_year_is_not_end_of_rule_and_preview_can_return_partial():
    r = rrule.parse(rule(end="INTERVAL=1"))
    with pytest.raises(rrule.CalendarError):
        rrule.next_after("2027-12-31", r)
    warnings = []
    dates = rrule.upcoming("2027-12-30", r, n=5, warnings=warnings)
    assert [d.date() for d in dates] == [date(2027, 12, 31)]
    assert warnings[0]["year"] == 2028
    assert rrule.upcoming("2027-12-30", r, end="2027-12-31") == dates


def test_create_aligns_first_due_and_count_ends_without_drift(app_ctx):
    task = call("task.create", title="节假日", due_at="2026-02-15T09:30:00", repeat=rule(policy="NEXT"))
    tid = task["id"]
    assert task["data"]["due_at"] == "2026-02-24T09:30:00"
    assert task["data"]["repeat_start"] == "2026-02-15T09:30:00"
    # UI re-submits unchanged fields on ordinary edits. Preserve the anchor.
    edited = call("task.update", item_id=tid, title="新标题", repeat=task["data"]["repeat"], due_at=task["data"]["due_at"])
    assert edited["data"]["repeat_start"] == task["data"]["repeat_start"]
    for expected in ("2026-02-25T09:30:00", "2026-02-26T09:30:00"):
        done = call("task.complete", item_id=tid)
        assert done["data"]["due_at"] == expected
        assert done["data"]["status"] == "todo"
    assert call("task.complete", item_id=tid)["data"]["status"] == "done"
    assert call("task.repeat_info", item_id=tid)["history"]["done"] == 3


def test_missing_year_cannot_complete_or_record_history(app_ctx):
    task = call("task.create", title="跨年", due_at="2027-12-31", repeat=rule(end="COUNT=2"))
    with pytest.raises(QdError, match="2028"):
        call("task.complete", item_id=task["id"])
    after = call("task.get", item_id=task["id"])
    assert after == task
    info = call("task.repeat_info", item_id=task["id"])
    assert info["warnings"][0]["year"] == 2028
    assert info["history"]["done"] == 0


def test_synced_unknown_snapshot_cannot_be_completed(app_ctx):
    task = call("task.create", title="新版本日历", due_at="2027-12-31")
    item = app_ctx.store.get(task["id"])
    item.data["repeat"] = rule().replace(workdays.CALENDAR_ID, "CN-FUTURE")
    app_ctx.store.put(item)
    before = call("task.get", item_id=item.id)
    with pytest.raises(QdError, match="更新"):
        call("task.complete", item_id=item.id)
    assert call("task.get", item_id=item.id) == before


def test_explicit_schedule_and_repeat_changes_align_and_reminders_move(app_ctx):
    task = call("task.create", title="安排", due_at="2026-02-14")
    tid = task["id"]
    call("task.set_reminder", item_id=tid, remind_at=["2026-02-13T18:00:00"])
    call("task.set_repeat", item_id=tid, repeat=rule(policy="NEXT"))
    out = call("task.set_due", item_id=tid, due_at="2026-02-15", all_day=True)
    assert out["data"]["due_at"] == "2026-02-24"
    assert out["data"]["repeat_start"] == "2026-02-15"
    assert out["data"]["remind_at"] == ["2026-02-22T18:00:00"]


def test_preview_includes_effective_first_date_and_provenance(app_ctx):
    out = call("task.repeat_preview", repeat=rule(), due_at="2026-01-01")
    assert out["first_due_at"] == "2026-01-04"
    assert out["upcoming"] == ["2026-01-05", "2026-01-06"]
    assert out["calendar"]["id"] == workdays.CALENDAR_ID
    with pytest.raises(QdError):
        call("task.create", title="无符合日期", due_at="2026-01-01", repeat=rule(end="UNTIL=20260103"))
