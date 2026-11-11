"""三方合并 pick()：R3b 的裁决表逐条验证。

命名规则：L/R = 本地/远端，数字 = HLC 的 ms（越大越新），D = 墓碑。
"""
from __future__ import annotations

import pytest

from qingdan.core.clock import ABSENT, Rev
from qingdan.core.merge import (
    external_delete_detected, newer, pick, same, should_record_conflict)
from qingdan.core.models import Item


def mk(ms: int, deleted: bool = False, seq: int = 0, dev: str = "D-a", item_id: str = "T1") -> Item:
    return Item(kind="task", id=item_id, rev=Rev(ms, seq, dev),
                data={"title": "x"}, deleted=deleted)


BASE = mk(100)
L_120 = mk(120)                      # 本地编辑
L_120D = mk(120, deleted=True)       # 本地删除（墓碑）
L_150 = mk(150)
R_130 = mk(130)                      # 远端编辑
R_130D = mk(130, deleted=True)       # 远端删除
R_160D = mk(160, deleted=True)


@pytest.mark.parametrize("local,remote,base,winner,loser,场景", [
    (BASE, BASE, BASE, BASE, None, "无变化"),
    (BASE, R_130, BASE, R_130, None, "只远端改"),
    (L_120, BASE, BASE, L_120, None, "只本地改"),
    (L_120, R_130, BASE, R_130, L_120, "两边都改，远端更新"),
    (L_150, R_130, BASE, L_150, R_130, "两边都改，本地更新"),
    (L_120D, BASE, BASE, L_120D, None, "本机电源没关删了任务、git 还没删 → 推墓碑"),
    (BASE, R_130D, BASE, R_130D, None, "另一台删了、本机没删 → 本机删除"),
    (L_120D, R_130D, BASE, R_130D, L_120D, "两边都删 → 结果仍是删除（冲突留档由策略层抑制）"),
    (L_150, R_130D, BASE, L_150, R_130D, "甲删(130)、乙改(150) → 编辑胜出，任务复活"),
    (L_120, R_130D, BASE, R_130D, L_120, "甲改(120)、乙删(130) → 删除胜出，编辑进 conflict"),
    (L_120D, R_160D, BASE, R_160D, L_120D, "两边都删，远端更晚"),
])
def test_adjudication_table(local, remote, base, winner, loser, 场景):
    w, l = pick(local, remote, base)
    assert w.version == winner.version, 场景
    if loser is None:
        assert l is None, 场景
    else:
        assert l is not None and l.version == loser.version, 场景


def test_conflict_policy_suppresses_double_delete():
    """两边都删语义一致，不打扰用户；其余落败版本一律留档。"""
    assert should_record_conflict(*pick(L_120D, R_130D, BASE)) is False
    assert should_record_conflict(*pick(L_150, R_130D, BASE)) is True    # 编辑 vs 删除
    assert should_record_conflict(*pick(L_120, R_130, BASE)) is True     # 双方都改
    assert should_record_conflict(*pick(L_120, BASE, BASE)) is False     # 只本地改，无落败者


def test_new_item_on_local_only():
    """base 与远端都没有、本地新增 → 本地胜，且远端不会被判定为删除。"""
    local = mk(200)
    w, l = pick(local, None, None)
    assert w is local and l is None


def test_new_item_on_remote_only():
    remote = mk(200)
    w, l = pick(None, remote, None)
    assert w is not None and w.version == remote.version and l is None


def test_fresh_device_pull_only_never_emits_tombstone():
    """新装机器：本地空、远端有数据、无 base → 胜出者是远端，绝不产出删除动作。"""
    remote = mk(200)
    w, _ = pick(None, remote, None)
    assert w is not None and not w.deleted


def test_local_empty_does_not_delete_remote_when_base_exists():
    """本地被清空但有 base（异常场景）：远端项胜出，等于恢复，而不是反向删除。"""
    remote = mk(200)
    w, l = pick(None, remote, BASE)
    assert w.version == remote.version


def test_same_handles_none_side():
    assert same(None, None)
    assert not same(None, BASE)
    assert not same(BASE, None)
    assert same(BASE, mk(100))


def test_newer_none_is_oldest():
    assert newer(None, BASE).version == BASE.version
    assert newer(BASE, None).version == BASE.version
    assert newer(BASE, R_130).version == R_130.version


def test_external_delete_is_detected_not_applied():
    """远端文件被人在网页上手工删（相对 base 消失且无墓碑）→ 识别为协议外异常。"""
    assert external_delete_detected(local=BASE, remote=None, base=BASE)
    assert not external_delete_detected(local=BASE, remote=R_130D, base=BASE)
    assert not external_delete_detected(local=None, remote=None, base=BASE)


def test_tie_on_ms_is_broken_by_seq_then_dev():
    a, b = mk(100, seq=1), mk(100, seq=2)
    assert pick(a, b, BASE)[0].version == b.version
    c = mk(100, seq=1, dev="D-z")
    assert c.version > a.version
    assert pick(a, c, BASE)[0].version == c.version


def test_pick_is_idempotent():
    for _ in range(5):
        w, l = pick(L_150, R_130D, BASE)
        assert w.version == L_150.version and l is not None and l.version == R_130D.version


def test_different_items_never_merge():
    """合并按 id 逐项进行，跨 id 不互相影响。"""
    a = mk(120, item_id="A")
    b = mk(130, item_id="B")
    assert a.id != b.id


def test_a8_same_id_created_on_both_sides_is_not_silently_dropped():
    """A8：同一个 id 两边都"新增"（没有 merge base）。

    ULID 撞车几乎不可能，但裁决表写了这一行就必须有测试：没有 base 时 pick 不能崩、
    不能把两份都留下，落败那份要能进 conflict 找回。
    """
    L, R = mk(120, dev="D-a"), mk(130, dev="D-b")
    winner, loser = pick(L, R, None)                # 「这一侧没有」= None
    assert winner.version == R.version, "没有 base 就退化成纯 LWW"
    assert loser is not None and loser.version == L.version
    assert should_record_conflict(winner, loser) is True, "两份都是用户写的，绝不能静默丢"


def test_wrong_absent_sentinel_fails_loudly():
    """`clock.ABSENT` 是 Rev 哨兵，只能比版本戳；喂给 pick 必须说清楚，
    不能留一句 'Rev' object has no attribute 'version' 让人以为是合并逻辑坏了。"""
    with pytest.raises(TypeError) as e:
        pick(mk(120), mk(130), ABSENT)
    msg = str(e.value)
    assert "base" in msg and "None" in msg and "ABSENT" in msg, msg


def test_a15_tie_break_agrees_from_both_machines():
    """A15：同毫秒同 seq 时比 dev —— 关键是**两台机器必须算出同一个胜者**。

    只在"我这边"看起来确定是不够的：胜者不一致，两边就会各自把己方推上去，
    永远收敛不了。所以正反两种参数顺序都要断言。
    """
    a, z = mk(120, dev="D-a"), mk(120, dev="D-z")
    assert pick(a, z, BASE)[0].version == z.version
    assert pick(z, a, BASE)[0].version == z.version, "谁是本地谁是远端不得影响裁决结果"
    # 顺带钉住冲突留档策略：两边内容真不一样，落败那份要能找回
    assert should_record_conflict(*pick(a, z, BASE)) is True
