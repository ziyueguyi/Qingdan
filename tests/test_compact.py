"""墓碑回收与 D1（离线复活）。

对应 `docs/M5-同步测试矩阵.md` D 组。核心命题：**同样是"文件不见了"，
被回收的墓碑和被人手工删除，语义完全相反** —— 前者是已确认的删除，
后者是协议外异常，必须保守处理。
"""
from __future__ import annotations

import json
import time

import pytest

from harness import Machine, remote, root      # noqa: F401
from qingdan.core import compact
from qingdan.core.clock import Rev
from qingdan.core.models import Item


def machine(root, name, remote, device=None):
    return Machine(root, name, remote, device=device)


def days_ago(n: int) -> int:
    return int((time.time() - n * 86400) * 1000)


# --------------------------------------------------------------------------- 回收计划


def test_plan_only_picks_old_tombstones(app_ctx):
    """回收窗口是按 deleted_at_ms 算的，不是按"是不是已删"。"""
    st = app_ctx.store
    def put(item_id, deleted, at_ms):
        st.put(Item(kind="task", id=item_id, rev=Rev(1000, 0, "D-x"),
                    data={"title": item_id}, deleted=deleted, deleted_at_ms=at_ms))
    put("fresh", True, days_ago(3))
    put("old", True, days_ago(120))
    put("alive", False, None)

    doomed = compact.plan(st, days=90)
    assert [it.id for it in doomed] == ["old"]


def test_plan_ignores_tombstones_without_timestamp(app_ctx):
    """没有 deleted_at_ms 的墓碑（历史数据）不回收 —— 宁可留着也不能误删证据。"""
    st = app_ctx.store
    st.put(Item(kind="task", id="no_ts", rev=Rev(1, 0, "D-x"), data={}, deleted=True,
                deleted_at_ms=None))
    assert compact.plan(st, days=0) == []


def test_graveyard_dump_is_deterministic():
    entries = {"b": {"rev": {"ms": 2, "seq": 0, "dev": "D"}, "deleted_at_ms": 1},
               "a": {"rev": {"ms": 1, "seq": 0, "dev": "D"}, "deleted_at_ms": 2}}
    assert compact.dump_graveyard(entries) == compact.dump_graveyard(dict(reversed(list(entries.items()))))
    assert b"\r" not in compact.dump_graveyard(entries)


def test_corrupt_graveyard_degrades_to_conservative():
    """名册坏了不能拖垮同步；退化成"当作手工删除"的保守路径，而不是当成没删。"""
    assert compact.parse_graveyard({"graveyard.json": b"{ broken json"}) == {}
    assert compact.parse_graveyard({}) == {}


# --------------------------------------------------------------------------- 回收动作


def test_compact_removes_files_and_writes_graveyard(root, remote):
    a = machine(root, "c1", remote)
    keep = a.add(title="留下的")
    gone = a.add(title="要回收的")
    a.sync()
    tomb = a.delete(gone.id)          # 墓碑是一个新版本，rev 比 add 时更晚
    a.sync()
    assert gone.path() in a.remote_paths()

    report = a.compact(days=0, now_ms=int(time.time() * 1000) + 1)
    assert report["removed"] == 1

    paths = a.remote_paths()
    assert gone.path() not in paths, "墓碑文件应被删除"
    assert keep.path() in paths
    assert "graveyard.json" in paths

    # 名册记的必须是**墓碑**的版本戳 —— D1 判断"离线编辑是否比删除更新"全靠它
    gy = compact.parse_graveyard(a.git.read_tree(a.git.head()))
    assert gy[gone.id]["rev"] == tomb.rev.to_json()
    assert gy[gone.id]["rev"] != gone.rev.to_json()


def test_compact_is_idempotent(root, remote):
    a = machine(root, "c2", remote)
    gone = a.add(title="回收两次")
    a.sync()
    a.delete(gone.id)
    a.sync()
    first = a.compact(days=0, now_ms=int(time.time() * 1000) + 1)
    second = a.compact(days=0, now_ms=int(time.time() * 1000) + 1)
    assert first["removed"] == 1 and second["removed"] == 0


def test_graveyard_survives_later_syncs(root, remote):
    """回收后新增的改动不能把名册覆盖掉 —— 名册是删除证据的唯一来源。"""
    a = machine(root, "c3", remote)
    gone = a.add(title="先删")
    a.sync(); a.delete(gone.id); a.sync()
    a.compact(days=0, now_ms=int(time.time() * 1000) + 1)

    a.add(title="后来加的")
    a.sync()
    gy = compact.parse_graveyard(a.git.read_tree(a.git.head()))
    assert gone.id in gy, "后续同步不得丢失回收名册"


# --------------------------------------------------------------------------- D1 核心


def test_d1_offline_edit_does_not_resurrect_silently(root, remote):
    """D1：离线超过回收窗口的机器带着更新的本地编辑上线，**不得静默复活**。"""
    a = machine(root, "d1a", remote)
    item = a.add(title="共同项")
    a.sync()

    b = machine(root, "d1b", remote)
    b.sync()                                   # b 拿到 base = 原始版本

    # a 删除并回收（模拟 90 天后）
    tomb = a.delete(item.id)
    a.sync()
    a.compact(days=0, now_ms=int(time.time() * 1000) + 1)

    # b 一直离线，并且把这一项改了（rev 比墓碑还新）
    edited = b.edit(item.id, title="b 离线期间的编辑")
    assert edited.rev > tomb.rev, "前置条件：本地编辑必须比删除更新"

    report = b.sync()

    # 关键断言 1：没有复活
    assert b.item(item.id).deleted is True, "已删项被静默复活"
    # 关键断言 2：远端也没被推回来
    assert item.path() not in a.remote_paths(), "复活被推回远端"
    # 关键断言 3：编辑没丢，进了冲突表
    rows = b.conflicts()
    assert any(r["reason"] == "resurrect_after_compaction" for r in rows), rows
    assert "resurrect_blocked" in str(report.anomalies)
    # 关键断言 4：名册里的权威版本戳被沿用，没有重新发号
    assert b.item(item.id).rev == tomb.rev, "回收删除必须沿用墓碑版本，不能本地发号"


def test_d1_user_can_recover_the_blocked_edit(root, remote):
    """被拦下的编辑必须真的能取回来 —— 否则"进冲突表"只是句空话。"""
    a = machine(root, "d2a", remote)
    item = a.add(title="原样")
    a.sync()
    b = machine(root, "d2b", remote)
    b.sync()
    a.delete(item.id); a.sync()
    a.compact(days=0, now_ms=int(time.time() * 1000) + 1)
    b.edit(item.id, title="我改过的内容")
    b.sync()

    row = b.store.db.execute("SELECT local_json FROM conflict WHERE item_id=?",
                             (item.id,)).fetchone()
    assert row is not None
    recovered = Item.from_payload(json.loads(row["local_json"]))
    assert recovered.data["title"] == "我改过的内容"
    assert recovered.deleted is False


def test_offline_machine_without_newer_edit_deletes_quietly(root, remote):
    """D1 的对照：本地没有更新的编辑 → 静默维持删除，不该弹冲突。"""
    a = machine(root, "d3a", remote)
    item = a.add(title="会被删")
    a.sync()
    b = machine(root, "d3b", remote)
    b.sync()
    a.delete(item.id); a.sync()
    a.compact(days=0, now_ms=int(time.time() * 1000) + 1)

    report = b.sync()
    assert b.item(item.id).deleted is True
    assert report.conflicts == 0, "没有编辑冲突就不该制造噪音"
    assert not any(x.startswith("resurrect_blocked") for x in report.anomalies)


def test_graveyard_vs_manual_delete_are_distinguished(root, remote):
    """名册里有 = 已确认删除；名册里没有 = 协议外手工删除（B4）。

    这两种"文件不见了"的处理完全相反，混了就会误删用户数据。
    """
    a = machine(root, "d4a", remote)
    compacted = a.add(title="回收掉的")
    manual = a.add(title="被手工删的")
    a.sync()
    b = machine(root, "d4b", remote)
    b.sync()

    # 一个走正常删除 + 回收
    a.delete(compacted.id); a.sync()
    a.compact(days=0, now_ms=int(time.time() * 1000) + 1)
    # 另一个被人从仓库里直接删文件（无墓碑、无名册）
    import os
    from dulwich import porcelain
    sab = os.path.join(a.root, "sab")
    porcelain.clone(a.remote, sab)
    os.remove(os.path.join(sab, manual.path()))
    porcelain.rm(sab, paths=[manual.path()])
    porcelain.commit(sab, message=b"manual", author=b"x <x@y>", committer=b"x <x@y>")
    porcelain.push(sab, a.remote, b"refs/heads/master")

    b.git.fetch(a.remote)
    report = b.sync()

    assert b.item(compacted.id).deleted is True, "名册项应维持删除"
    assert b.item(manual.id) is not None and not b.item(manual.id).deleted, "手工删除应保留本地"
    assert any(x.startswith("external_delete") for x in report.anomalies)
    assert not any(x.startswith("resurrect_blocked") for x in report.anomalies)


def test_compact_never_touches_alive_items(root, remote):
    """回收只针对墓碑。活着的项哪怕从没被改过也不能进名册。"""
    a = machine(root, "c5", remote)
    alive = a.add(title="活着")
    a.sync()
    report = a.compact(days=0, now_ms=int(time.time() * 1000) + 1)
    assert report["removed"] == 0
    assert alive.path() in a.remote_paths()
    assert "graveyard.json" not in a.remote_paths()
