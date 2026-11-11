"""把老位置（`%LOCALAPPDATA%\\qingdan`）的数据搬到应用目录 `<应用目录>\\data`。

为什么要有这个脚本而不是"自动搬家"：搬数据是不可逆的用户资产变更，程序自己悄悄搬，
一旦搬错（目标已存在、磁盘满、只读目录）就没有回头路。这里坚持三条：

1. **先复制、校验通过、再让你决定删不删** —— 脚本自己绝不删源目录；
2. 目标目录已经有库就直接拒绝，不做任何覆盖；
3. 复制前要求应用**没在运行**（单实例锁是硬证据），否则复制到的可能是一半新一半旧的状态。

用法：

    python scripts/migrate_data.py            # 检查并给出会做什么
    python scripts/migrate_data.py --apply    # 真正复制
"""
from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from qingdan.core import context                      # noqa: E402
from qingdan.core.instance import (Instance, WinMutexBackend,  # noqa: E402
                                   mutex_name, read_status)

SUBDIRS = ("backups", "exports", "repo")


def counts(db: Path) -> dict:
    """行数只用来证明"搬完了内容一样"，不做业务判断。"""
    con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    try:
        return {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                for t in ("item", "settings", "sync_state", "conflict")}
    finally:
        con.close()


def integrity(db: Path) -> str:
    con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    try:
        return str(con.execute("PRAGMA integrity_check").fetchone()[0])
    finally:
        con.close()


def app_is_running(data_dir: Path) -> int | None:
    """应用目录上有锁就说明程序在跑，这时候不能搬。"""
    probe = Instance(backend=WinMutexBackend())
    got = probe.acquire(data_dir)
    if got["held"]:
        probe.release()
        return None                       # 没人在跑
    return (read_status(got["data_dir"]) or {}).get("port", -1)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="真的复制（默认只预演）")
    ns = ap.parse_args()

    src = context.LEGACY_DIR
    dst = context.default_dir()
    src_db, dst_db = src / "db.sqlite", dst / "db.sqlite"

    print(f"源：{src}  {'（有库）' if src_db.is_file() else '（没有库）'}")
    print(f"目标：{dst}  {'（已有库 → 拒绝执行）' if dst_db.is_file() else '（空）'}")
    if not src_db.is_file():
        print("没有可搬的数据，退出。")
        return 1
    if dst_db.is_file():
        print("目标已经有一份库了。要合并请先手工改名，脚本不猜你想要哪一份。")
        return 1

    port = app_is_running(src)
    if port is not None:
        print(f"应用还在运行（端口 {port}），请先从托盘退出再搬。")
        return 1

    if not ns.apply:
        print("预演模式：将复制 db.sqlite(+wal/shm) 与 " + ", ".join(SUBDIRS))
        print("确认无误后加 --apply。源目录不会被删除。")
        return 0

    dst.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src_db, dst_db)
    for suffix in ("-wal", "-shm"):
        p = Path(str(src_db) + suffix)
        if p.is_file():
            shutil.copy2(p, Path(str(dst_db) + suffix))
    for sub in SUBDIRS:
        s = src / sub
        if s.is_dir():
            shutil.copytree(s, dst / sub, dirs_exist_ok=True)

    # 复制完必须验，不然"搬好了"只是句空话：integrity + 行数逐项对齐
    before, after = counts(src_db), counts(dst_db)
    ok = integrity(dst_db) == "ok" and before == after
    print(f"校验：integrity={integrity(dst_db)} 行数 源={before} 目标={after}")
    if not ok:
        print("校验没过：目标库已保留但**不要**用它启动，请先排查（源目录未动）。")
        return 1

    print(f"完成。以后默认读 {dst}；确认用着没问题后，可以手工把旧目录放进回收站：")
    print(f"    {src}")
    print("（脚本不删源数据 —— 删自己确认过的东西才安全。）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
