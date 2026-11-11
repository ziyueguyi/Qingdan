"""Item 模型与确定性序列化。

序列化确定性是 git 不产生伪 diff 的前提，必须逐字节验证。
"""
from __future__ import annotations

import json

import pytest

from qingdan.core.clock import Rev
from qingdan.core.models import Item, KINDS, new_id, parse_rev


def _item(**kw):
    base = dict(kind="task", id="01TEST", rev=Rev(1000, 0, "D-a"),
                data={"title": "买猫粮", "priority": 1, "tags": ["宠物"]})
    base.update(kw)
    return Item(**base)


def test_new_id_shape():
    i = new_id()
    assert len(i) == 26
    assert set(i) <= set("0123456789ABCDEFGHJKMNPQRSTVWXYZ")
    assert i != new_id()


def test_new_id_is_time_ordered():
    a = new_id(1_700_000_000_000)
    b = new_id(1_700_000_001_000)
    assert a < b, "ULID 的时间前缀应保证可排序"


def test_new_id_rejects_out_of_range():
    with pytest.raises(ValueError):
        new_id(1 << 48)


def test_serialization_is_deterministic():
    """同一内容两次 dump 必须字节相同 —— 嵌套 dict 也要排序。"""
    a = _item(data={"z": 1, "title": "x", "a": {"m": 2, "b": [3, {"q": 1, "a": 2}]}})
    b = _item(data={"a": {"b": [3, {"a": 2, "q": 1}], "m": 2}, "title": "x", "z": 1})
    assert a.to_bytes() == b.to_bytes()


def test_serialization_is_utf8_lf_and_sorted():
    blob = _item().to_bytes()
    assert blob.endswith(b"\n")
    assert b"\r" not in blob
    assert "买猫粮".encode("utf-8") in blob, "中文不能被转义成 \\uXXXX"
    payload = json.loads(blob)
    assert list(payload) == sorted(payload), "顶层键必须排序，否则 git 会产生伪 diff"
    assert list(payload["data"]) == sorted(payload["data"]), "嵌套 dict 也要排序"


def test_roundtrip_preserves_everything():
    src = _item(deleted=True, deleted_at_ms=2000, meta={"creator_dev": "D-a"})
    back = Item.from_bytes(src.to_bytes())
    assert back == src


def test_repo_path_by_kind_flat():
    assert _item().path() == "items/task/01TEST.json"
    assert _item(kind="list").path() == "items/list/01TEST.json"


def test_unknown_kind_rejected():
    with pytest.raises(ValueError):
        _item(kind="whatever")


def test_empty_id_rejected():
    with pytest.raises(ValueError):
        _item(id="")


def test_version_pair_drives_comparison():
    a = _item(rev=Rev(1, 0, "D-a"))
    b = _item(rev=Rev(1, 0, "D-a"))
    assert a.version == b.version
    assert a.version != b.__class__.from_payload({**b.payload(), "deleted": True}).version


def test_rev_string_roundtrip():
    it = _item(rev=Rev(123, 4, "D-xy"))
    assert parse_rev(it.to_dict()["rev"]) == it.rev


def test_payload_is_json_safe():
    json.dumps(_item().payload(), ensure_ascii=False)


def test_all_sync_kinds_are_supported():
    for kind in sorted(KINDS):
        assert _item(kind=kind).path().startswith(f"items/{kind}/")
