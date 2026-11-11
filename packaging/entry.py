"""PyInstaller 入口。

单独一个文件而不是直接 `-m qingdan`，是因为冻结后 `sys.argv[0]` 是 exe 自身，
`runpy` 的模块查找路径也不同；显式调用最稳。

崩溃必须落盘：windowed 模式没有控制台，异常只会变成一个 PyInstaller 弹窗
或者干脆什么都不发生。实测过一次因 excludes 排错导致的启动崩溃，
没有这个日志就只能靠猜。
"""
import os
import sys
import traceback
from pathlib import Path


def _crash_dir() -> Path:
    """崩溃日志跟着应用目录走（用户要求：程序产生的东西不要散落在别处）。

    这里**不能** import qingdan.core.context 来复用同一套解析：崩溃往往就发生在
    导入或初始化阶段，兜底路径自己再依赖被测对象，就变成"崩了却写不下日志"。
    所以只依赖 sys.executable —— 冻结后它就是 exe 本身。
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]        # 源码运行：工程根 qingdan/


def _write_crash() -> None:
    for target in (_crash_dir(), Path(os.environ.get("LOCALAPPDATA", Path.home())) / "qingdan"):
        try:
            target.mkdir(parents=True, exist_ok=True)
            (target / "crash.log").write_text(traceback.format_exc(), encoding="utf-8")
            return
        except OSError:
            continue        # 应用目录只读（Program Files）才退到用户目录，且只退一步


def main() -> int:
    try:
        from qingdan.__main__ import main as real_main
        argv = sys.argv[1:] or ["app"]
        return real_main(argv)
    except SystemExit:
        raise
    except BaseException:                      # noqa: BLE001 —— 入口兜底，必须全接
        _write_crash()
        raise


if __name__ == "__main__":
    # 双击 exe 时没有参数 → 开桌面窗口。带参数时透传，方便 `Qingdan.exe task list` 这类调用。
    raise SystemExit(main())
