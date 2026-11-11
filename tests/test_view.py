"""视图与查询的纯逻辑测试。

这里不碰数据库，直接构造 Item，专门验证 §5.2 的视图语义和分组/排序不丢任务。
"""
from __future__ import annotations

import re
from datetime import date

import pytest

from qingdan.core.clock import Rev
from qingdan.core.models import Item
from qingdan.core import view

TODAY = date(2026, 11, 5)


def mk(item_id="T", title="t", due=None, status="todo", priority=4, tags=None,
       list_id=None, deleted=False, deleted_at=None, done_at=None, order=0,
       created="2026-11-01T09:00:00", target=None, seq=0):
    data = {"title": title, "status": status, "priority": priority, "tags": tags or [],
            "list_id": list_id, "due_at": due, "order": order, "created_at": created,
            "done_at": done_at}
    if target is not None:
        data["target_date"] = target
    return Item(kind="task", id=item_id, rev=Rev(1000 + seq, 0, "D-a"), data=data,
                deleted=deleted, deleted_at_ms=deleted_at)


# --------------------------------------------------------------------------- 日期


def test_days_between_is_calendar_based():
    """回归：用 round(毫秒差/天) 会把「今天 20:00」算进明天、「逾期 6 小时」算进今天。"""
    assert view.days_between("2026-11-05T20:00:00", TODAY) == 0
    assert view.days_between("2026-11-04T18:00:00", TODAY) == -1
    assert view.days_between("2026-11-05T00:30:00", TODAY) == 0
    assert view.days_between("2026-11-06T00:30:00", TODAY) == 1
    assert view.days_between(None, TODAY) is None


def test_parse_dt_folds_timezone_to_local():
    aware = view.parse_dt("2026-11-05T23:30:00+08:00")
    assert aware is not None and aware.tzinfo is None
    assert aware.strftime("%Y-%m-%d") in {"2026-11-05", "2026-11-04"}


def test_parse_dt_rejects_garbage():
    assert view.parse_dt("明天下午") is None
    assert view.parse_dt("") is None


# --------------------------------------------------------------------------- 今天


def test_today_includes_overdue_and_excludes_no_due():
    items = [mk("A", due="2026-11-03T10:00:00"),      # 逾期
             mk("B", due="2026-11-05T20:00:00"),      # 今天
             mk("C"),                                  # 无日期
             mk("D", due="2026-11-06T09:00:00")]       # 明天
    got = [it.id for it in view.apply_view("today", items, TODAY)]
    assert got == ["A", "B"], "今天视图 = 逾期 + 今天到期；无日期任务不出现"


def test_today_hides_done_unless_asked():
    items = [mk("B", due="2026-11-05T20:00:00", status="done", done_at="2026-11-05T10:00:00")]
    assert view.apply_view("today", items, TODAY) == []
    assert len(view.apply_view("today", items, TODAY, include_done=True)) == 1


def test_today_excludes_deleted():
    items = [mk("A", due="2026-11-05T10:00:00", deleted=True, deleted_at=1)]
    assert view.apply_view("today", items, TODAY) == []


def test_today_boundary_at_midnight():
    """23:59 今天到期必须算今天，00:00 明天到期必须不算。"""
    assert len(view.apply_view("today", [mk(due="2026-11-05T23:59:59")], TODAY)) == 1
    assert view.apply_view("today", [mk(due="2026-11-06T00:00:00")], TODAY) == []


def test_today_accepts_string_today():
    assert len(view.apply_view("today", [mk(due="2026-11-05T10:00:00")], "2026-11-05")) == 1


# --------------------------------------------------------------------------- 近一周


def test_next7_window_is_seven_days_inclusive():
    items = [mk("A", due="2026-11-03T10:00:00"),      # 逾期，含
             mk("B", due="2026-11-11T23:00:00"),      # 今天+6，含
             mk("C", due="2026-11-12T00:00:00"),      # 今天+7，不含
             mk("D")]
    got = [it.id for it in view.apply_view("next7", items, TODAY)]
    assert got == ["A", "B"]


# --------------------------------------------------------------------------- 其它视图


def test_inbox_is_items_without_group():
    items = [mk("A"), mk("B", list_id="G1"), mk("C", deleted=True, deleted_at=1)]
    assert [it.id for it in view.apply_view("inbox", items, TODAY)] == ["A"]


def test_done_view_sorts_by_done_at_desc():
    items = [mk("A", status="done", done_at="2026-11-05T09:00:00"),
             mk("B", status="done", done_at="2026-11-04T09:00:00"),
             mk("C", status="todo")]
    assert [it.id for it in view.apply_view("done", items, TODAY)] == ["A", "B"]


def test_trash_view_only_tombstones_newest_first():
    items = [mk("A", deleted=True, deleted_at=100), mk("B", deleted=True, deleted_at=200),
             mk("C")]
    assert [it.id for it in view.apply_view("trash", items, TODAY)] == ["B", "A"]


def test_unknown_view_raises():
    with pytest.raises(ValueError):
        view.apply_view("nope", [], TODAY)


# --------------------------------------------------------------------------- 分组不变量


@pytest.mark.parametrize("mode", view.GROUP_MODES)
def test_grouping_never_loses_or_duplicates_items(mode):
    """任意分组方式下，各组数量之和 == 不分组总数。这是「分组只是呈现」的证明。"""
    items = [mk("A", due="2026-11-03T10:00:00", tags=["重要"], priority=1, list_id="G1"),
             mk("B", due="2026-11-05T10:00:00", tags=["数据"], priority=2),
             mk("C", status="done", done_at="2026-11-05T08:00:00", tags=["重要"]),
             mk("D", priority=4)]
    total = len(items)
    buckets = view.group_items(items, mode, TODAY, {"G1": "工作任务"})
    assert sum(len(b["items"]) for b in buckets) == total, mode
    ids = [it.id for b in buckets for it in b["items"]]
    assert sorted(ids) == sorted(i.id for i in items), mode


def test_ui_labels_come_back_as_keys_not_chinese():
    """后端不得返回展示文案 —— 否则切英文后界面残留「已逾期」「待办」。"""
    items = [mk("A", due="2026-11-03T10:00:00"),
             mk("B", status="done", done_at="2026-11-05T08:00:00")]
    for b in view.group_items(items, "date", TODAY):
        assert b["label_key"] and b["label"] is None
        assert not re.search(r"[一-鿿]", b["label_key"])
    for b in view.group_items(items, "status", TODAY):
        assert b["label_key"] in {"st_todo", "st_doing", "st_done", "st_cancelled"}
    for b in view.group_items(items, "priority", TODAY):
        assert b["label_key"].startswith("prio_")


def test_user_data_labels_are_not_keyed():
    """分组名和标签名是用户数据：走 label、label_key 为空，不能被当成 i18n 键。"""
    items = [mk("A", list_id="G1", tags=["考编"])]
    g = [b for b in view.group_items(items, "group", TODAY, {"G1": "学习安排"}) if b["items"]][0]
    assert g["label"] == "学习安排" and g["label_key"] is None
    tg = [b for b in view.group_items(items, "tag", TODAY) if b["items"]][0]
    assert tg["label"] == "考编" and tg["label_key"] is None


def test_date_grouping_labels():
    items = [mk("A", due="2026-11-03T10:00:00"), mk("B", due="2026-11-05T10:00:00"),
             mk("C", due="2026-11-06T10:00:00"), mk("D", due="2026-11-09T10:00:00"),
             mk("E", due="2026-12-01T10:00:00"), mk("F"),
             mk("G", status="done", done_at="2026-11-05T08:00:00")]
    keys = [b["label_key"] for b in view.group_items(items, "date", TODAY)]
    assert keys == ["sec_overdue", "sec_today", "sec_tomorrow", "sec_week",
                    "sec_later", "sec_none", "sec_done"]


def test_empty_groups_are_dropped():
    buckets = view.group_items([mk("A", due="2026-11-05T10:00:00")], "date", TODAY)
    assert [b["label_key"] for b in buckets] == ["sec_today"]


def test_tag_grouping_collects_untagged():
    items = [mk("A", tags=["重要"]), mk("B", tags=["重要", "数据"]), mk("C")]
    buckets = view.group_items(items, "tag", TODAY)
    by_label = {b["label"]: b["items"] for b in buckets if b["label"]}
    assert [i.id for i in by_label["重要"]] == ["A", "B"]
    assert buckets[-1]["label_key"] == "untagged"
    assert [i.id for i in buckets[-1]["items"]] == ["C"]


def test_unknown_group_mode_raises():
    with pytest.raises(ValueError):
        view.group_items([], "nonsense", TODAY)


# --------------------------------------------------------------------------- 排序


def test_smart_order_is_due_then_priority():
    items = [mk("late", due="2026-12-31T10:00:00", priority=1),
             mk("soon", due="2026-11-06T10:00:00", priority=3),
             mk("none", priority=1)]
    assert [it.id for it in view.sort_items(items, "smart")] == ["soon", "late", "none"]


def test_same_due_orders_by_priority_then_manual():
    items = [mk("a", due="2026-11-06T10:00:00", priority=3, order=5),
             mk("b", due="2026-11-06T10:00:00", priority=1, order=9),
             mk("c", due="2026-11-06T10:00:00", priority=3, order=1)]
    assert [it.id for it in view.sort_items(items, "smart")] == ["b", "c", "a"]


def test_sort_is_total_no_item_lost():
    for mode in view.SORT_MODES:
        items = [mk("A", priority=1), mk("B", priority=2), mk("C", priority=3)]
        assert sorted(it.id for it in view.sort_items(items, mode)) == ["A", "B", "C"], mode
        assert len(view.sort_items(items, mode, reverse=True)) == 3


def test_reverse_is_not_equal_to_forward_when_ties_exist():
    items = [mk("A", seq=1), mk("B", seq=2)]
    assert [i.id for i in view.sort_items(items, "updated")] == ["A", "B"]
    assert [i.id for i in view.sort_items(items, "updated", reverse=True)] == ["B", "A"]


def test_unknown_sort_raises():
    with pytest.raises(ValueError):
        view.sort_items([], "nonsense")


# --------------------------------------------------------------------------- 日历与看板


def test_calendar_days_covers_range_and_marks_today():
    items = [mk("A", due="2026-11-05T10:00:00"), mk("B", due="2026-11-05T20:00:00")]
    cells = view.calendar_days(items, "2026-11-01", 10, TODAY)
    assert len(cells) == 10
    assert cells[4]["date"] == "2026-11-05" and cells[4]["count"] == 2
    assert cells[4]["is_today"] is True and cells[3]["is_today"] is False
    assert cells[0]["in_month"] is True


def test_calendar_marks_overdue_flag():
    cells = view.calendar_days([mk("A", due="2026-11-03T10:00:00")], "2026-11-01", 6, TODAY)
    assert cells[2]["events"][0]["overdue"] is True


def test_calendar_skips_deleted():
    cells = view.calendar_days([mk("A", due="2026-11-05T10:00:00", deleted=True, deleted_at=1)],
                               "2026-11-05", 1, TODAY)
    assert cells[0]["count"] == 0


def test_board_columns_by_status_covers_all_items():
    items = [mk("A", status="todo"), mk("B", status="done"), mk("C", status="doing")]
    cols = view.board_columns(items, "status", {}, TODAY)
    assert sum(c["total"] for c in cols) == 3
    assert [c["key"] for c in cols] == ["todo", "doing", "done", "cancelled"]


def test_board_columns_by_group_uses_names():
    items = [mk("A", list_id="G1"), mk("B")]
    cols = view.board_columns(items, "group", {"G1": "工作任务"}, TODAY)
    labels = {c["label"] for c in cols}
    assert "工作任务" in labels


def test_board_rejects_unknown_group_by():
    with pytest.raises(ValueError):
        view.board_columns([], "nonsense")


# --------------------------------------------------------------------------- 过滤器条件


def test_criteria_empty_is_no_filter():
    assert view.match_criteria(mk("A"), {}, TODAY) is True


def test_criteria_open_only_and_priority():
    it = mk("A", priority=1)
    assert view.match_criteria(it, {"open_only": True}, TODAY) is True
    assert view.match_criteria(mk("B", status="done"), {"open_only": True}, TODAY) is False
    assert view.match_criteria(it, {"priority": 1}, TODAY) is True
    assert view.match_criteria(it, {"priority": 2}, TODAY) is False
    assert view.match_criteria(it, {"priority_max": 2}, TODAY) is True


def test_criteria_tag_variants():
    it = mk("A", tags=["重要", "数据"])
    assert view.match_criteria(it, {"tag": "重要"}, TODAY) is True
    assert view.match_criteria(it, {"tags_any": ["考编", "数据"]}, TODAY) is True
    assert view.match_criteria(it, {"tags_any": ["考编"]}, TODAY) is False


def test_criteria_due_window():
    it = mk("A", due="2026-11-07T10:00:00")
    assert view.match_criteria(it, {"due_within_days": 3}, TODAY) is True
    assert view.match_criteria(it, {"due_within_days": 1}, TODAY) is False
    assert view.match_criteria(mk("B"), {"due_within_days": 3}, TODAY) is False, "无日期不算在窗口内"


def test_criteria_overdue():
    assert view.match_criteria(mk("A", due="2026-11-01T10:00:00"), {"overdue": True}, TODAY) is True
    assert view.match_criteria(mk("B", due="2026-11-06T10:00:00"), {"overdue": True}, TODAY) is False


def test_criteria_no_group_and_group_id():
    assert view.match_criteria(mk("A"), {"no_group": True}, TODAY) is True
    assert view.match_criteria(mk("B", list_id="G1"), {"no_group": True}, TODAY) is False
    assert view.match_criteria(mk("C", list_id="G1"), {"group_id": "G1"}, TODAY) is True


def test_criteria_text_query_is_case_insensitive_substring():
    it = mk("A", title="Export CSV Report")
    assert view.match_criteria(it, {"q": "csv report"}, TODAY) is True
    assert view.match_criteria(it, {"q": "不存在"}, TODAY) is False


def test_criteria_skips_deleted_and_non_tasks():
    assert view.match_criteria(mk("A", deleted=True, deleted_at=1), {}, TODAY) is False
    tag = Item(kind="tag", id="X", rev=Rev(1, 0, "D-a"), data={"name": "重要"})
    assert view.match_criteria(tag, {}, TODAY) is False
