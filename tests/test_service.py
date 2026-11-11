"""端到端走 registry.call()：与 UI/HTTP 完全同一条路径。"""
from __future__ import annotations

import pytest

from qingdan.core.registry import QdError, call


def _create(**kw) -> str:
    return call("task.create", **{"title": "写周报", **kw})["id"]


def test_create_defaults_land_in_inbox(app_ctx):
    d = call("task.create", title="买猫粮")
    assert d["data"]["status"] == "todo"
    assert d["data"]["priority"] == 4
    assert d["data"]["list_id"] is None, "未指定分组应落收集箱"
    assert d["deleted"] is False


def test_title_required_and_trimmed(app_ctx):
    with pytest.raises(QdError) as e:
        call("task.create", title="   ")
    assert e.value.code == "VALIDATION_ERROR"
    assert call("task.create", title="  跑步  ")["data"]["title"] == "跑步"


def test_priority_domain_is_1_to_4(app_ctx):
    assert call("task.create", title="x", priority=1)["data"]["priority"] == 1
    for bad in (0, 5, "abc"):
        with pytest.raises(QdError):
            call("task.create", title="x", priority=bad)


def test_update_patches_and_bumps_version(app_ctx):
    tid = _create(title="旧标题")
    before = call("task.get", item_id=tid)
    after = call("task.update", item_id=tid, title="新标题")
    assert after["data"]["title"] == "新标题"
    assert after["rev"] != before["rev"], "修改必须发新版本，否则同步无法判断谁更新"
    assert after["data"]["status"] == "todo", "未传的字段不能被清掉"


def test_update_rejects_unknown_field(app_ctx):
    tid = _create()
    with pytest.raises(QdError) as e:
        call("task.update", item_id=tid, bogus=1)
    assert e.value.code == "VALIDATION_ERROR"


def test_delete_is_a_tombstone_not_removal(app_ctx):
    tid = _create()
    d = call("task.delete", item_id=tid)
    assert d["deleted"] is True and d["deleted_at_ms"]
    assert call("task.list")["total"] == 0
    assert call("task.list", include_deleted=True)["total"] == 1, "墓碑必须还能被同步层看见"
    assert call("task.get", item_id=tid)["deleted"] is True


def test_delete_then_restore_keeps_history_of_versions(app_ctx):
    tid = _create()
    v1 = call("task.get", item_id=tid)["rev"]
    v2 = call("task.delete", item_id=tid)["rev"]
    v3 = call("task.restore", item_id=tid)["rev"]
    assert len({v1, v2, v3}) == 3, "删与恢复各自是一个版本"
    assert call("task.get", item_id=tid)["deleted"] is False


def test_complete_sets_status_and_time(app_ctx):
    tid = _create()
    d = call("task.complete", item_id=tid)
    assert d["data"]["status"] == "done" and d["data"]["done_at"]


def test_get_missing_is_404(app_ctx):
    with pytest.raises(QdError) as e:
        call("task.get", item_id="NOPE")
    assert e.value.code == "NOT_FOUND"


def test_list_filters(app_ctx):
    g = call("group.create", name="工作任务")["id"]
    a = _create(title="A", list_id=g, priority=1, tags=["重要"])
    _create(title="B", priority=4)
    assert [i["id"] for i in call("task.list", list_id=g)["items"]] == [a]
    assert call("task.list", tag="重要")["total"] == 1
    assert call("task.list", priority=1)["total"] == 1
    assert call("task.list", status="done")["total"] == 0
    with pytest.raises(QdError):
        call("task.list", status="nonsense")


def test_smart_order_puts_due_first_then_priority(app_ctx):
    """Todoist 智能序：日期时间 → 优先级。"""
    late = _create(title="late", due_at="2026-12-31T10:00:00", priority=1)
    soon = _create(title="soon", due_at="2026-11-06T10:00:00", priority=3)
    none = _create(title="none")
    order = [i["data"]["title"] for i in call("task.list", sort="smart")["items"]]
    assert order == ["soon", "late", "none"], order


def test_group_lifecycle(app_ctx):
    g = call("group.create", name="学习安排", color="#7457e8")
    assert g["kind"] == "list", "UI 叫分组，内部 kind 不变"
    assert call("group.list")["total"] == 1
    call("group.delete", item_id=g["id"])
    assert call("group.list")["total"] == 0
    assert call("group.list", include_deleted=True)["total"] == 1


def test_group_name_required(app_ctx):
    with pytest.raises(QdError):
        call("group.create", name="  ")


def test_every_write_marks_dirty_for_push(app_ctx):
    st = app_ctx.store
    assert st is not None
    assert st.dirty_count() == 0
    tid = _create()
    assert st.dirty_count() == 1
    call("task.update", item_id=tid, title="改一下")
    assert st.dirty_count() == 1, "同一项反复修改仍只待推一次"
    call("task.delete", item_id=tid)
    assert st.dirty_count() == 1


def test_health_reports_counts(app_ctx):
    _create()
    h = call("app.health")
    assert h["ok"] is True and h["items"] == 1 and h["dirty"] == 1
    assert h["device_id"] == "D-test"


def test_app_info_lists_features(app_ctx):
    info = call("app.info")
    assert "task.create" in info["features"]
    assert info["device_id"] == "D-test"


def test_unregistered_feature_is_not_found(app_ctx):
    with pytest.raises(QdError) as e:
        call("task.does_not_exist")
    assert e.value.code == "NOT_FOUND"


def test_query_string_types_are_coerced(app_ctx):
    """HTTP query 全是字符串，必须按签名类型转回来。"""
    from qingdan.core.registry import bind
    from qingdan.core.service import task_create
    kw = bind(task_create, {"title": "x", "priority": "2", "tags": '["a","b"]'})
    assert kw["priority"] == 2 and kw["tags"] == ["a", "b"]
