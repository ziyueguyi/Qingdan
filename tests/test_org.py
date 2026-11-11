"""组织结构：文件夹 / 小节 / 标签 / 过滤器的连带行为。

这些功能的危险之处不在自身，而在**引用它的任务会不会被留在悬空状态**。
"""
from __future__ import annotations

import pytest

from qingdan.core.registry import QdError, call


def _group(name="工作任务") -> str:
    return call("group.create", name=name)["id"]


def _task(**kw) -> str:
    return call("task.create", **{"title": "任务", **kw})["id"]


# --------------------------------------------------------------------------- 标签


def test_tag_create_and_duplicate_rejected():
    call("tag.create", name="重要")
    with pytest.raises(QdError) as e:
        call("tag.create", name="重要")
    assert e.value.code == "VALIDATION_ERROR"


def test_tag_create_strips_leading_hash():
    """输入 @重要 时 UI 会传 "重要"，但手输 #重要 也要归一，否则同一标签两个名字。"""
    assert call("tag.create", name="#考编")["data"]["name"] == "考编"


def test_tag_assign_and_unassign():
    tid = _task()
    assert call("tag.assign", item_id=tid, tag="数据")["data"]["tags"] == ["数据"]
    assert call("tag.assign", item_id=tid, tag="#数据")["data"]["tags"] == ["数据"], "重复分配要幂等"
    assert call("tag.unassign", item_id=tid, tag="数据")["data"]["tags"] == []


def test_tag_rename_cascades_to_tasks():
    tag = call("tag.create", name="重要")["id"]
    t1, t2 = _task(tags=["重要"]), _task(tags=["重要", "数据"])
    out = call("tag.rename", item_id=tag, name="关键")
    assert out["tasks_updated"] == 2
    assert "关键" in call("task.get", item_id=t1)["data"]["tags"]
    assert sorted(call("task.get", item_id=t2)["data"]["tags"]) == ["关键", "数据"]
    assert call("task.get", item_id=t1)["data"]["tags"] != ["重要"]


def test_tag_delete_removes_from_all_tasks():
    tag = call("tag.create", name="家务")["id"]
    tid = _task(tags=["家务"])
    out = call("tag.delete", item_id=tag)
    assert out["tasks_updated"] == 1
    assert call("task.get", item_id=tid)["data"]["tags"] == []


def test_tag_list_reports_task_counts():
    call("tag.create", name="重要")
    _task(tags=["重要"])
    _task(tags=["重要"])
    item = next(x for x in call("tag.list")["items"] if x["data"]["name"] == "重要")
    assert item["task_count"] == 2


# --------------------------------------------------------------------------- 文件夹


def test_folder_delete_detaches_groups_not_deletes_them():
    fid = call("folder.create", name="工作")["id"]
    gid = _group()
    call("group.update", item_id=gid, folder_id=fid)
    call("folder.delete", item_id=fid)
    still = call("group.list")["items"]
    assert [g["id"] for g in still] == [gid], "删文件夹不能连带删掉分组"
    assert still[0]["data"]["folder_id"] is None


def test_folder_delete_can_move_groups_to_another_folder():
    f1 = call("folder.create", name="旧")["id"]
    f2 = call("folder.create", name="新")["id"]
    gid = _group()
    call("group.update", item_id=gid, folder_id=f1)
    call("folder.delete", item_id=f1, move_groups_to=f2)
    g = next(x for x in call("group.list")["items"] if x["id"] == gid)
    assert g["data"]["folder_id"] == f2


def test_folder_name_required():
    with pytest.raises(QdError):
        call("folder.create", name="  ")


# --------------------------------------------------------------------------- 小节


def test_section_requires_existing_group():
    with pytest.raises(QdError):
        call("section.create", list_id="NOPE", name="小节")


def test_section_delete_clears_task_reference():
    gid = _group()
    sid = call("section.create", list_id=gid, name="本周")["id"]
    tid = _task(list_id=gid, section_id=sid)
    call("section.delete", item_id=sid)
    assert call("task.get", item_id=tid)["data"]["section_id"] is None
    assert call("task.get", item_id=tid)["data"]["list_id"] == gid, "任务本身不能消失"


def test_section_reorder_sets_order_and_validates_membership():
    gid = _group()
    a = call("section.create", list_id=gid, name="A")["id"]
    b = call("section.create", list_id=gid, name="B")["id"]
    out = call("section.reorder", list_id=gid, order=[b, a])
    assert out["reordered"] == [b, a]
    items = call("section.list", list_id=gid)["items"]
    assert [x["data"]["name"] for x in items] == ["B", "A"]


def test_section_reorder_rejects_foreign_section():
    g1, g2 = _group("分组一"), _group("分组二")
    sid = call("section.create", list_id=g1, name="只属于一")["id"]
    with pytest.raises(QdError) as e:
        call("section.reorder", list_id=g2, order=[sid])
    assert e.value.code == "VALIDATION_ERROR"


def test_section_reorder_rejects_empty_order():
    gid = _group()
    with pytest.raises(QdError):
        call("section.reorder", list_id=gid, order=[])


# --------------------------------------------------------------------------- 分组补充


def test_group_update_rejects_blank_name():
    gid = _group()
    with pytest.raises(QdError):
        call("group.update", item_id=gid, name="   ")


def test_group_archive_marks_flag_and_keeps_it_listed():
    """归档是呈现层的事：分组仍在列表里，只是带上 archived 标记由 UI 折叠。"""
    gid = _group()
    tid = _task(list_id=gid)
    assert call("group.archive", item_id=gid)["data"]["archived"] is True
    assert call("group.list")["total"] == 1
    assert call("task.get", item_id=tid)["data"]["list_id"] == gid
    assert call("group.archive", item_id=gid, archived=False)["data"]["archived"] is False


def test_group_delete_moves_tasks_to_inbox():
    gid = _group()
    tid = _task(list_id=gid)
    out = call("group.delete", item_id=gid)
    assert out["moved_to_inbox"] == 1
    it = call("task.get", item_id=tid)
    assert it["deleted"] is False, "任务本身不能消失"
    assert it["data"]["list_id"] is None, "必须显式清引用，否则任务悬空、既不在分组也不进收集箱"
    assert call("view.query", name="inbox")["total"] == 1


# --------------------------------------------------------------------------- 过滤器


def test_filter_rejects_unknown_criteria_key():
    with pytest.raises(QdError) as e:
        call("filter.create", name="我的", criteria={"nonsense": 1})
    assert "不支持的过滤条件" in e.value.message


def test_filter_rejects_empty_criteria():
    with pytest.raises(QdError):
        call("filter.create", name="我的", criteria={})


def test_filter_tasks_applies_criteria():
    gid = _group("工作")
    _task(title="紧急活", list_id=gid, priority=1, tags=["重要"])
    _task(title="普通活", list_id=gid, priority=4)
    _task(title="别组的", list_id=_group("生活"), priority=1)
    fid = call("filter.create", name="工作高优",
               criteria={"group_id": gid, "priority_max": 2})["id"]
    out = call("filter.tasks", item_id=fid, today="2026-11-05")
    assert out["filter"] == "工作高优"
    assert [x["data"]["title"] for x in out["items"]] == ["紧急活"]


def test_filter_update_and_delete():
    fid = call("filter.create", name="原", criteria={"open_only": True})["id"]
    assert call("filter.update", item_id=fid, name="改")["data"]["name"] == "改"
    call("filter.delete", item_id=fid)
    assert call("filter.list")["total"] == 0
    assert call("filter.list", include_deleted=True)["total"] == 1


def test_filter_tasks_on_missing_filter_is_404():
    with pytest.raises(QdError) as e:
        call("filter.tasks", item_id="NOPE")
    assert e.value.code == "NOT_FOUND"
