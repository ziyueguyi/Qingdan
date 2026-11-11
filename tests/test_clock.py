"""HLC 时钟：三个坑（时钟偏差 / 同毫秒并发 / 时钟回拨）逐个验证。"""
from __future__ import annotations

import time

import pytest

from qingdan.core.clock import ABSENT, Clock, Rev


def test_tick_is_strictly_increasing():
    c = Clock("D-a")
    revs = [c.tick() for _ in range(200)]
    assert all(revs[i] < revs[i + 1] for i in range(len(revs) - 1)), "存在非递增的版本戳"


def test_same_millisecond_still_orders():
    """同一毫秒内两次发号必须可比 —— 裸时间戳在这里必然失败。"""
    c = Clock("D-a")
    fixed = 1_700_000_000_000
    c.last_ms, c.last_seq = fixed, 0
    orig = time.time
    time.time = lambda: fixed / 1000.0
    try:
        r1, r2 = c.tick(), c.tick()
    finally:
        time.time = orig
    assert r1.ms == r2.ms == fixed
    assert r1 < r2 and r2.seq == r1.seq + 1


def test_clock_rollback_is_clamped():
    """系统时间被往回改，版本不能倒退。"""
    c = Clock("D-a")
    orig = time.time
    time.time = lambda: 1_800_000_000.0
    try:
        high = c.tick()
    finally:
        time.time = orig
    c2_ms = high.ms - 10 ** 7          # 往回拨约 115 天
    orig2 = time.time
    time.time = lambda: c2_ms / 1000.0
    try:
        after = c.tick()
    finally:
        time.time = orig2
    assert after > high, "时钟回拨导致版本倒退"
    assert after.ms >= high.ms


def test_device_id_is_final_tiebreak():
    """两台机器同一毫秒同一 seq，必须仍能分出胜负且结果可复现。"""
    a, b = Rev(100, 0, "D-aaa"), Rev(100, 0, "D-bbb")
    assert (a < b) == ("D-aaa" < "D-bbb")
    assert a != b


def test_offset_makes_skewed_clock_comparable():
    """A 机慢 8 分钟：校正 offset 后，它的新改动不应被 B 机的旧版本压住。"""
    FIXED = 1_700_000_000.0
    orig = time.time
    try:
        time.time = lambda: FIXED - 480          # A 机墙钟慢 8 分钟
        slow = Clock("D-slow")
        skewed = slow.tick()
        time.time = lambda: FIXED                # B 机时间正确
        ref = Clock("D-fast").tick()
    finally:
        time.time = orig
    assert skewed < ref, "未校正时慢机的改动确实被压住了（这正是裸时间戳的故障）"

    slow.set_offset(8 * 60 * 1000)               # 用服务器 Date 头校正
    orig2 = time.time
    try:
        time.time = lambda: FIXED - 480
        corrected = slow.tick()
    finally:
        time.time = orig2
    assert corrected.ms == ref.ms, "校正后两台机器的 ms 应落在同一时刻，不再系统性偏后"


def test_observe_guarantees_next_tick_exceeds_remote():
    c = Clock("D-me")
    remote = Rev(c.last_ms + 10 ** 9, 7, "D-other")
    c.observe(remote)
    assert c.tick() > remote


def test_observe_ignores_own_device():
    c = Clock("D-me", last_ms=500, last_seq=3)
    c.observe(Rev(9_999, 9, "D-me"))
    assert c.last_ms == 500 and c.last_seq == 3


def test_absent_is_oldest():
    c = Clock("D-a")
    assert ABSENT < c.tick()
    assert ABSENT < Rev(0, 0, "D-z")


def test_state_roundtrip():
    c = Clock("D-a", offset_ms=120)
    for _ in range(5):
        c.tick()
    d = c.state()
    r = Clock.restore(d, "D-default")
    assert (r.dev, r.offset_ms, r.last_ms, r.last_seq) == (c.dev, c.offset_ms, c.last_ms, c.last_seq)
    assert r.tick() > Rev(d["last_ms"], d["last_seq"], d["dev"]), "恢复后必须继续单调递增"


def test_empty_device_id_rejected():
    with pytest.raises(ValueError):
        Clock("")
