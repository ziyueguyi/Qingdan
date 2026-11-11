"""视图类功能的接口层测试（纯逻辑在 test_view.py，这里验 call() 与参数校验）。"""
from __future__ import annotations

from datetime import datetime

import pytest

from qingdan.core import stamp
from qingdan.core.registry import QdError, call

TODAY = "2026-11-05"


@pytest.fixture(autouse=True)
def _clock_pinned_to_the_scripted_today(app_ctx):
    """整个文件的剧本是"今天是 2026-11-05"，那就必须连**写入侧**也过到这个日子。

    `stats.today` 数的是 `done_at`，而 `task.complete` 用墙钟写它。以前只传
    `today="2026-11-05"` 而不钉时钟，两条时间线只在"跑测试那天真是 11 月 5 日"时
    一致 —— 日历一跨午夜，`done_today` 就从 1 变 0（2026-11-06 凌晨实测挂掉）。
    """
    stamp.set_clock(lambda: datetime(2026, 11, 5, 20, 0, 0))
    yield
    stamp.set_clock(None)


def _t(**kw) -> str:
    return call("task.create", **{"title": "任务", **kw})["id"]


# --------------------------------------------------------------------------- view.query


def test_view_query_today_shape():
    _t(title="逾期活", due_at="2026-11-01T10:00:00")
    _t(title="今天活", due_at="2026-11-05T20:00:00")
    _t(title="没排期")
    out = call("view.query", name="today", today=TODAY, group="none")
    assert out["view"] == "today" and out["today"] == TODAY
    assert sorted(x["data"]["title"] for x in out["items"]) == ["今天活", "逾期活"]
    assert out["total"] == 2
    assert len(out["groups"]) == 1 and out["groups"][0]["label"] == ""


def test_view_query_groups_sum_equals_total():
    for i in range(6):
        _t(title=f"t{i}", due_at=f"2026-11-0{i % 3 + 3}T10:00:00", priority=(i % 4) + 1)
    for g in ("date", "group", "priority", "tag", "status", "created"):
        out = call("view.query", name="all", today=TODAY, group=g)
        assert sum(x["total"] for x in out["groups"]) == out["total"], g


def test_view_query_default_group_is_date():
    _t(due_at="2026-11-03T10:00:00")
    _t(due_at="2026-11-05T10:00:00")
    out = call("view.query", name="all", today=TODAY)
    assert [g["label_key"] for g in out["groups"]] == ["sec_overdue", "sec_today"], \
        "分组标题必须是 i18n 键，不能是中文文案"


def test_view_query_rejects_unknown_view():
    with pytest.raises(QdError) as e:
        call("view.query", name="四象限", today=TODAY)
    assert e.value.code == "VALIDATION_ERROR"
    assert "allowed" in e.value.detail


def test_view_query_rejects_unknown_group_and_sort():
    with pytest.raises(QdError):
        call("view.query", name="today", group="nonsense", today=TODAY)
    with pytest.raises(QdError):
        call("view.query", name="today", sort="nonsense", today=TODAY)


def test_view_query_filters_by_group_and_tag():
    gid = call("group.create", name="工作")["id"]
    _t(title="A", list_id=gid, due_at="2026-11-05T10:00:00", tags=["重要"])
    _t(title="B", due_at="2026-11-05T10:00:00")
    assert call("view.query", name="today", list_id=gid, today=TODAY)["total"] == 1
    assert call("view.query", name="today", tag="重要", today=TODAY)["total"] == 1
    assert call("view.query", name="today", tag="不存在", today=TODAY)["total"] == 0


def test_view_query_trash_and_done():
    tid = _t(title="删掉")
    call("task.delete", item_id=tid)
    done = _t(title="做完")
    call("task.complete", item_id=done)
    assert [x["data"]["title"] for x in call("view.query", name="trash", today=TODAY)["items"]] == ["删掉"]
    assert [x["data"]["title"] for x in call("view.query", name="done", today=TODAY)["items"]] == ["做完"]


# --------------------------------------------------------------------------- 日历与看板


def test_view_calendar_month_grid():
    _t(title="今天活", due_at="2026-11-05T10:00:00")
    out = call("view.calendar", start="2026-11-01", days=30, today=TODAY)
    assert out["days"] == 30 and len(out["cells"]) == 30
    day5 = next(c for c in out["cells"] if c["date"] == "2026-11-05")
    assert day5["is_today"] is True and day5["count"] == 1
    assert day5["events"][0]["title"] == "今天活"


def test_view_calendar_rejects_bad_days():
    with pytest.raises(QdError):
        call("view.calendar", days=0, today=TODAY)
    with pytest.raises(QdError):
        call("view.calendar", days=999, today=TODAY)


def test_view_calendar_includes_countdown_by_default():
    call("countdown.create", title="统考", target_date="2026-11-20")
    with_cd = call("view.calendar", start="2026-11-20", days=1, today=TODAY)
    without = call("view.calendar", start="2026-11-20", days=1, today=TODAY,
                   include_countdown=False)
    assert with_cd["cells"][0]["count"] == 1
    assert without["cells"][0]["count"] == 0


def test_view_board_columns_cover_all_open_tasks():
    gid = call("group.create", name="工作")["id"]
    _t(title="A", list_id=gid, priority=1)
    _t(title="B", list_id=gid)
    call("task.complete", item_id=_t(title="C"))
    out = call("view.board", group_by="status", today=TODAY)
    assert out["total"] == 3
    todo_col = next(c for c in out["columns"] if c["key"] == "todo")
    assert {x["data"]["title"] for x in todo_col["items"]} == {"A", "B"}


def test_view_board_by_group_uses_group_names():
    gid = call("group.create", name="工作任务")["id"]
    _t(title="A", list_id=gid)
    _t(title="B")
    labels = {c["label"] for c in call("view.board", group_by="group", today=TODAY)["columns"]}
    assert "工作任务" in labels


# --------------------------------------------------------------------------- 视图偏好


def test_view_prefs_roundtrip_and_marked_unsynced():
    out = call("view.pref_set", prefs={"layout": "board", "group": "priority"})
    assert out["synced"] is False, "视图偏好属设备本地数据，绝不能被同步到另一台机器"
    got = call("view.pref_get")["prefs"]
    assert got["layout"] == "board" and got["group"] == "priority"


def test_view_prefs_rejects_unknown_key():
    with pytest.raises(QdError) as e:
        call("view.pref_set", prefs={"whatever": 1})
    assert "不支持的偏好项" in e.value.message


def test_view_prefs_rejects_empty():
    with pytest.raises(QdError):
        call("view.pref_set", prefs={})


def test_view_prefs_do_not_touch_synced_items():
    """写偏好不能把任何 item 标成待推送，否则每次切视图都会产生一次 git 提交。"""
    _t(title="A")
    st = _store()
    st.mark_clean()
    call("view.pref_set", prefs={"layout": "calendar"})
    assert st.dirty_count() == 0


# --------------------------------------------------------------------------- 统计


def test_stats_today_counts():
    _t(title="逾期", due_at="2026-11-01T10:00:00", priority=1)
    _t(title="今天", due_at="2026-11-05T10:00:00", priority=2)
    _t(title="明天", due_at="2026-11-06T10:00:00")
    done = _t(title="完成", due_at="2026-11-05T09:00:00")
    call("task.complete", item_id=done)
    s = call("stats.today", today=TODAY)
    assert s["overdue"] == 1
    assert s["due_today"] == 1, "今天到期且未完成"
    assert s["done_today"] == 1
    assert s["open_total"] == 3
    assert s["by_priority"] == {"1": 1, "2": 1, "3": 0, "4": 1}, "「明天」未设优先级即 P4"


def test_stats_today_lists_near_countdowns():
    call("countdown.create", title="统考", target_date="2026-11-20")
    call("countdown.create", title="太远", target_date="2027-11-20")
    s = call("stats.today", today=TODAY)
    assert [c["title"] for c in s["countdown_soon"]] == ["统考"]
    assert s["countdown_soon"][0]["days_left"] == 15


def test_view_counts_is_global_not_view_scoped(app_ctx):
    """侧栏徽标必须全量计算。曾经用「当前视图的 tasks」数其它视图的角标，
    切到今天就显示今天的条数。"""
    gid = call("group.create", name="工作")["id"]
    for i in range(3):
        call("task.create", title=f"t{i}", list_id=gid, due_at="2026-11-05T10:00:00")
    call("task.create", title="明天", due_at="2026-11-06T10:00:00")
    call("task.create", title="无日期")
    done = call("task.create", title="完成", due_at="2026-11-05T09:00:00")["id"]
    call("task.complete", item_id=done)
    gone = call("task.create", title="删掉")["id"]
    call("task.delete", item_id=gone)

    c = call("view.counts", today="2026-11-05")
    assert c["all"] == 6 and c["today"] == 3
    assert c["next7"] == 4 and c["inbox"] == 2
    assert c["done"] == 1 and c["trash"] == 1
    assert c["by_group"] == {gid: 3}
    assert c["overdue"] == 0


def test_view_counts_flags_overdue(app_ctx):
    call("task.create", title="逾期活", due_at="2026-11-01T10:00:00")
    assert call("view.counts", today="2026-11-05")["overdue"] == 1


def _store():
    from qingdan.core.context import ctx
    return ctx.need_store()
