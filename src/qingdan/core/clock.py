"""HLC 混合逻辑时钟 —— 「以最新更新时间为准」的正确实现（开发文档 §10）。

裸比墙钟有三个必炸的坑，这里逐个堵住：

| 坑 | 后果 | 对策 |
|---|---|---|
| 机器时钟慢/快几分钟 | 新改动被旧版本覆盖 | `offset_ms` 用服务器 Date 头校正后才算 ms |
| 同一毫秒两台机器各改一次 | 时间戳相等，结果不确定 | 复合序 `(ms, seq, dev)`，dev 做最终 tie-break |
| 时钟回拨（改系统时间） | 版本倒退、合并乱套 | ms 钳制为历史最大值，seq 单调递增 |

所以「最新」的严格定义 = `(ms, seq, dev)` 三元组的字典序较大者胜出（LWW）。
"""
from __future__ import annotations

import time
from dataclasses import dataclass


@dataclass(frozen=True, order=True)
class Rev:
    """字段顺序即比较顺序，dataclass(order=True) 直接给出 HLC 的字典序语义。"""

    ms: int
    seq: int
    dev: str

    def as_tuple(self) -> tuple[int, int, str]:
        return (self.ms, self.seq, self.dev)

    def to_json(self) -> dict:
        return {"ms": self.ms, "seq": self.seq, "dev": self.dev}

    @staticmethod
    def from_json(d: dict) -> "Rev":
        return Rev(int(d["ms"]), int(d["seq"]), str(d["dev"]))


#: 表示「这条记录在这个版本上不存在」。参与比较时永远最旧。
ABSENT = Rev(-1, -1, "")


class Clock:
    """单台设备的版本发号器。

    `dev` 是设备指纹（8 位），同机重启必须不变，所以由外部持久化后传进来。
    内部维护 `(last_ms, last_seq)`，调用方负责持久化（见 Store）。
    """

    def __init__(self, dev: str, offset_ms: int = 0, last_ms: int = 0, last_seq: int = 0):
        if not dev:
            raise ValueError("dev 不能为空")
        self.dev = dev
        self.offset_ms = int(offset_ms)
        self.last_ms = int(last_ms)
        self.last_seq = int(last_seq)

    # -- 发号 --------------------------------------------------------------
    def tick(self) -> Rev:
        wall = int(time.time() * 1000) + self.offset_ms
        if wall > self.last_ms:
            self.last_ms, self.last_seq = wall, 0
        else:
            # 墙钟没前进（同毫秒或回拨）→ ms 钳制在历史最大值，只推 seq
            self.last_seq += 1
        return Rev(self.last_ms, self.last_seq, self.dev)

    # -- 观察外部版本 ------------------------------------------------------
    def observe(self, rev: Rev) -> None:
        """收到远端/他人版本后调用，保证本机下一个 tick 严格大于它。

        不做这件事的话，一台时钟超前的机器会把我们的后续改动「压」在下面。
        """
        if rev.dev == self.dev:
            return
        if rev.ms > self.last_ms:
            self.last_ms, self.last_seq = rev.ms, 0
        elif rev.ms == self.last_ms and rev.seq > self.last_seq:
            self.last_seq = rev.seq

    # -- 状态持久化 --------------------------------------------------------
    def state(self) -> dict:
        return {"dev": self.dev, "offset_ms": self.offset_ms,
                "last_ms": self.last_ms, "last_seq": self.last_seq}

    @classmethod
    def restore(cls, state: dict | None, default_dev: str) -> "Clock":
        if not state:
            return cls(default_dev)
        return cls(
            dev=state.get("dev") or default_dev,
            offset_ms=state.get("offset_ms", 0),
            last_ms=state.get("last_ms", 0),
            last_seq=state.get("last_seq", 0),
        )

    def set_offset(self, offset_ms: int) -> None:
        """用 HTTP 响应的 Date 头校准：offset = 服务器时间 - 本地时间。"""
        self.offset_ms = int(offset_ms)

    def clock_skew_ms(self) -> int:
        """本机墙钟（已含 offset）与原始墙钟的偏离量，用于 §21 的「偏差过大拒绝 push」。"""
        return int(time.time() * 1000) - (self.last_ms - self.last_seq)
