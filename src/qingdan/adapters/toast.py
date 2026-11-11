"""Windows 系统通知适配器（M3 / A6）。

放在 `adapters/` 而不是 `core/`：它依赖平台相关能力，而 core 必须保持平台中立
（`tests/test_layers.py` 把 winotify / pystray 列为 core 禁用依赖）。由 bootstrap 注册。

**后端优先级是实测出来的，不是猜的**：

  1. `balloon` —— 托盘气球（Shell_NotifyIcon）。**不需要 AUMID**，未打包应用也能显示。
     由 shell 在托盘启动后注入 notifier。
  2. `dialog`   —— `ctypes` MessageBoxW。一定能看见，但模态、样式差。
  3. `toast`    —— winotify。实测：调用不抛异常、内部 PowerShell 也返回成功，
     但 **Windows 会静默丢弃未打包应用发的 toast**，用户什么都看不到。
     打包并注册 AUMID 之后才值得优先用。

默认 `dialog`；托盘一起来就自动升到 `balloon`。
"""
from __future__ import annotations

import threading
import time
from typing import Callable

from qingdan.core import stamp
from qingdan.core.alerting.base import AlertMessage, SendResult

APP_ID = "Qingdan 清单"

#: shell 启动托盘后注入的通知回调 (title, body) -> None
_tray_notifier: Callable[[str, str], None] | None = None


def set_tray_notifier(fn: Callable[[str, str], None] | None) -> None:
    global _tray_notifier
    _tray_notifier = fn


class ToastChannel:
    name = "toast"

    def __init__(self, config_getter: Callable[[], dict],
                 notifier: Callable[[str, str], None] | None = None):
        self._config = config_getter
        self._notifier = notifier          # 测试注入用
        self._detected = self._detect()

    def _detect(self) -> str:
        if self._notifier:
            return "injected"
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW
        except Exception:
            return "none"
        return "dialog"

    @property
    def _want(self) -> str:
        c = self._config()
        return (c.get("toast") or {}).get("backend") or "dialog"

    @property
    def backend(self) -> str:
        """实际用哪个后端。托盘在跑就优先气球——它不受 AUMID 限制。"""
        if self._notifier:
            return "injected"
        if _tray_notifier is not None and self._want != "toast":
            return "balloon"
        if self._want in ("toast", "auto"):
            try:
                import winotify                                  # noqa: F401
                return "toast" if self._want == "toast" else "dialog"
            except Exception:
                return "dialog"
        return "dialog" if self._detected != "none" else "none"

    def is_configured(self) -> bool:
        """只回答"本机有没有可用的通知方式"，**不读运行时配置**。

        渠道是在 bootstrap 导入时注册的，那时上下文还没建立；这里一旦去读
        alert 配置就会炸（实测踩过）。配置相关的判断一律归 `enabled()`。
        """
        return self._detected != "none"

    def enabled(self) -> bool:
        c = self._config()
        return bool((c.get("channels") or {}).get("toast") and c.get("master"))

    @staticmethod
    def compose(msgs: list[AlertMessage]) -> tuple[str, str]:
        if len(msgs) == 1:
            m = msgs[0]
            return (m.title, " · ".join(x for x in (m.when_text, m.group_name,
                                                    "已逾期" if m.overdue else "") if x))
        return (f"{len(msgs)} 条提醒", "\n".join(m.line().lstrip("- ") for m in msgs[:8]))

    def _show(self, title: str, body: str) -> None:
        backend = self.backend
        if backend == "injected":
            self._notifier(title, body)
            return
        if backend == "balloon":
            _tray_notifier(title, body)
            return
        if backend == "toast":
            from winotify import Notification
            Notification(app_id=APP_ID, title=title, msg=body or " ", duration="long").show()
            return
        import ctypes
        # MessageBoxW 是阻塞调用，必须丢独立线程，否则卡死调度循环
        threading.Thread(target=ctypes.windll.user32.MessageBoxW,
                         args=(0, body or title, title, 0), daemon=True).start()

    def send(self, msgs: list[AlertMessage]) -> SendResult:
        if not msgs:
            return SendResult(ok=True, sent_count=0, covered=0)
        title, body = self.compose(msgs)
        try:
            self._show(title, body)
            return SendResult(ok=True, sent_count=len(msgs), covered=len(msgs),
                              detail={"backend": self.backend})
        except Exception as e:
            return SendResult(ok=False, code="NOTIFY_FAILED",
                              message=f"{type(e).__name__}: {e}", covered=len(msgs),
                              detail={"backend": self.backend})

    def test(self) -> SendResult:
        r = self.send([AlertMessage(item_id="__test__", kind="task", title="系统通知测试",
                                    fire_at_ms=stamp.now_ms(), when_text="现在")])
        r.detail["test"] = True
        return r
