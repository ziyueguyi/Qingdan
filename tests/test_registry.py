"""registry 的参数绑定与调用约定。

这里锁的是两个曾经真实发生过的故障：形参名与业务参数撞车、未知参数被静默丢弃。
"""
from __future__ import annotations

import pytest

from qingdan.core.registry import QdError, REGISTRY, bind, call


def test_unknown_param_is_rejected_not_dropped():
    """静默丢弃未知参数会让 UI 里一个拼错的字段名表现为「点了没反应」。"""
    from qingdan.core.service import task_create
    with pytest.raises(QdError) as e:
        bind(task_create, {"title": "x", "section": "SEC-1"})
    assert e.value.code == "VALIDATION_ERROR"
    assert "section" in e.value.detail["unknown"]


def test_unknown_param_rejected_over_http(app_ctx):
    with pytest.raises(QdError) as e:
        call("task.create", title="x", bogus_field=1)
    assert "bogus_field" in str(e.value.detail)


def test_kwargs_features_still_accept_extras():
    """task.update 用 **changes 接收可变字段集，未知键由它自己按白名单校验。"""
    tid = call("task.create", title="旧")["id"]
    out = call("task.update", item_id=tid, title="新")
    assert out["data"]["title"] == "新"
    with pytest.raises(QdError):
        call("task.update", item_id=tid, not_a_field=1)


def test_forward_function_name_does_not_collide_with_business_param():
    """回归：call(name, **raw) 的形参曾与 group.create(name=...) 撞车。

    现在 feature_name 是仅位置参数，业务侧传 name 也不会冲突。
    """
    out = call("group.create", **{"name": "工作任务"})
    assert out["data"]["name"] == "工作任务"


def test_missing_required_param_reports_which_one():
    with pytest.raises(QdError) as e:
        call("task.create")
    assert "title" in e.value.message


def test_types_are_coerced_from_query_like_strings():
    from qingdan.core.service import task_list
    kw = bind(task_list, {"priority": "2", "include_deleted": "true", "limit": "10"})
    assert kw["priority"] == 2 and kw["include_deleted"] is True and kw["limit"] == 10


def test_bad_int_is_rejected_not_silently_zero():
    from qingdan.core.service import task_list
    with pytest.raises(QdError):
        bind(task_list, {"priority": "高"})


def test_list_param_accepts_json_string():
    from qingdan.core.service import task_create
    kw = bind(task_create, {"title": "x", "tags": '["重要","数据"]'})
    assert kw["tags"] == ["重要", "数据"]


def test_needs_auth_gates_before_execution(app_ctx):
    """未登录调同步类功能必须 401，且不能有任何副作用。"""
    from qingdan.core.registry import Feature

    def _noop() -> dict:
        raise AssertionError("不该被执行")

    REGISTRY["tmp.needs"] = Feature(name="tmp.needs", group="tmp", func=_noop,
                                    http=("POST", "/api/v1/tmp"), cli="tmp needs",
                                    needs_auth=True)
    try:
        with pytest.raises(QdError) as e:
            call("tmp.needs")
        assert e.value.code == "AUTH_REQUIRED"
        assert e.value.http_status == 401
    finally:
        REGISTRY.pop("tmp.needs", None)


def test_cli_subcommand_names_are_derived_from_features():
    for name, f in REGISTRY.items():
        group, action = f.cli.split(" ", 1)
        assert group == f.group and action == name.split(".", 1)[1]
