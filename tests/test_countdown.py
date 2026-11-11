"""倒数日：天数是派生值，绝不落库。"""
from __future__ import annotations

import pytest

from qingdan.core.registry import QdError, call

TODAY = "2026-11-05"


def _cd(**kw) -> str:
    return call("countdown.create", **{"title": "统考", "target_date": "2026-11-25", **kw})["id"]


def _one(cid: str) -> dict:
    """取单个倒数日的呈现结果（没有 countdown.get，用 list 过滤）。"""
    items = [x for x in call("countdown.list", include_deleted=True)["items"] if x["id"] == cid]
    assert len(items) == 1, f"没找到 {cid}"
    return items[0]


def test_days_left_is_derived_not_stored():
    cid = _cd(target_date="2026-11-15")
    out = call("countdown.list", today=TODAY)["items"][0]
    assert out["days"] == 10
    assert "days" not in out["data"], "天数存在 data 里第二天就过期了"


def test_days_recompute_when_today_changes():
    cid = _cd(target_date="2026-11-15")
    assert call("countdown.list", today=TODAY)["items"][0]["days"] == 10
    assert call("countdown.list", today="2026-11-20")["items"][0]["days"] == -5


def test_passed_days_direction():
    """后端只给 direction + days：「已过 35 天」是界面文案，写进响应体英文界面就会漏中文。"""
    _cd(target_date="2026-10-01")
    out = call("countdown.list", today=TODAY)["items"][0]
    assert out["direction"] == "past" and out["days"] == -35
    assert "label" not in out


def test_today_label():
    _cd(target_date=TODAY)
    out = call("countdown.list", today=TODAY)["items"][0]
    assert out["direction"] == "today" and out["days"] == 0


def test_list_sorts_by_absolute_days():
    _cd(title="远", target_date="2027-06-01")
    _cd(title="近", target_date="2026-11-08")
    _cd(title="已过", target_date="2026-11-04")
    titles = [x["title"] for x in call("countdown.list", today=TODAY)["items"]]
    assert titles == ["已过", "近", "远"], "按剩余天数绝对值排，最近的（含刚过）在最前"


def test_invalid_target_date_rejected():
    with pytest.raises(QdError) as e:
        call("countdown.create", title="x", target_date="下个月某天")
    assert e.value.code == "VALIDATION_ERROR"


def test_invalid_count_mode_rejected():
    with pytest.raises(QdError):
        call("countdown.create", title="x", target_date=TODAY, count_mode="hours")


def test_remind_days_before_range():
    with pytest.raises(QdError):
        call("countdown.create", title="x", target_date=TODAY, remind_days_before=999)


def test_weeks_and_years_modes():
    cid = _cd(target_date="2026-12-31", count_mode="weeks")
    out = call("countdown.list", today=TODAY)["items"][0]
    assert out["weeks"] == round(56 / 7, 1)

    call("countdown.update", item_id=cid, count_mode="years", target_date="2020-01-01")
    out = call("countdown.list", today=TODAY)["items"][0]
    assert out["years"] is not None and out["years"] > 6


def test_show_age_for_birthday():
    cid = _cd(title="母亲生日", target_date="1968-12-10", show_age=True)
    out = call("countdown.list", today=TODAY)["items"][0]
    assert out["age"] == 57


def test_update_and_delete_restore():
    cid = _cd()
    assert call("countdown.update", item_id=cid, title="改成考试")["data"]["title"] == "改成考试"
    with pytest.raises(QdError):
        call("countdown.update", item_id=cid, title="   ")
    call("countdown.delete", item_id=cid)
    assert call("countdown.list")["total"] == 0
    assert call("countdown.list", include_deleted=True)["total"] == 1
    call("countdown.restore", item_id=cid)
    assert call("countdown.list")["total"] == 1


def test_blank_title_rejected():
    with pytest.raises(QdError):
        call("countdown.create", title="   ", target_date=TODAY)


def test_repeat_yearly_flag_persists():
    cid = _cd(repeat_yearly=True)
    assert _one(cid)["data"]["repeat_yearly"] is True


def test_update_can_clear_repeat_and_change_mode():
    cid = _cd(repeat_yearly=True, count_mode="days")
    out = call("countdown.update", item_id=cid, repeat_yearly=False, count_mode="weeks")
    assert out["data"]["repeat_yearly"] is False and out["data"]["count_mode"] == "weeks"
