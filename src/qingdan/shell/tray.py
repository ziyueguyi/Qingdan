"""系统托盘（M6）。

放在 `shell/` 而不是 `core/`：pystray 是平台相关依赖，`tests/test_layers.py`
把 core 层的 pystray 列为禁用。

托盘存在的意义不只是"有个图标"：**关窗口不等于退出程序**。提醒调度器跟着进程活着，
用户点 X 就退出的话，后面所有到期提醒都不会发。所以默认行为是
「关窗口 → 隐藏到托盘」，只有托盘菜单里的"退出"才真的结束进程。
"""
from __future__ import annotations

import sys
import threading
from pathlib import Path

APP_NAME = "清单"


def icon_candidates(icon_path: Path | None = ()) -> list[Path]:
    """托盘图标的可能位置：显式传入 → 冻结包内 → 源码目录。

    冻结后 `Path(__file__).parents[2]/"packaging"` 不存在（packaging 不进包），
    资源在 sys._MEIPASS 下，所以两条都要列出来。
    """
    out: list[Path] = []
    if icon_path:
        out.append(Path(icon_path))
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", "."))
        out.append(base / "qingdan" / "assets" / "icon.png")
    out.append(Path(__file__).resolve().parents[3] / "packaging" / "icon.png")
    return out


class Tray:
    """pystray 托盘。没装 pystray 或没有图标文件时整体降级为不可用，不影响主流程。"""

    def __init__(self, on_show, on_quit, on_toggle_alerts, is_visible, alerts_enabled):
        self._on_show = on_show
        self._on_quit = on_quit
        self._on_toggle_alerts = on_toggle_alerts
        self._is_visible = is_visible
        self._alerts_enabled = alerts_enabled
        self.icon = None
        self._thread: threading.Thread | None = None
        self.available = False

    def start(self, icon_path: Path | None = None) -> bool:
        try:
            import pystray
            from PIL import Image
        except Exception:
            return False

        image = None
        for candidate in icon_candidates(icon_path):
            if Path(candidate).is_file():
                try:
                    image = Image.open(candidate).convert("RGBA")
                    break
                except Exception:
                    continue
        if image is None:
            image = Image.new("RGBA", (64, 64), (47, 111, 237, 255))

        menu = pystray.Menu(
            pystray.MenuItem(lambda item: "显示主窗口" if not self._is_visible() else "隐藏主窗口",
                             self._handle_show, default=True),
            pystray.MenuItem(lambda item: "提醒已开启" if self._alerts_enabled() else "提醒已静音",
                             self._handle_alerts, checked=lambda item: self._alerts_enabled()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("退出", self._handle_quit),
        )
        self.icon = pystray.Icon("qingdan", image, APP_NAME, menu)
        self._thread = threading.Thread(target=self.icon.run, daemon=True, name="qingdan-tray")
        self._thread.start()
        # run() 是异步起循环的，等它真的就绪再返回，否则第一次 notify 可能落空
        self.available = _wait_for_icon(self.icon, self._thread)
        return self.available

    def _handle_show(self, icon=None, item=None):
        self._on_show()

    def _handle_alerts(self, icon=None, item=None):
        self._on_toggle_alerts()

    def _handle_quit(self, icon=None, item=None):
        self.stop()
        self._on_quit()

    def notify(self, title: str, body: str) -> bool:
        """托盘气球通知。

        走 Shell_NotifyIcon，**不需要 AUMID** —— 与 winotify 的 toast 不同，
        未打包应用也能正常显示（A0 实测：winotify 调用不报错但系统静默丢弃）。
        """
        if self.icon is None:
            return False
        try:
            self.icon.notify(body, title)
            return True
        except Exception:
            return False

    def stop(self) -> None:
        if self.icon is not None:
            try:
                self.icon.stop()
            except Exception:
                pass
        self.icon = None
        self._thread = None
        self.available = False


def _wait_for_icon(icon, thread, timeout: float = 3.0) -> bool:
    import time

    deadline = time.time() + timeout
    while time.time() < deadline:
        if getattr(icon, "visible", False):
            return True
        if not thread.is_alive():
            return False
        time.sleep(0.05)
    return thread.is_alive()
