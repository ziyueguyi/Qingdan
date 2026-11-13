"""桌面窗口壳（M6 的核心件）。

这就是「为什么界面是 HTML」的答案：
产品是一个 exe，`pywebview` 在原生窗口里嵌系统自带的 Edge WebView2 来渲染打包在
exe 内部的 HTML。**没有任何公网服务器**，服务只听 127.0.0.1，前端文件随 exe 一起分发，
窗口没有地址栏和标签页。用浏览器打开只是开发期的调试方式，不是产品形态。

启动顺序有讲究：先起 HTTP 服务并等它真的能响应，再开窗口 ——
否则窗口先加载会白屏（webview 不会自动重试）。

关窗口默认**隐藏到托盘**而不是退出：提醒调度器跟着进程活着，进程没了提醒就全哑了。
"""
from __future__ import annotations

import json
import socket
import threading
import time
from pathlib import Path


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _write_status(data_dir, payload: dict) -> None:
    """把端口/失败原因写进 http.json。windowed 模式没有控制台，这是唯一的现场证据。"""
    try:
        Path(data_dir).mkdir(parents=True, exist_ok=True)
        (Path(data_dir) / "http.json").write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def _wait_healthy(port: int, timeout: float = 15.0) -> bool:
    """轮询 /api/v1/health。uvicorn 起线程是异步的，不能假设立刻可用。"""
    import httpx

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if httpx.get(f"http://127.0.0.1:{port}/api/v1/health", timeout=1.0).status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(0.15)
    return False


def run(data_dir: str | Path | None = None, width: int = 1280, height: int = 820) -> int:
    import uvicorn
    import webview

    import qingdan.bootstrap  # noqa: F401  （注册全部 @feature 与告警渠道）
    from qingdan.adapters.toast import set_tray_notifier
    from qingdan.api.http import build_app
    from qingdan.core import alerting
    from qingdan.core import instance as inst
    from qingdan.core.alerting import config as alert_config
    from qingdan.core.alerting.scheduler import scheduler
    from qingdan.core.autostart import default_autostart
    from qingdan.core.context import ctx, init, resolve_data_dir
    from qingdan.shell.tray import Tray

    # 单实例锁必须赶在 init 之前拿：init 会开数据库连接、跑 daily_backup、
    # 还可能执行 apply_pending_restore（换主库文件）。两个进程一起做这些，
    # 轻则提醒推两遍，重则同一毫秒发出两个一模一样的版本戳（见 core/instance.py）。
    target, _src = resolve_data_dir(data_dir)   # 锁必须锁在真正会打开的那个目录上
    lock = inst.current()
    got = lock.acquire(target)
    if not got["held"]:
        port = (got.get("running") or {}).get("port")
        if not inst.foreground_existing():
            # 窗口找不到 / 抢焦点被系统拒绝：windowed exe 没有控制台，
            # 只 print 的话用户双击第二次看到的是"什么都没发生"。
            inst.warn_already_running(got.get("data_dir"), port)
        print(f"[shell] 已经有一个清单在运行（端口 {port}），本次不启动第二个实例。"
              f"同一份数据被两个进程写会重复提醒、版本戳也会撞车。", flush=True)
        return 0

    init(data_dir)

    # 每日首启自动备份（同一天重复启动不会叠备份）。放在建连之后、其它一切之前，
    # 这样哪怕后面某个环节崩了，今天的快照也已经存在。
    from qingdan.core.backup import daily_backup
    try:
        snap = daily_backup()
        if snap.get("created"):
            print(f"[shell] 已生成今日备份 {snap['name']}", flush=True)
    except Exception as e:                       # 备份失败不该阻止程序启动
        print(f"[shell] 自动备份失败：{e}", flush=True)

    port = _free_port()

    config = uvicorn.Config(build_app(), host="127.0.0.1", port=port,
                            log_level="warning", access_log=False, proxy_headers=False)
    server = uvicorn.Server(config)
    threading.Thread(target=server.run, daemon=True, name="qingdan-api").start()

    if not _wait_healthy(port):
        _write_status(ctx.data_dir, {"ok": False, "error": "本地服务未在 15 秒内响应"})
        print("[shell] 本地服务没能起来，窗口不会打开", flush=True)
        return 1

    _write_status(ctx.data_dir, {"ok": True, "port": port})
    from qingdan.api.remote import install_remote
    remote = install_remote()

    # 自启项里的路径可能因为 exe 被移动而过期 → 开机静默失败，启动时自查纠正
    repaired = default_autostart().repair_if_stale()
    if repaired:
        print(f"[shell] 已纠正过期的开机自启路径：{repaired}", flush=True)

    scheduler.start()          # 提醒调度跟着进程活着
    from qingdan.core.autosync import loop as auto_sync
    auto_sync.start()

    from qingdan.shell.notifications import NotificationHost
    notification_host=NotificationHost()
    window = webview.create_window(
        "清单", f"http://127.0.0.1:{port}/",
        width=width, height=height, min_size=(900, 600), text_select=True,js_api=notification_host)
    notification_host._window=window

    quitting = {"v": False}
    tray = Tray(
        on_show=lambda: (window.show(), window.restore()),
        on_quit=lambda: quitting.update(v=True) or _shutdown(scheduler, server, window),
        on_toggle_alerts=lambda: _toggle_alerts(),
        is_visible=lambda: True,
        alerts_enabled=lambda: bool(alert_config.get_config()["master"]),
        on_notification=notification_host._open,
    )

    def on_closing():
        """点 X → 隐藏到托盘。只有托盘菜单里的"退出"才真的结束进程。"""
        if quitting["v"]:
            return True                      # 放行真正的关闭
        try:
            window.hide()
        except Exception:
            pass
        return False                         # 取消关闭

    window.events.closing += on_closing

    def on_ready():
        if tray.start():
            set_tray_notifier(tray.notify)
            print("[shell] 托盘已就绪，通知后端 = 托盘气球", flush=True)
        else:
            print("[shell] 托盘不可用，通知退回 dialog 后端", flush=True)

    try:
        webview.start(on_ready, gui="edgechromium", debug=False)
    finally:
        remote.close()
        auto_sync.stop()
        set_tray_notifier(None)
        tray.stop()
        scheduler.stop()
        server.should_exit = True
        if ctx.store is not None:
            ctx.store.close()
        lock.release()          # 正常退出要还锁；崩溃时由内核自己回收
    return 0


def _toggle_alerts() -> None:
    """托盘菜单里的提醒开关。走接口层，保证和 UI 上点开关是同一条路径。"""
    from qingdan.core.registry import call
    try:
        cfg = call("alert.config_get")
        call("alert.config_update", config={"master": not cfg["master"]})
    except Exception as e:                       # 托盘回调里抛异常会崩掉托盘线程
        print(f"[shell] 切换提醒失败：{e}", flush=True)


def _shutdown(scheduler, server, window):
    scheduler.stop()
    server.should_exit = True
    try:
        window.destroy()
    except Exception:
        pass
