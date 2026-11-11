"""入口：`python -m qingdan serve` 起本地服务，其它参数转给 CLI。"""
from __future__ import annotations

import json
import secrets
import socket
import sys
from pathlib import Path

from qingdan.core.context import ctx, init, resolve_data_dir


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def serve(data_dir: str | None = None, host: str = "127.0.0.1") -> int:
    """只监听 127.0.0.1；端口与一次性 token 写进 http.json 供窗口壳读取（§9.2）。"""
    import uvicorn
    import qingdan.bootstrap  # noqa: F401
    if host not in {'127.0.0.1','localhost','::1'}:
        raise ValueError('桌面/CLI 通道只能绑定本机地址，请在访问设置中启用远程 API')

    from qingdan.api.http import build_app
    from qingdan.core import instance as inst
    from qingdan.core.alerting.scheduler import scheduler

    # 与窗口壳同一把锁：危险来自"两个进程同时开同一个数据目录"，不区分谁起的。
    # 锁名按绝对路径派生，所以 --data-dir 指到别处（开发副本、测试目录）不受影响。
    lock = inst.current()
    got = lock.acquire(resolve_data_dir(data_dir)[0])
    if not got["held"]:
        print(f"[serve] 已有进程持有 {got['data_dir']} 的锁"
              f"（端口 {(got.get('running') or {}).get('port')}），拒绝二次启动", flush=True)
        return 2

    init(data_dir)

    from qingdan.core.backup import daily_backup
    try:
        daily_backup()
    except Exception as e:
        print(f"[serve] 自动备份失败：{e}", flush=True)

    port = _free_port()
    token = secrets.token_urlsafe(24)
    assert ctx.data_dir is not None
    (Path(ctx.data_dir) / "http.json").write_text(
        json.dumps({"port": port, "token": token}, ensure_ascii=False), encoding="utf-8")

    # 提醒只在进程存活时生效；服务端兜底见 §12.5（M7）。
    # start() 内部会先把过期作业标 skipped，避免开机瞬间涌出历史提醒。
    scheduler.start()
    from qingdan.core.autosync import loop as auto_sync
    auto_sync.start()
    from qingdan.api.remote import install_remote
    remote = install_remote()

    print(f"qingdan api listening on http://{host}:{port}", flush=True)
    try:
        # windowed EXE 没有 stdout/stderr；默认日志格式器会调用 None.isatty()。
        uvicorn.run(build_app(), host=host, port=port, log_level="warning", log_config=None, proxy_headers=False)
    finally:
        remote.close()
        auto_sync.stop()
        scheduler.stop()
        lock.release()
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] in ("serve", "app"):
        rest = [a for a in argv[1:]]
        data_dir = rest[0] if rest else None
        if argv[0] == "app":
            from qingdan.shell.app import run as run_app
            return run_app(data_dir)
        return serve(data_dir)
    from qingdan.cli import main as cli_main
    return cli_main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
