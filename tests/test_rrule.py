"""RRULE 子集的单元测试。

重点全在边界上：月末夹取、闰年、BYDAY 跨周、INTERVAL 对齐、COUNT 锚定、UNTIL 截断、
畸形规则拒收。随机生成式的不变量覆盖在 `test_rrule_props.py`，两边刻意不重复。
"""
from __future__ import annotations

from datetime import date, datetime

import pytest

from qingdan.core import rrule


# --------------------------------------------------------------------------- 解析


def test_short_canon_equals_freq_form():
    assert rrule.parse("daily") == rrule.parse("FREQ=DAILY")
    assert rrule.parse("weekly").freq == "WEEKLY"
    assert rrule.parse("monthly").interval == 1
    assert rrule.parse("yearly").freq == "YEARLY"


def test_parse_reads_all_supported_parts():
    r = rrule.parse("FREQ=WEEKLY;INTERVAL=2;BYDAY=MO,TH;UNTIL=20261231")
    assert r.freq == "WEEKLY"
    assert r.interval == 2
    assert r.byday == (0, 3)                       # 周一=0、周四=3，且升序
    assert r.until == date(2026, 12, 31)
    assert r.count is None


def test_parse_accepts_negative_bymonthday_as_last_day():
    r = rrule.parse("FREQ=MONTHLY;BYMONTHDAY=1,-1")
    assert r.bymonthday == (-1, 1) or r.bymonthday == (1, -1)
    # 2 月：1 号与最后一天（平年 28）
    assert list(rrule.occurrences("2026-02-05", r, limit=4))[:3] == [
        date(2026, 2, 28), date(2026, 3, 1), date(2026, 3, 31)]


@pytest.mark.parametrize("bad", [
    "", "   ", "FREQ=HOURLY", "FREQ=DAILY;BYSETPOS=2", "FREQ=DAILY;INTERVAL=0",
    "FREQ=DAILY;INTERVAL=x", "FREQ=DAILY;COUNT=0", "FREQ=DAILY;UNTIL=20261231;COUNT=3",
    "FREQ=WEEKLY;BYDAY=XX", "FREQ=DAILY;BYDAY=MO", "FREQ=DAILY;BYMONTHDAY=0",
    "FREQ=DAILY;FREQ=WEEKLY", "WEEKLYX", "每天", "FREQ=DAILY;UNTIL=notadate", "garbage",
])
def test_parse_rejects_unsupported_or_malformed(bad):
    with pytest.raises(rrule.RuleError):
        rrule.parse(bad)


def test_short_forms_are_case_insensitive():
    """`WEEKLY` / `Weekly` 都收 —— 界面与手写都可能大写，拒收只会逼用户猜。"""
    assert rrule.parse("WEEKLY").freq == "WEEKLY"
    assert rrule.parse("Weekly").freq == "WEEKLY"


def test_try_parse_never_raises():
    """视图层专用：另一台机器同步来的垃圾规则不许把日历带崩。"""
    assert rrule.try_parse("FREQ=HOURLY") is None
    assert rrule.try_parse(None) is None
    assert rrule.try_parse("") is None
    assert rrule.try_parse("FREQ=DAILY").freq == "DAILY"


# --------------------------------------------------------------------------- 推进


def test_next_after_preserves_time_of_day():
    got = rrule.next_after("2026-11-09T20:30:00", rrule.parse("daily"))
    assert got == datetime(2026, 11, 10, 20, 30)


def test_next_after_accepts_datetime_and_date():
    r = rrule.parse("weekly")
    assert rrule.next_after(datetime(2026, 11, 9), r).date() == date(2026, 11, 16)
    assert rrule.next_after(date(2026, 11, 9), r).date() == date(2026, 11, 16)


def test_month_end_clamps_instead_of_overflowing():
    """1 月 31 日 + 每月 → 2 月 28 日，绝不变成 3 月 3 日。

    溢出会让「每月最后一天还款」这类任务悄悄漂到月初，是最难被用户察觉的一类错。
    """
    r = rrule.parse("monthly")
    seq = [rrule.next_after(d, r) for d in ["2026-01-31"]]
    assert seq[0].date() == date(2026, 2, 28)
    # 从夹取后的 2/28 再推进：回到 3 月 28 日（锚是 28，不是 31）—— 这是「以当前值为锚」的
    # 既定语义；要每月都咬 31 号请用 BYMONTHDAY=31 或 -1
    assert rrule.next_after(seq[0], r).date() == date(2026, 3, 28)


def test_explicit_bymonthday_skips_months_lacking_that_day():
    """RFC 5545 语义：显式写了 31 号，2 月就**不产生**这一次，而不是夹到 28 号。

    与「隐式锚日夹取」的区别是有意的：用户明确指定了日号，替他改成 28 号是篡改意图；
    想要每月最后一天请用 -1。
    """
    r = rrule.parse("FREQ=MONTHLY;BYMONTHDAY=31")
    assert rrule.next_after("2026-01-31", r).date() == date(2026, 3, 31)
    assert rrule.next_after("2026-03-31", r).date() == date(2026, 5, 31)   # 4 月也没有 31 号


def test_bymonthday_minus_one_is_always_last_day():
    r = rrule.parse("FREQ=MONTHLY;BYMONTHDAY=-1")
    got = [rrule.next_after(d, r).date() for d in ["2026-01-15", "2026-02-28", "2026-03-31"]]
    assert got == [date(2026, 1, 31), date(2026, 3, 31), date(2026, 4, 30)]


def test_bymonthday_minus_one_lands_on_leap_day():
    r = rrule.parse("FREQ=MONTHLY;BYMONTHDAY=-1")
    assert rrule.next_after("2028-01-31", r).date() == date(2028, 2, 29)


def test_leap_day_yearly_falls_back_to_feb_28():
    r = rrule.parse("yearly")
    assert rrule.next_after("2024-02-29", r).date() == date(2025, 2, 28)
    assert rrule.next_after("2027-02-28", r).date() == date(2028, 2, 28)   # 闰年回到 29 号


def test_weekly_byday_lists_within_and_across_weeks():
    r = rrule.parse("FREQ=WEEKLY;BYDAY=MO,WE,FR")
    got = [d.date() for d in rrule.upcoming("2026-11-09", r, 4)]   # 2026-11-09 是周一
    assert got == [date(2026, 11, 11), date(2026, 11, 13),
                   date(2026, 11, 16), date(2026, 11, 18)]


def test_interval_alignment_follows_anchor():
    r = rrule.parse("FREQ=DAILY;INTERVAL=3")
    got = [d.date() for d in rrule.upcoming("2026-11-01", r, 3, anchor="2026-11-01")]
    assert got == [date(2026, 11, 4), date(2026, 11, 7), date(2026, 11, 10)]


def test_count_is_anchored_not_sliding():
    """COUNT 从锚点数起。锚跟着 due 走的话 COUNT 永远数不到头。"""
    r = rrule.parse("FREQ=DAILY;COUNT=3")
    got = rrule.upcoming("2026-11-01", r, 10, anchor="2026-11-01")
    assert [d.date() for d in got] == [date(2026, 11, 2), date(2026, 11, 3)]
    assert rrule.next_after("2026-11-03", r, anchor="2026-11-01") is None


def test_until_stops_the_rule():
    r = rrule.parse("FREQ=DAILY;UNTIL=2026-11-11")
    assert rrule.next_after("2026-11-10", r).date() == date(2026, 11, 11)
    assert rrule.next_after("2026-11-11", r) is None


def test_upcoming_respects_end_horizon():
    r = rrule.parse("daily")
    got = rrule.upcoming("2026-11-09", r, 100, end="2026-11-12")
    assert [d.date() for d in got] == [date(2026, 11, 10), date(2026, 11, 11), date(2026, 11, 12)]


def test_upcoming_zero_or_negative_count_is_empty():
    assert rrule.upcoming("2026-11-09", rrule.parse("daily"), 0) == []
    assert rrule.upcoming("2026-11-09", rrule.parse("daily"), -3) == []


def test_anchor_later_than_due_is_ignored():
    """用户把到期日改到锚之前（锚没跟着改的老数据）时不许算出负数步。"""
    r = rrule.parse("daily")
    assert rrule.next_after("2026-11-09", r, anchor="2026-12-01").date() == date(2026, 11, 10)


def test_timezone_input_is_folded_to_local_wall_clock():
    r = rrule.parse("daily")
    got = rrule.next_after("2026-11-09T20:00:00+00:00", r)
    assert got.tzinfo is None                        # 与 view.parse_dt 同口径：折算后去时区


# --------------------------------------------------------------------------- 展示


def test_summary_returns_machine_keys_only():
    assert rrule.summary(rrule.parse("daily")) == {"key": "rp_daily", "interval": 1}
    s = rrule.summary(rrule.parse("FREQ=WEEKLY;INTERVAL=2;BYDAY=MO,TH"))
    assert s["key"] == "rp_every_n_weeks" and s["n"] == 2
    assert s["days"] == ["wd_mo", "wd_th"]
    # 后端只给机器键，绝不下发中文（英文界面残留中文是实测踩过的整族 bug）
    assert not any(isinstance(v, str) and any("\u4e00" <= ch <= "\u9fff" for ch in v)
                   for v in s.values())


def test_summary_of_garbage_is_empty_not_crash():
    assert rrule.summary("FREQ=HOURLY") == {}
    assert rrule.summary(None) == {}
