"""开机自启（M6）。

写 `HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run`，只影响当前用户，
不需要管理员权限，也不碰机器级配置。

两个刻意的设计：

1. **注册表后端可注入**。core 层不在模块级 import winreg，这样"自启状态"的逻辑
   能被完整单测覆盖，而不是只能在真机上点。
2. **开发态拒绝注册**。冻结前 `sys.executable` 是 python.exe，把它写进 Run 键
   等于每次开机拉起一个解释器进程 —— 那不是自启，是故障。所以非 frozen 一律返回原因。
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Callable, Protocol

from qingdan.core.registry import feature

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "Qingdan"


class RegistryBackend(Protocol):
    def get(self, name: str) -> str | None: ...
    def set(self, name: str, value: str) -> None: ...
    def delete(self, name: str) -> None: ...


class WinRegBackend:
    """真实实现。winreg 在方法内部导入，模块导入本身不依赖 Windows。"""

    def _key(self, access):
        import winreg
        return winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, access)

    def get(self, name: str) -> str | None:
        import winreg
        try:
            with self._key(winreg.KEY_READ) as key:
                value, _ = winreg.QueryValueEx(key, name)
                return str(value)
        except OSError:
            return None

    def set(self, name: str, value: str) -> None:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)

    def delete(self, name: str) -> None:
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, name)
        except OSError:
            pass


class FakeRegistry:
    """测试用。"""

    def __init__(self):
        self.values: dict[str, str] = {}

    def get(self, name: str) -> str | None:
        return self.values.get(name)

    def set(self, name: str, value: str) -> None:
        self.values[name] = value

    def delete(self, name: str) -> None:
        self.values.pop(name, None)


@dataclass
class Autostart:
    registry: RegistryBackend
    exe_path: Callable[[], str]
    frozen: Callable[[], bool]

    def command(self) -> str:
        path = self.exe_path()
        return f'"{path}"' if " " in path else path

    def status(self) -> dict:
        current = self.registry.get(VALUE_NAME)
        return {
            "enabled": bool(current),
            "command": current,
            "expected": self.command(),
            "frozen": self.frozen(),
            # 只出机器键：这句中文曾经直接写进响应体，英文界面里就会残留中文。
            # 展示文案归前端 i18n（`autostart_dev`），后端只说"为什么"是哪个原因。
            "reason_key": None if self.frozen() else "dev_mode",
        }

    def enable(self) -> dict:
        st = self.status()
        if not st["frozen"]:
            return {**st, "changed": False}
        self.registry.set(VALUE_NAME, st["expected"])
        return {**self.status(), "changed": True}

    def disable(self) -> dict:
        if self.registry.get(VALUE_NAME) is None:
            return {**self.status(), "changed": False}
        self.registry.delete(VALUE_NAME)
        return {**self.status(), "changed": True}

    def repair_if_stale(self) -> dict | None:
        """exe 移动过位置后 Run 键会指向旧路径 —— 开机静默失败，最难查的那种。
        启动时自查一次，发现不一致就纠正。"""
        current = self.registry.get(VALUE_NAME)
        expected = self.command()
        if current and self.frozen() and current != expected:
            self.registry.set(VALUE_NAME, expected)
            return {"repaired_from": current, "repaired_to": expected}
        return None


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def default_autostart() -> Autostart:
    return Autostart(WinRegBackend(), lambda: sys.executable, is_frozen)


# --------------------------------------------------------------------------- 接口（R4）

_manager: Autostart | None = None


def set_manager(m: Autostart) -> None:
    """测试可注入 FakeRegistry 版本。"""
    global _manager
    _manager = m


def _mgr() -> Autostart:
    global _manager
    if _manager is None:
        _manager = default_autostart()
    return _manager


@feature(name="app.autostart_get", group="app", http=("GET", "/api/v1/app/autostart"),
         idempotent=True)
def app_autostart_get() -> dict:
    return _mgr().status()


@feature(name="app.autostart_set", group="app", http=("POST", "/api/v1/app/autostart"))
def app_autostart_set(on: bool = True) -> dict:
    return _mgr().enable() if on else _mgr().disable()
