"""平台相关适配器。core 保持中立，具体渠道在这里注册进 alerting 的注册表。

为什么需要显式注册而不是导入即注册：渠道依赖本机能力（有没有 winotify、
是不是 Windows），注册时机应在上下文初始化之后，且测试里要能换成假适配器。
"""
from __future__ import annotations

from qingdan.core.alerting import register_channel
from qingdan.core.alerting.dingtalk import DingTalkChannel
from qingdan.core.alerting.emailch import EmailChannel
from qingdan.core.alerting.config import get_config

DEFAULT_REGISTRATIONS = ("dingtalk", "email", "toast")


def register_all(only: tuple[str, ...] = DEFAULT_REGISTRATIONS) -> list[str]:
    """注册可用渠道。返回实际注册上的名字。"""
    registered = []
    if "dingtalk" in only:
        register_channel(DingTalkChannel(get_config))
        registered.append("dingtalk")
    if "email" in only:
        register_channel(EmailChannel(get_config))
        registered.append("email")
    if "toast" in only:
        from qingdan.adapters.toast import ToastChannel
        channel = ToastChannel(get_config)
        if channel.is_configured():
            register_channel(channel)
            registered.append("toast")
    return registered
