"""撤销：用前像再写一个新版本，不是回滚历史。"""
from __future__ import annotations

import pytest

from qingdan.core.registry import QdError, call


def _tid(**kw) -> str:
    return call("task.create", **{"title": "写周报", **kw})["id"]


def test_undo_create_writes_tombstone():
    tid = _tid()
    out = call("undo.last")
    assert out["undone"] == "task.create"
    assert out["items"] == [tid]
    it = call("task.get", item_id=tid)
    assert it["deleted"] is True, "撤销新建 = 写墓碑，不是物理删除"
    assert call("task.list")["total"] == 0


def test_undo_update_restores_content_with_a_new_version():
    tid = _tid(title="旧标题")
    v_before = call("task.get", item_id=tid)["rev"]
    v_after = call("task.update", item_id=tid, title="新标题")["rev"]
    out = call("undo.last")
    assert out["undone"] == "task.update"
    it = call("task.get", item_id=tid)
    assert it["data"]["title"] == "旧标题"
    assert it["rev"] not in (v_before, v_after), "撤销必须发新版本，复用旧 rev 会破坏 LWW"


def test_undo_delete_brings_item_back_alive():
    tid = _tid()
    call("task.delete", item_id=tid)
    call("undo.last")
    assert call("task.get", item_id=tid)["deleted"] is False


def test_undo_reports_remaining():
    _tid()
    _tid()
    out = call("undo.last")
    assert out["remaining"] == 1, "两次 create 产生两条可撤销记录"
    call("undo.last")
    with pytest.raises(QdError) as e:
        call("undo.last")
    assert e.value.code == "NOT_FOUND"


def test_non_undoable_feature_records_nothing():
    call("view.pref_set", prefs={"layout": "board"})
    with pytest.raises(QdError):
        call("undo.last"), "视图偏好不该进撤销日志"


def test_undo_marks_dirty_for_push():
    """撤销产生的新版本同样是本地改动，必须待推送，否则另一台机器看不到撤销结果。"""
    tid = _tid()
    st = _store()
    st.mark_clean()
    assert st.dirty_count() == 0
    call("undo.last")
    assert st.dirty_count() == 1


def test_undo_cascaded_tag_rename_reverts_task_only():
    """标签改名会连带改一批任务；撤销要能把这一批一起退回。"""
    tag = call("tag.create", name="重要")["id"]
    t1 = _tid(title="A", tags=["重要"])
    t2 = _tid(title="B", tags=["重要"])
    out = call("tag.rename", item_id=tag, name="关键")
    assert out["tasks_updated"] == 2
    call("undo.last")
    assert sorted([call("task.get", item_id=t1)["data"]["tags"],
                   call("task.get", item_id=t2)["data"]["tags"]]) == [["重要"], ["重要"]]


def _store():
    from qingdan.core.context import ctx
    return ctx.need_store()
