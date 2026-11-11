"""搜索：中文两字词必须能命中。

这条测试是「为什么不用 FTS5」的证据固化 —— 一旦有人为了性能把 LIKE 换回
FTS5 默认分词器，「立案」「周报」这类两字查询会静默返回空结果。
"""
from __future__ import annotations

import pytest

from qingdan.core.registry import QdError, call


def test_two_char_chinese_query_hits():
    call("task.create", title="整理滨江法院立案材料")
    out = call("search.query", q="立案")
    assert out["total"] == 1, "两字中文词必须命中（trigram 分词器会返回 0）"


def test_search_covers_note_and_tags():
    tid = call("task.create", title="周报", note="含《依法立案申请书》截图")["id"]
    call("task.create", title="别的东西", tags=["考编"])
    assert call("search.query", q="申请书")["total"] == 1
    assert call("search.query", q="考编")["total"] == 1


def test_search_is_case_insensitive_for_ascii():
    call("task.create", title="Export CSV Report")
    assert call("search.query", q="csv")["total"] == 1
    assert call("search.query", q="CSV")["total"] == 1


def test_search_excludes_deleted_by_default():
    tid = call("task.create", title="旧版报销单整理")["id"]
    call("task.delete", item_id=tid)
    assert call("search.query", q="报销单")["total"] == 0
    assert call("search.query", q="报销单", include_deleted=True)["total"] == 1


def test_search_can_restrict_kinds():
    gid = call("group.create", name="工作任务")["id"]
    call("task.create", title="写周报", list_id=gid)
    assert call("search.query", q="周报", kinds=["task"])["total"] == 1
    assert call("search.query", q="工作任务", kinds=["list"])["total"] == 1
    assert call("search.query", q="工作任务", kinds=["task"])["total"] == 0


def test_search_empty_query_rejected():
    with pytest.raises(QdError) as e:
        call("search.query", q="   ")
    assert e.value.code == "VALIDATION_ERROR"


def test_search_respects_limit():
    for i in range(12):
        call("task.create", title=f"填充任务{i} 立案")
    assert call("search.query", q="立案", limit=5)["total"] == 5


def test_search_returns_full_item_shape():
    tid = call("task.create", title="买猫粮")["id"]
    hit = call("search.query", q="猫粮")["items"][0]
    assert hit["id"] == tid and hit["kind"] == "task" and "rev" in hit
