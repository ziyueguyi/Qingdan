"""告警子系统的渠道框架（M3 / A3）。

两个刻意的设计：

1. **`send` 接收消息列表而不是单条**。钉钉自定义机器人限流 20 条/分钟，超限封禁
   10 分钟，所以同一次触发窗口内的多条提醒必须合并成一条消息。合并是硬需求，
   接口形状就得先支持，否则后面要改所有调用点。

2. **core 层不 import 任何平台相关依赖**。Windows 通知（winotify）放在
   `qingdan/adapters/toast.py`，由 bootstrap 注册 —— 这样 core 保持平台中立，
   `tests/test_layers.py` 的依赖红线才有意义。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass
class AlertMessage:
    """一条待推送的提醒。"""

    item_id: str
    kind: str                       # task | countdown
    title: str
    fire_at_ms: int
    when_text: str = ""             # 已格式化的时间文案
    group_name: str = ""
    priority: int = 4
    overdue: bool = False
    days_left: int | None = None    # 倒数日专用
    revision: dict | None = None   # Local notification actions bind to this Item version.

    def line(self) -> str:
        if self.kind == "countdown" and self.days_left is not None:
            tail = f"还剩 {self.days_left} 天" if self.days_left >= 0 else f"已过 {-self.days_left} 天"
            return f"- **{self.title}** {tail}"
        mark = "⚑" if self.priority < 4 else "•"
        bits = [f"- {mark} {self.title}"]
        if self.when_text:
            bits.append(f"（{self.when_text}）")
        if self.overdue:
            bits.append("已逾期")
        if self.group_name:
            bits.append(f"@{self.group_name}")
        return "".join(bits)


@dataclass
class SendResult:
    ok: bool
    code: str = ""                  # 失败时的机器码，如 DING_ERRCODE_310000
    message: str = ""               # 可直接展示给用户的原因
    detail: dict[str, Any] = field(default_factory=dict)
    sent_count: int = 0             # 合并后实际推送的消息条数
    covered: int = 0                # 这次发送覆盖了多少条提醒

    def as_dict(self) -> dict:
        return {"ok": self.ok, "code": self.code, "message": self.message,
                "detail": self.detail, "sent_count": self.sent_count, "covered": self.covered}


@runtime_checkable
class AlertChannel(Protocol):
    """渠道适配器契约。新增渠道 = 实现它 + 注册 + 一个 @feature，主链路零改动。"""

    name: str

    def is_configured(self) -> bool:
        """配置是否齐全（没配好就不该尝试发送，也不该在 UI 上显示为可用）。"""
        ...

    def enabled(self) -> bool:
        """用户是否开启了这个渠道。"""
        ...

    def send(self, msgs: list[AlertMessage]) -> SendResult:
        """发送（允许/要求内部合并）。"""
        ...

    def test(self) -> SendResult:
        """设置页「发送测试」。每个渠道都必须实现，否则配置错了要等到期才发现。"""
        ...


# --------------------------------------------------------------------------- 注册表

CHANNELS: dict[str, AlertChannel] = {}


def register_channel(channel: AlertChannel) -> None:
    CHANNELS[channel.name] = channel


def get_channel(name: str) -> AlertChannel | None:
    return CHANNELS.get(name)


def active_channels() -> list[AlertChannel]:
    return [c for c in CHANNELS.values() if c.enabled() and c.is_configured()]
