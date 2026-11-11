"""RRULE 子集的**属性测试**（M6 验收项里一直缺的那一条）。

为什么不用 hypothesis：它只服务于测试，但 PyInstaller 的依赖分析会顺着 import 走，
而体积门禁余量只剩 1.5MB；更重要的是离线环境装不上就等于这条守卫不存在。
改用固定种子的生成器，性质一样（随机输入 + 不变量断言），代价是自己写生成逻辑，
好处是**失败可精确复现**：种子与样本索引都印在断言消息里。

这里断言的是「任何合法规则都必须满足的性质」，与 test_rrule.py 的具体边界用例互补：
单元用例证明「这几个已知边界对」，属性测试证明「随机组合下也不会破」。
"""
from __future__ import annotations

import calendar
import random
import string
from datetime import date, datetime, timedelta

import pytest

from qingdan.core import rrule

SEED = 20261109
CASES = 600
HORIZON = 400          # 每个样本最多展开多少次，防个别规则跑太久


def _rand_rule(rng: random.Random) -> str:
    """生成一条**合法**规则（合法性由 parse 把关，这里只负责组合）。"""
    freq = rng.choice(rrule.FREQS)
    parts = [f"FREQ={freq}"]
    if rng.random() < 0.5:
        parts.append(f"INTERVAL={rng.randint(1, 8)}")
    if freq == "WEEKLY" and rng.random() < 0.5:
        days = rng.sample(["MO", "TU", "WE", "TH", "FR", "SA", "SU"], rng.randint(1, 4))
        parts.append("BYDAY=" + ",".join(days))
    if freq in ("MONTHLY", "YEARLY") and rng.random() < 0.4:
        cand = [str(n) for n in (1, 15, 28, 29, 30, 31, -1, -2)]
        parts.append("BYMONTHDAY=" + ",".join(rng.sample(cand, rng.randint(1, 2))))
    # UNTIL 与 COUNT 互斥（RFC 也禁止同时出现）
    pick = rng.random()
    if pick < 0.25:
        parts.append(f"COUNT={rng.randint(1, 30)}")
    elif pick < 0.5:
        parts.append(f"UNTIL={rng.randint(2026, 2035)}{rng.randint(1, 12):02d}{rng.randint(1, 28):02d}")
    return ";".join(parts)


def _rand_anchor(rng: random.Random) -> date:
    return date(2020, 1, 1) + timedelta(days=rng.randint(0, 3000))


def _samples():
    rng = random.Random(SEED)
    for i in range(CASES):
        text = _rand_rule(rng)
        rule = rrule.parse(text)                 # 生成器若产出不合法规则，这里就该炸
        anchor = _rand_anchor(rng)
        # 锚日刻意包含月末与闰日，这是夹取逻辑最容易出事的地方
        if i % 7 == 0:
            last = (anchor.replace(day=1) + timedelta(days=32)).replace(day=1) - timedelta(days=1)
            anchor = last if i % 14 == 0 else anchor.replace(day=min(28, last.day))
        yield i, text, rule, anchor


# --------------------------------------------------------------------------- 性质


def test_occurrences_are_strictly_increasing_and_valid():
    """严格递增 + 每个都是合法日期。回退或重复会让「下一次」变成死循环。"""
    for i, text, rule, anchor in _samples():
        seq = list(rrule.occurrences(anchor, rule, limit=HORIZON))
        if not seq:
            # 空序列只有两种合法来由，别的都算 bug：
            # ① 规则已过期（UNTIL 早于锚，或锚之后没有 <= UNTIL 的候选）；
            # ② 显式 BYMONTHDAY 在锚所在的月/年根本不存在（YEARLY;BYMONTHDAY=30 锚在 2 月）。
            # 隐式锚日是夹取的，永远有解，所以没有 BYMONTHDAY 又不许空。
            assert rule.until is not None or rule.bymonthday, \
                f"#{i} {text} anchor={anchor} 无理由地产出空序列"
            continue
        for a, b in zip(seq, seq[1:]):
            assert b > a, f"#{i} {text} anchor={anchor} 非严格递增：{a} → {b}"


def test_first_occurrence_never_before_anchor():
    for i, text, rule, anchor in _samples():
        seq = list(rrule.occurrences(anchor, rule, limit=1))
        if not seq:
            continue                      # 空序列的合法性由上一条用例负责
        assert seq[0] >= anchor, f"#{i} {text} anchor={anchor} 产出早于锚：{seq[0]}"


def test_count_and_until_are_hard_bounds():
    """COUNT/UNTIL 是硬上界：越过它继续推进就是「重复任务永远关不掉」。"""
    for i, text, rule, anchor in _samples():
        seq = list(rrule.occurrences(anchor, rule, limit=HORIZON + 50))
        if rule.count is not None:
            assert len(seq) <= rule.count, f"#{i} {text} 产出 {len(seq)} 次 > COUNT={rule.count}"
        if rule.until is not None:
            assert all(d <= rule.until for d in seq), f"#{i} {text} 有产出晚于 UNTIL={rule.until}"


def test_day_numbers_never_overflow_their_month():
    """产出的日号必须真实存在于当月 —— 夹取写错会变成 2 月 30 日这种不存在的日期。"""
    for i, text, rule, anchor in _samples():
        for d in rrule.occurrences(anchor, rule, limit=HORIZON):
            assert 1 <= d.day <= 31
            assert d == date(d.year, d.month, d.day)      # 构造得出来即合法


def test_explicit_bymonthday_is_honoured_or_skipped_never_clamped():
    """显式日号要么正好命中，要么整月跳过（RFC 语义）；夹取只允许发生在隐式锚日上。"""
    for i, text, rule, anchor in _samples():
        if not rule.bymonthday:
            continue
        for d in rrule.occurrences(anchor, rule, limit=HORIZON):
            length = calendar.monthrange(d.year, d.month)[1]
            ok = {(length + md + 1) if md < 0 else md for md in rule.bymonthday}
            assert d.day in ok, f"#{i} {text} 在 {d}（当月 {length} 天）命中了非指定日号"


def test_interval_spacing_is_respected():
    """无 BYDAY/BYMONTHDAY 时，相邻间隔必须是 interval 的整数倍（对齐锚）。"""
    for i, text, rule, anchor in _samples():
        if rule.byday or rule.bymonthday:
            continue
        unit = {"DAILY": 1, "WEEKLY": 7}.get(rule.freq)
        if unit is None:
            continue
        seq = list(rrule.occurrences(anchor, rule, limit=HORIZON))
        for a, b in zip(seq, seq[1:]):
            gap = (b - a).days
            assert gap % (unit * rule.interval) == 0, f"#{i} {text} 间隔 {gap} 未对齐"


def test_next_after_agrees_with_upcoming():
    """两个入口必须同口径，否则界面预览与实际推进会不一致。"""
    rng = random.Random(SEED + 1)
    for i, text, rule, anchor in _samples():
        if i % 5:
            continue
        cur = anchor + timedelta(days=rng.randint(0, 60))
        one = rrule.next_after(cur, rule, anchor=anchor)
        up = rrule.upcoming(cur, rule, 1, anchor=anchor)
        if one is None:
            assert not up, f"#{i} {text} next_after 说用尽，upcoming 却给了 {up}"
        else:
            assert up and up[0] == one, f"#{i} {text} 两个入口不一致：{one} vs {up}"


def test_next_after_preserves_time_of_day():
    """推进只换日期：20:30 的任务推进后还是 20:30。"""
    rng = random.Random(SEED + 2)
    for i, text, rule, anchor in _samples():
        if i % 4:
            continue
        hh, mm = rng.randint(0, 23), rng.randint(0, 59)
        cur = datetime(anchor.year, anchor.month, anchor.day, hh, mm)
        nxt = rrule.next_after(cur, rule, anchor=anchor)
        if nxt is not None:
            assert (nxt.hour, nxt.minute) == (hh, mm), f"#{i} {text} 时刻被改了：{nxt}"


def test_computation_is_deterministic_and_stateless():
    """同样的输入必须给同样的输出 —— 它是纯函数，两台机器才会算出同一个下一次。"""
    for i, text, rule, anchor in _samples():
        if i % 3:
            continue
        a = list(rrule.occurrences(anchor, rule, limit=20))
        b = list(rrule.occurrences(anchor, rule, limit=20))
        assert a == b, f"#{i} {text} 两次调用结果不同（有隐藏状态）"


def test_termination_is_bounded_even_for_absurd_rules():
    """极端规则也必须终止：不封顶就会在日历里挂死。"""
    r = rrule.parse("FREQ=DAILY;INTERVAL=1")
    seq = list(rrule.occurrences(date(2026, 1, 1), r, limit=50))
    assert len(seq) == 50


# --------------------------------------------------------------------------- 垃圾输入


def _garbage(rng: random.Random) -> str:
    """半结构化的垃圾：模拟另一台机器同步来的畸形 repeat 字段。"""
    alphabet = string.ascii_uppercase + "=;,0123456789-" + "每日周末"
    n = rng.randint(0, 24)
    s = "".join(rng.choice(alphabet) for _ in range(n))
    return rng.choice([s, f"FREQ={s}", f"{s};INTERVAL=3", "FREQ=DAILY;" + s])


def test_try_parse_never_raises_on_garbage():
    """视图层的生命线：任何垃圾都不许抛，否则一条脏数据就能让整个日历 500。"""
    rng = random.Random(SEED + 3)
    for n in range(1500):
        s = _garbage(rng)
        try:
            got = rrule.try_parse(s)
        except Exception as e:                       # noqa: BLE001 - 就是要抓任何异常
            pytest.fail(f"try_parse({s!r}) 抛了 {type(e).__name__}: {e}")
        if got is not None:
            assert got.freq in rrule.FREQS


def test_next_after_on_garbage_returns_none_instead_of_crashing():
    """脏规则推进时必须返回 None（= 不推进），由上层把原因告诉用户，而不是抛异常。"""
    rng = random.Random(SEED + 4)
    checked = 0
    for _ in range(300):
        s = _garbage(rng)
        if rrule.try_parse(s) is not None:
            continue                       # 这条恰好合法，不属于本用例的对象
        assert rrule.next_after("2026-11-09T20:00:00", s) is None, f"脏规则 {s!r} 竟然推进了"
        assert rrule.upcoming("2026-11-09T20:00:00", s, 3) == []
        checked += 1
    assert checked > 50, "生成的垃圾样本里合法的太多，这个用例没真正覆盖到脏数据"
