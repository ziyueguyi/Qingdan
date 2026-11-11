"""同步引擎端到端测试。

对应 `docs/M5-同步测试矩阵.md` 的 🔴 行。用**两台机器**（各自独立 Store + 独立 git
工作副本）对同一个本地裸仓库同步，真实走 git 读写，不用 mock。
harness 见 `tests/harness.py`，与 test_compact.py 共用。
"""
from __future__ import annotations

import os
import time

import pytest
from dulwich import porcelain

from harness import Machine, remote, root      # noqa: F401  （fixture 由 conftest 注入）
from qingdan.core.models import Item
from qingdan.core.sync import parse_tree


def machine(root, name, remote, device=None):
    return Machine(root, name, remote, device or f"D-{name}")


# --------------------------------------------------------------------------- B1 / B2 装机


def test_fresh_machine_pulls_only_never_deletes_remote(root, remote):
    """B1 + B3 保险丝：新装机器本地为空，绝不能把远端整库判成"该删"。"""
    a = machine(root, "a", remote)
    for i in range(5):
        a.add(title=f"任务{i}")
    a.sync()

    b = machine(root, "b", remote)
    report = b.sync()
    assert report.mode == "bootstrap"
    assert report.pulled == 5
    assert report.pushed == 0, "bootstrap 模式不允许推送"
    assert b.titles() == {f"任务{i}" for i in range(5)}

    # 远端内容没被动过
    check = machine(root, "check", remote)
    check.sync()
    assert len(check.titles()) == 5


def test_offline_machine_first_merge_keeps_both_sides(root, remote):
    """B2：离线用过再首次登录 → FIRST_MERGE，本地独有与远端独有都必须活下来。"""
    a = machine(root, "a2", remote)
    a.add(title="远端独有")
    a.sync()

    b = machine(root, "b2", remote)
    b.add(title="本地离线建的")            # 有 dirty，未同步
    report = b.sync()
    assert report.mode == "first_merge"
    assert {"远端独有", "本地离线建的"} <= b.titles()

    a.sync()
    assert {"远端独有", "本地离线建的"} <= a.titles(), "另一台也必须看到本地离线建的项"


def test_local_row_lost_without_base_reimports_not_deletes(root, remote):
    """B3：本地行被清掉时 base 也一起没了（同表同行），于是按"远端新增"导入。

    这是结构性的安全：不可能出现"本地没了 + 有 base 记录"从而误删远端。
    """
    a = machine(root, "a3", remote)
    item = a.add(title="会被清掉")
    a.sync()

    b = machine(root, "b3", remote)
    b.sync()
    b.store.db.execute("DELETE FROM item WHERE id=?", (item.id,))
    b.store.db.commit()

    b.sync()
    assert b.item(item.id) is not None, "清掉的行必须被重新导入"

    a.sync()
    assert a.item(item.id) is not None, "远端不能被一次本地误操作删掉"


# --------------------------------------------------------------------------- B10–B14 不变量


def test_sync_is_idempotent(root, remote):
    """B10：sync(sync(s)) == sync(s)，第二轮不产生新提交。"""
    a = machine(root, "a10", remote)
    a.add(title="幂等")
    first = a.sync()
    assert first.pushed == 1
    second = a.sync()
    assert second.pushed == 0 and second.pulled == 0, "重复同步不该再推任何东西"


def test_two_machines_converge(root, remote):
    """B11：两台机器各自同步一轮后，可见状态必须一致。"""
    a = machine(root, "a11", remote)
    b = machine(root, "b11", remote)
    a.add(title="A建的")
    a.sync()
    b.sync()
    b.add(title="B建的")
    b.sync()
    a.sync()
    assert a.titles() == b.titles() == {"A建的", "B建的"}

    # 字节级一致：两边读到的远端 tree 完全相同
    assert a.git.read_tree(a.git.head()) == b.git.read_tree(b.git.head())


def test_dirty_cleared_after_push(root, remote):
    """B14：推送成功后 dirty 归零、base 落到该版本。"""
    a = machine(root, "a14", remote)
    item = a.add(title="待推")
    assert a.store.dirty_count() == 1
    a.sync()
    assert a.store.dirty_count() == 0
    base = a.store.base_of(item.id)
    assert base is not None and base.rev == item.rev


def test_pulled_item_keeps_original_rev(root, remote):
    """B15：拉下来的版本**不得重新发号**，否则另一台会把它当本地更新压回去。"""
    a = machine(root, "a15", remote)
    item = a.add(title="原版")
    a.sync()

    b = machine(root, "b15", remote)
    b.sync()
    got = b.item(item.id)
    assert got is not None
    assert got.rev == item.rev, "远端版本落地必须保留原 rev"
    assert b.store.dirty_count() == 0, "拉取不该产生新的待推送（B22）"


def test_observe_protects_next_local_rev(root, remote):
    """B16：拉到更晚的远端版本后，本机下一个版本必须严格更大。"""
    a = machine(root, "a16", remote)
    future = int(time.time() * 1000) + 10 ** 10          # A 机时钟超前约 4 个月
    a.store.clock.last_ms = future
    item = a.add(title="超前的改动")
    a.sync()

    b = machine(root, "b16", remote)
    b.sync()
    assert b.store.clock.last_ms >= item.rev.ms, "observe 没把本机时钟抬到远端水平"
    mine = b.add(title="B 的新改动")
    assert mine.rev > item.rev, "本机后续版本必须压得过远端的超前版本"


def test_conflict_loser_is_recoverable(root, remote):
    """B13：落败版本必须能从 conflict 表取回原文，不静默丢数据。"""
    a = machine(root, "a13", remote)
    item = a.add(title="初始")
    a.sync()
    b = machine(root, "b13", remote)
    b.sync()

    a.edit(item.id, title="A 的标题")
    a.sync()
    b.edit(item.id, title="B 的标题")
    report = b.sync()
    assert report.conflicts >= 1
    rows = b.store.db.execute("SELECT * FROM conflict").fetchall()
    assert rows, "冲突必须留档"
    assert "B 的标题" in rows[0]["local_json"] or "A 的标题" in rows[0]["remote_json"]


# --------------------------------------------------------------------------- A9–A13 删除语义


def test_local_delete_propagates_as_tombstone(root, remote):
    """A9：清单里删了、git 里还没删 → 同步后 git 出现墓碑，而不是文件消失。"""
    a = machine(root, "a9", remote)
    item = a.add(title="要删的")
    a.sync()
    b = machine(root, "b9", remote)
    b.sync()

    a.delete(item.id)
    a.sync()

    tree = a.git.read_tree(a.git.head())
    assert item.path() in tree, "墓碑必须还在仓库里（不是删文件）"
    assert parse_tree(tree)[item.id].deleted is True

    b.sync()
    assert b.item(item.id).deleted is True


def test_remote_delete_removes_locally(root, remote):
    """A10：另一台删了 → 本机进垃圾桶。"""
    a = machine(root, "a10", remote)
    item = a.add(title="会被远端删")
    a.sync()
    b = machine(root, "b10", remote)
    b.sync()

    b.delete(item.id)
    b.sync()
    a.sync()
    assert a.item(item.id).deleted is True


def test_delete_then_later_edit_resurrects(root, remote):
    """A12：甲删、乙改且乙更新 → 编辑胜出、任务复活。这是 LWW 的必然语义。"""
    a = machine(root, "a12", remote)
    item = a.add(title="复活候选")
    a.sync()
    b = machine(root, "b12", remote)
    b.sync()

    a.delete(item.id)                 # 先删
    b.edit(item.id, title="复活候选-改过")   # 后改（rev 更大）
    b.sync()
    a.sync()
    assert a.item(item.id).deleted is False, "更新的编辑应压过删除"
    assert "改过" in a.item(item.id).data["title"]


def test_double_delete_is_silent(root, remote):
    """A11：两边都删 → 结果仍是删除，且不记冲突（语义一致不打扰用户）。"""
    a = machine(root, "a11", remote)
    item = a.add(title="都删")
    a.sync()
    b = machine(root, "b11", remote)
    b.sync()

    a.delete(item.id)
    b.delete(item.id)
    ra = a.sync()
    rb = b.sync()
    assert a.item(item.id).deleted and b.item(item.id).deleted
    assert rb.conflicts == 0, "两边都删不该弹冲突"


# --------------------------------------------------------------------------- B4 协议外删除


def test_external_delete_without_tombstone_is_kept(root, remote):
    """B4：有人在网页上手工删文件（没有墓碑）→ 识别为异常，保留本地，不反向删。

    顺序必须是「先同步拿到 base → 远端被手工删 → 再同步」，
    否则新机器根本没拿到过这一项，谈不上"检测到消失"。
    """
    a = machine(root, "a4", remote)
    item = a.add(title="手工删除目标")
    a.sync()

    b = machine(root, "b4", remote)
    b.sync()                                  # b 拿到 item 与 base
    assert b.item(item.id) is not None

    # 模拟人工在网页上删文件并提交
    other = os.path.join(a.root, "saboteur")
    porcelain.clone(remote, other)
    os.remove(os.path.join(other, item.path()))
    porcelain.rm(other, paths=[item.path()])
    porcelain.commit(other, message=b"manual delete", author=b"x <x@y>", committer=b"x <x@y>")
    porcelain.push(other, remote, b"refs/heads/master")

    report = b.sync()
    assert any(x.startswith("external_delete") for x in report.anomalies), \
        f"必须报协议外删除，实际 anomalies={report.anomalies}"
    assert b.item(item.id) is not None and not b.item(item.id).deleted, "默认 keep，不删本地"


# --------------------------------------------------------------------------- B7 撞车恢复


def test_push_collision_recovers_within_three_rounds(root, remote):
    """B7：fetch 之后远端才被别人推进 → 我们被拒 → 自动重拉重合并再推。

    注意普通的「b 后同步」根本不会冲突，因为 b 的 sync 一开始就 fetch 到新提交了。
    真冲突是 fetch 与 push 之间的窗口里远端前进，所以用桩把第一次 fetch 结果变旧来复现竞态。
    """
    a = machine(root, "a7", remote)
    item = a.add(title="共同基线")
    a.sync()
    b = machine(root, "b7", remote)
    b.sync()
    stale = b.git.head()                       # b 已对齐到基线

    a.edit(item.id, title="A 抢先推")
    a.sync()                                   # 远端前进，b 不知情

    b.edit(item.id, title="B 后推")
    real_fetch = b.git.fetch
    calls = {"n": 0}

    def fetch_stale(url):
        calls["n"] += 1
        return stale if calls["n"] == 1 else real_fetch(url)

    b.git.fetch = fetch_stale
    report = b.sync()

    assert report.ok, f"B 应通过重试推上去，实际 error={report.error}"
    assert report.attempts >= 2, f"应经历过 PushRejected 重试，实际 attempts={report.attempts}"

    a.sync()
    assert a.item(item.id).data["title"] == b.item(item.id).data["title"], "最终必须收敛到同一个值"


# --------------------------------------------------------------------------- B19/B20/C5 编码与布局


def test_unicode_and_emoji_roundtrip(root, remote):
    """B19：中文、emoji、尖括号、引号在 git 往返后必须字节级一致。"""
    tricky = '整理《依法立案申请书》🐱 <img src=x> "引号" \'单引\' 反斜杠\\'
    a = machine(root, "a19", remote)
    item = a.add(title=tricky, note="多行\n中文备注")
    a.sync()
    b = machine(root, "b19", remote)
    b.sync()
    assert b.item(item.id).data["title"] == tricky
    assert b.item(item.id).data["note"] == "多行\n中文备注"


def test_files_use_lf_and_no_crlf(root, remote):
    """B20：Windows 上 git 默认会把 LF 转成 CRLF，会让整个文件每次都变。"""
    a = machine(root, "a20", remote)
    a.add(title="换行测试", note="第一行\n第二行")
    a.sync()
    blob = a.git.read_tree(a.git.head())[list(a.git.read_tree(a.git.head()))[0]]
    assert b"\r" not in blob, "仓库内容必须是 LF"


def test_repo_path_layout(root, remote):
    """C5：路径固定为 items/{kind}/{id}.json，扁平按 kind。"""
    a = machine(root, "a21", remote)
    task = a.add(kind="task", title="T")
    group = a.add(kind="list", name="G")
    a.sync()
    paths = set(a.git.read_tree(a.git.head()))
    assert task.path() == f"items/task/{task.id}.json"
    assert group.path() == f"items/list/{group.id}.json"
    assert {task.path(), group.path()} <= paths


# --------------------------------------------------------------------------- 架构红线


def test_sync_module_never_uses_pull():
    """矩阵 B 组的实现前提：合并只能在应用层做，git 的文本合并会毁掉 JSON 与墓碑。"""
    import ast
    from pathlib import Path
    src = Path("src/qingdan/core/sync.py")
    tree = ast.parse(src.read_text(encoding="utf-8"))
    called = {n.func.attr for n in ast.walk(tree)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    assert "pull" not in called


def test_bootstrap_never_writes_tombstones(root, remote):
    """B1 的强化版：新装机器即使远端有 100 项，也不该产生任何删除提交。"""
    a = machine(root, "ab", remote)
    for i in range(20):
        a.add(title=f"x{i}")
    a.sync()
    before = a.git.read_tree(a.git.head())

    b = machine(root, "bb", remote)
    b.sync()
    after = a.git.read_tree(a.git.head())
    assert before == after, "另一台机器 bootstrap 不该改变远端内容"
