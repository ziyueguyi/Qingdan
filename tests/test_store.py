"""Store：merge base 列、dirty 队列、新装机判据。"""
from __future__ import annotations

from qingdan.core.clock import Rev
from qingdan.core.models import Item


def mk(rev_ms: int, deleted: bool = False, item_id: str = "T1") -> Item:
    return Item(kind="task", id=item_id, rev=Rev(rev_ms, 0, "D-test"),
                data={"title": "t", "priority": 1}, deleted=deleted)


def test_put_get_roundtrip(store):
    store.put(mk(1000))
    got = store.get("T1")
    assert got is not None and got.rev.ms == 1000
    assert got.data["title"] == "t"


def test_put_overwrites_by_id(store):
    store.put(mk(1000))
    store.put(mk(1200, item_id="T1"))
    assert store.get("T1").rev.ms == 1200
    assert len(store.list_items()) == 1


def test_deleted_hidden_by_default(store):
    store.put(mk(1000))
    store.put(mk(1200, deleted=True))
    assert store.list_items() == []
    assert len(store.list_items(include_deleted=True)) == 1


def test_dirty_flag_and_clean(store):
    store.put(mk(1000))
    assert store.dirty_count() == 1
    assert [i.id for i in store.list_items(dirty_only=True)] == ["T1"]
    assert store.mark_clean() == 1
    assert store.dirty_count() == 0


def test_base_snapshot_is_separate_from_current(store):
    """base 只存比较所需的 (rev, deleted)，与当前版本互不干扰。"""
    store.put(mk(1000))
    assert store.base_of("T1") is None, "未同步过就没有基线"
    store.set_base("T1", Rev(900, 0, "D-test"), False)
    base = store.base_of("T1")
    assert base is not None and base.rev.ms == 900 and not base.deleted
    assert store.get("T1").rev.ms == 1000, "写 base 不能动当前版本"
    assert store.dirty_count() == 0, "set_base 顺带清 dirty"


def test_bootstrap_candidate(store):
    """新装机判据：无基线 + 无待推送 → True；有脏数据 → False。"""
    assert store.bootstrap_candidate() is True
    store.put(mk(1000))
    assert store.bootstrap_candidate() is False, "离线用过不能当新机，否则本地空会删空远端"
    store.set_base("T1", Rev(1000, 0, "D-test"), False)
    store.state_set("base_commit", "abc123")
    assert store.bootstrap_candidate() is False


def test_device_id_persists_across_reopen(tmp_path):
    from qingdan.core.store import Store
    p = tmp_path / "db.sqlite"
    s1 = Store(p)
    dev = s1.device_id
    s1.put(mk(1000))
    s1.next_rev()                 # 必须走 next_rev，才会把时钟状态落库
    s1.close()
    s2 = Store(p)
    assert s2.device_id == dev, "设备指纹变了会破坏 HLC 的 tie-break"
    assert s2.clock.last_ms >= s1.clock.last_ms, "重启后时钟必须从历史最大值继续"
    s2.close()


def test_clock_advances_across_writes(store):
    a = store.next_rev()
    b = store.next_rev()
    assert b > a


def test_observe_remote_rev_protects_ordering(store):
    remote = Rev(store.clock.last_ms + 10 ** 6, 3, "D-other")
    store.observe(remote)
    assert store.next_rev() > remote


def test_conflict_records_full_versions(store):
    store.put(mk(1000))
    cid = store.record_conflict("T1", "both_changed", local=mk(1200), remote=mk(1300), base=mk(1000))
    row = store.db.execute("SELECT * FROM conflict WHERE id=?", (cid,)).fetchone()
    assert row["reason"] == "both_changed"
    assert '"ms": 1300' in row["remote_json"] or "1300" in row["remote_json"]


def test_snapshot_includes_tombstones(store):
    store.put(mk(1000, item_id="A"))
    store.put(mk(1100, deleted=True, item_id="B"))
    snap = store.snapshot()
    assert set(snap) == {"A", "B"}


def test_state_kv(store):
    assert store.state_get("nothing") is None
    store.state_set("base_commit", {"sha": "x"})
    assert store.state_get("base_commit") == {"sha": "x"}
