"""统一时间缝（唯一允许"读当下"的地方）。

规矩：**凡是要和被写入数据的时间戳做比较的**，都必须从这里取时间 ——
`created_at`、`done_at`、`snoozed_until`、`deleted_at_ms`，以及只读视图里
"今天"的默认值。两边不同源，测试就只能在"跑测试那天刚好等于写死的那天"时对。

实测踩坑：视图测试一直传 `today="2026-11-05"`，而 `task.complete` 用真实墙钟写
`done_at`。日历跨到 11 月 6 日凌晨，`stats.today` 的 `done_today` 从 1 变 0 ——
断言不是写错了，是**每天对一次**，迟早会在别人毫无心理准备时红一片。

**不走这里**的三类，是故意的：

- `clock.Clock` 的版本戳发号：它有自己的 HLC 语义（含服务器 offset 与回拨钳制）。
- 备份/导出文件名与 `alert_log.sent_at`：真实时刻，不参与"今天"的裁决。
- git 提交的 author time：属于协议外部数据。
"""
from __future__ import annotations

from datetime import datetime
from typing import Callable

#: 想钉住时间就替换它（测试里 `stamp.set_clock(lambda: datetime(2026, 11, 5, 20, 0))`）。
_CLOCK: Callable[[], datetime] = datetime.now


def set_clock(clock: Callable[[], datetime] | None) -> None:
    """测试注入。传 None 恢复真实墙钟 —— 恢复必须是显式的，别靠"下个测试碰不到"。"""
    global _CLOCK
    _CLOCK = clock if clock is not None else datetime.now


def now_dt() -> datetime:
    return _CLOCK()


def now_text() -> str:
    """`2026-11-05T20:08:13` —— 写进 item.data 的文本时间戳。"""
    return _CLOCK().strftime("%Y-%m-%dT%H:%M:%S")


def now_day() -> str:
    """`2026-11-05` —— 视图里"今天"的默认值。"""
    return _CLOCK().strftime("%Y-%m-%d")


def now_ms() -> int:
    return int(_CLOCK().timestamp() * 1000)
