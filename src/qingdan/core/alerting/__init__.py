"""告警子系统（M3）。

core 层保持平台中立：Windows 通知适配器放在 `qingdan/adapters/toast.py`，
由 `qingdan.bootstrap` 注册进来。这样 `tests/test_layers.py` 的依赖红线才有意义。
"""
from qingdan.core.alerting.base import (  # noqa: F401
    AlertChannel,
    AlertMessage,
    SendResult,
    get_channel,
    register_channel,
)
