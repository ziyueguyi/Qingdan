"""单实例互斥（开发文档 §17）。

为什么这件事不是"体验优化"而是数据正确性问题：`device_id` 持久化在 settings 表里
（见 Store.__init__），两个实例同时打开同一个数据目录时——

1. 各自从盘上 restore 一份 `Clock(last_ms, last_seq)`，各自独立发号。同一毫秒里
   两边都会算出 `Rev(wall, 0, dev)`，**三元组完全相同**；LWW 靠 seq 决胜负的那一半
   当场失效，谁覆盖谁变成看运气。
2. `Clock.observe()` 对 `rev.dev == self.dev` 直接 return —— 因为设计上"同一台设备
   的版本不用防"。两个进程共用 dev 时这条优化正好把唯一的补救通道也关掉了。
3. 两个告警调度线程都会扫同一批 alert_job → **同一条提醒推两遍**。
4. `apply_pending_restore` / `daily_backup` 会在另一个进程正在使用的目录上换主库文件。

锁用 **Windows 命名互斥体（内核对象）**而不是锁文件：进程被杀、蓝屏、崩溃，内核对象
都会被系统回收；锁文件必须再多写一层"陈旧判断"（PID 存活？心跳超时？），而那层本身
就是新的 bug 来源。窗口壳被强杀后打不开，是最难排查的那种故障。
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol

from qingdan.core.context import ctx
from qingdan.core.registry import feature

#: CreateMutexW 成功后 GetLastError 仍为 183 表示"名字已存在"，即别的进程已经持有。
ERROR_ALREADY_EXISTS = 183
#: 窗口标题，二次启动时用来找回已有窗口。
WINDOW_TITLE = "清单"


class LockBackend(Protocol):
    """能不能拿到锁，交给可替换的后端 —— 与 autostart 的 RegistryBackend 同一个套路：
    core 层不直接碰 winreg/ctypes，测试才能在不真建内核对象的前提下验语义。"""

    supported: bool

    def acquire(self, name: str) -> Any:
        """拿到返回句柄，拿不到返回 None。"""

    def release(self, handle: Any) -> None: ...


class WinMutexBackend:
    supported = True

    def acquire(self, name: str) -> Any:
        import ctypes

        kern = ctypes.windll.kernel32
        handle = kern.CreateMutexW(None, False, name)
        if not handle:
            return None
        if kern.GetLastError() == ERROR_ALREADY_EXISTS:
            kern.CloseHandle(handle)      # 不是我的锁，句柄必须关掉，否则白占一次引用
            return None
        return handle

    def release(self, handle: Any) -> None:
        import ctypes

        kern = ctypes.windll.kernel32
        kern.ReleaseMutex(handle)
        kern.CloseHandle(handle)


class FakeBackend:
    """进程内假后端，供测试验互斥语义。"""

    supported = True

    def __init__(self) -> None:
        self.held: dict[str, int] = {}
        self.names: list[str] = []

    def acquire(self, name: str) -> Any:
        self.names.append(name)
        if name in self.held:
            return None
        self.held[name] = 1
        return f"handle:{name}"

    def release(self, handle: Any) -> None:
        name = str(handle).split(":", 1)[1]
        self.held.pop(name, None)


class NoopBackend:
    """非 Windows 开发环境：不阻塞启动，但状态里如实说"本后端不提供互斥"。"""

    supported = False

    def acquire(self, name: str) -> Any:
        return "noop"

    def release(self, handle: Any) -> None:
        pass


def mutex_name(data_dir: os.PathLike | str) -> str:
    """按**绝对路径**派生锁名。

    用完整路径而不是固定名字，是因为允许 `--data-dir` 指到别处（多份清单并存、
    测试专用目录）；固定名字会把两个互不相干的目录也判成"同一个实例"。
    """
    raw = str(Path(data_dir).expanduser().resolve()).lower()
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]
    # Local\ = 同一登录会话内互斥。Global\ 需要额外权限，桌面场景也不需要跨会话。
    return f"Local\\qingdan-{digest}"


def read_status(data_dir: os.PathLike | str) -> dict:
    """读第一个实例留下的 http.json，好告诉用户"已经在跑的那个"是谁。"""
    try:
        return json.loads((Path(data_dir) / "http.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


@dataclass
class Instance:
    backend: LockBackend = field(default_factory=NoopBackend)
    handle: Any = None
    name: str | None = None
    data_dir: str | None = None

    def acquire(self, data_dir: os.PathLike | str) -> dict:
        if self.handle is not None:
            return {"held": True, "reason_key": "already_acquired_by_us", "mutex": self.name}
        name = mutex_name(data_dir)
        handle = self.backend.acquire(name)
        self.data_dir = str(Path(data_dir).expanduser().resolve())
        if handle is None:
            self.name = name
            return {"held": False, "reason_key": "another_instance",
                    "mutex": name, "data_dir": self.data_dir,
                    "running": read_status(self.data_dir)}
        self.handle, self.name = handle, name
        return {"held": True, "reason_key": None, "mutex": name,
                "data_dir": self.data_dir, "pid": os.getpid()}

    def release(self) -> None:
        if self.handle is not None:
            self.backend.release(self.handle)
            self.handle, self.name = None, None

    @property
    def held(self) -> bool:
        return self.handle is not None

    def status(self) -> dict:
        """给接口层用（R4：加了功能就要有它的接口）。"""
        return {"held": self.held, "mutex": self.name,
                "data_dir": self.data_dir, "pid": os.getpid(),
                "backend": type(self.backend).__name__,
                "supported": self.backend.supported}


def default_backend() -> LockBackend:
    return WinMutexBackend() if os.name == "nt" and sys.platform == "win32" else NoopBackend()


_current = Instance(backend=default_backend())


def current() -> Instance:
    return _current


def set_manager(inst: Instance | None) -> None:
    """测试注入用：换掉全局实例，避免单测真的去建内核对象。"""
    global _current
    _current = inst if inst is not None else Instance(backend=default_backend())


def _popup(caption: str, text: str) -> None:
    """默认提示实现：MessageBoxW。阻塞调用，必须丢独立线程，否则卡住启动流程。"""
    import ctypes

    threading.Thread(target=ctypes.windll.user32.MessageBoxW,
                     args=(0, text, caption, 0x40), daemon=True).start()


#: 可替换的提示后端 —— 与 gitee.TRANSPORT 同一个理由：测试绝不能真的往用户桌面弹窗。
POPUP: Callable[[str, str], None] = _popup


def build_running_text(data_dir: str | None = None, port: int | None = None) -> str:
    lines = ["清单已经在运行了。"]
    if port:
        lines.append(f"正在运行的窗口服务在端口 {port}。")
    lines.append("请勿同时开两份：同一个数据目录被两个进程写，"
                 "提醒会重复推送，版本戳也会撞车。")
    if data_dir:
        lines.append(f"数据目录：{data_dir}")
    return "\n".join(lines)


def warn_already_running(data_dir: str | Path | None = None, port: int | None = None) -> bool:
    """第二个实例的兜底提示。

    这条路径**必须能被看见**：windowed exe 没有控制台，`print` 等于没说；
    用户双击第二次如果什么都没发生，只会认为"程序坏了"，然后去任务管理器里
    杀掉正在跑的那个实例 —— 结果数据反而被截断在半途。
    文案写死在 shell 层是故意的：这一刻 i18n 还没起来（窗口都没开），
    "展示文案归前端"那条规矩管不到原生启动框。
    """
    if os.name != "nt":
        return False
    try:
        POPUP("清单", build_running_text(str(data_dir) if data_dir else None, port))
        return True
    except Exception:
        return False


def foreground_existing(title: str = WINDOW_TITLE) -> bool:
    """把已经在跑的窗口提到前台。找不到或平台不支持都只返回 False，绝不抛异常 ——
    二次启动时用户至少要看到"已经有窗口了"，而不是一个 traceback。

    注意 `SetForegroundWindow` 可能被系统拒绝（Windows 限制非前台进程抢焦点），
    所以这里用"焦点窗口是不是它"来判定成功，而不是假定调用有效。
    """
    if os.name != "nt":
        return False
    try:
        import ctypes

        u = ctypes.windll.user32
        hwnd = u.FindWindowW(None, title)
        if not hwnd:
            return False
        SW_RESTORE, SW_SHOW = 9, 5
        u.ShowWindow(hwnd, SW_RESTORE)
        u.ShowWindow(hwnd, SW_SHOW)
        u.SetForegroundWindow(hwnd)
        return bool(u.GetForegroundWindow() == hwnd)
    except Exception:
        return False


# --------------------------------------------------------------------------- 接口（R4）


@feature(name="app.instance_status", group="app", http=("GET", "/api/v1/app/instance"),
         idempotent=True)
def app_instance_status() -> dict:
    """当前进程是不是那个"正主"。

    看起来像废话（服务能应答就说明进程活着），但它回答的是另一个问题：
    **这个数据目录有没有被第二个实例同时打开过**。多实例并存时用户看到的是
    "两个窗口里的东西不一样"，排查时第一句就该能查出锁在不在手上。
    """
    st = current().status()
    st["data_dir"] = st["data_dir"] or (ctx.data_dir and str(ctx.data_dir))
    return st
