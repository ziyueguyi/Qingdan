"""每日备份与恢复（M6）。

三个不那么显然的设计决定：

1. **用 `VACUUM INTO` 而不是复制文件**。直接 copy `db.sqlite` 会拿到一个可能正在被写的
   快照，WAL 模式下 `-wal` 文件里还有未落盘的事务，复制出来的库可能损坏。
   `VACUUM INTO` 是 SQLite 官方的在线备份路径，产出一个一致且紧凑的单文件。

2. **恢复是"重启时执行"，不是当场替换**。uvicorn 线程池、告警调度线程都持有连接，
   在运行中把底层文件换掉是未定义行为。所以 restore 只写一个待恢复标记，
   下次启动在建连接之前完成替换。宁可要求用户重启一次，也不做危险的热替换。

3. **恢复不会把远端也回滚**。备份里的 `base_*` 是随行数据，恢复后三方合并仍然按
   各自的版本戳裁决：如果某项在备份之后被别的设备删掉了，恢复回来后同步会再次删除它。
   这是正确行为（避免"复活已删项"），但和用户对"恢复"的直觉不同，必须在 UI 上讲清楚。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import time
from contextlib import closing
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from qingdan.core.context import ctx
from qingdan.core.registry import feature, not_found, validation_error
from qingdan.core.store import Store

BACKUP_DIR = "backups"
RESTORE_MARKER = "restore_pending.json"
# 只允许 `db-YYYY-MM-DD` 加一个字母/数字/连字符后缀，再 `.sqlite`。
# 锚定的字符集本身就是防线：`/`、`\`、`..` 都进不来，路径穿越无从下手。
# 后缀要放宽到允许字母（`-pre-restore`），否则 restore 自己生成的安全备份
# 过不了自己的校验（实测踩过）。
NAME_RE = re.compile(r"^db-\d{4}-\d{2}-\d{2}(-[0-9A-Za-z][0-9A-Za-z-]*)?\.sqlite$")
KEEP_DEFAULT = 14


def backups_dir() -> Path:
    return Path(ctx.data_dir) / BACKUP_DIR


def today_name(now: date | None = None) -> str:
    return f"db-{(now or date.today()).isoformat()}.sqlite"


# --------------------------------------------------------------------------- 创建


def create(name: str | None = None, force: bool = False) -> dict:
    """生成一份一致快照。

    不传 `force` 时同一天不重复创建 —— 每日首启靠这个幂等性。
    `force=True` 是用户手动点「立即备份」：必须真的产出一份新快照，
    落 `db-YYYY-MM-DD-HHMMSS.sqlite`。第一版这里直接复用日期名，
    于是当天第二次点击返回「已存在」并什么都不写 —— 按钮看着能点、实际是空操作，
    用户以为备好了，其实那份数据从没进过任何备份。
    """
    store = ctx.need_store()
    d = backups_dir()
    d.mkdir(parents=True, exist_ok=True)
    if name is None and force:
        name = f"db-{date.today().isoformat()}-{time.strftime('%H%M%S')}.sqlite"
    target = _free_path(d / (name or today_name()), force)
    if not NAME_RE.match(target.name):
        raise validation_error("备份名必须形如 db-YYYY-MM-DD.sqlite", got=target.name)
    if target.exists():
        return {"created": False, "path": str(target), "reason_key": "today_exists"}

    tmp = target.with_suffix(".tmp")
    # VACUUM INTO 要求目标不存在
    if tmp.exists():
        tmp.unlink()
    store.db.execute("VACUUM INTO ?", (str(tmp),))
    os.replace(tmp, target)
    size = target.stat().st_size
    return {"created": True, "path": str(target), "name": target.name, "size": size}


def _free_path(target: Path, force: bool) -> Path:
    """force 时给一个没用过的名字，绝不覆盖已有快照。

    覆盖等于「用户以为备了两份，实际只有一份」，而且很可能被 os.replace
    换成刚写坏的那一份 —— 备份目录里唯一不该丢的就是旧快照。
    """
    if not force or not target.exists():
        return target
    stem = target.name[: -len(".sqlite")]
    for n in range(2, 1000):
        cand = target.with_name(f"{stem}-{n}.sqlite")
        if not cand.exists():
            return cand
    raise validation_error("同一秒内备份次数过多", got=stem)


def daily_backup() -> dict:
    """首启调用：今天没备份就建一份，然后按保留数清理。"""
    out = create()
    pruned = prune()
    return {**out, "pruned": pruned}


def list_backups() -> list[dict]:
    d = backups_dir()
    if not d.is_dir():
        return []
    out = []
    for f in d.glob("db-*.sqlite"):
        if not NAME_RE.match(f.name):
            continue
        st = f.stat()
        out.append({"name": f.name, "size": st.st_size,
                    "mtime": int(st.st_mtime * 1000),
                    "valid": is_valid(f)})
    # 必须按时间排，不能按名字排：`db-2026-11-05-201130.sqlite` 的 `-` 排在 `.` 前面，
    # 名字倒序会把当天那份日期名备份当成"最新"，手动备份永远显示不出来，
    # prune 也会把最新一份当最旧的删掉。
    out.sort(key=lambda b: b["mtime"], reverse=True)
    return out


def prune(keep: int = KEEP_DEFAULT) -> list[str]:
    """滚动删除最旧的备份，只动本模块命名规范内的文件。

    不匹配 `db-YYYY-MM-DD*.sqlite` 的一律不碰 —— 防止目录里混进用户自己的文件后
    被当成备份删掉。
    """
    files = [b for b in list_backups() if b["valid"]]
    doomed = files[int(keep):] if keep > 0 else files
    removed = []
    for item in doomed:
        path = backups_dir() / item["name"]
        try:
            path.unlink()
            removed.append(item["name"])
        except OSError:
            continue
    return removed


def is_valid(path: Path) -> bool:
    """备份必须是能打开、且有我们表结构的库，不能只看文件存在。"""
    try:
        probe = sqlite3_connect_ro(path)
    except Exception:
        return False
    try:
        if probe.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
            return False
        rows = {r[0] for r in probe.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        if not {"item", "sync_state", "settings"} <= rows:
            return False
        required = {
            "item": {"id", "kind", "rev_ms", "rev_seq", "rev_dev", "data", "deleted"},
            "sync_state": {"k", "v"}, "settings": {"k", "v"},
        }
        return all(columns <= {r[1] for r in probe.execute(f"PRAGMA table_info({table})")}
                   for table, columns in required.items())
    except Exception:
        return False
    finally:
        probe.close()


def sqlite3_connect_ro(path: Path):
    import sqlite3

    return sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)


# --------------------------------------------------------------------------- 恢复（重启时执行）


def schedule_restore(name: str) -> dict:
    store = ctx.need_store()
    src = backups_dir() / name
    if not NAME_RE.match(name) or not src.is_file():
        raise not_found(f"备份 {name}")
    if not is_valid(src):
        raise validation_error("备份文件损坏或表结构不符，拒绝恢复", got=name)

    # 先给当前状态留一份，恢复错了还能退回去
    safety = create(name=f"db-{date.today().isoformat()}-pre-restore.sqlite", force=True)
    store.state_set("restore_scheduled", {"name": name, "at": int(time.time() * 1000)})
    (Path(ctx.data_dir) / RESTORE_MARKER).write_text(
        json.dumps({"name": name, "scheduled_at": int(time.time() * 1000)}, ensure_ascii=False),
        encoding="utf-8")
    return {"scheduled": True, "restart_required": True, "restore_from": name,
            "safety_backup": safety["path"]}
            # 恢复语义（只回滚本机、远端删除仍会重新生效）由前端 i18n 讲给用户，
            # 后端不塞中文进响应体。


def apply_pending_restore(data_dir: Path) -> dict | None:
    """在建 Store 之前调用：把待恢复的备份换成主库。

    顺序很重要 —— 必须赶在任何连接打开之前完成，否则就是热替换。
    """
    marker = data_dir / RESTORE_MARKER
    if not marker.is_file():
        return None
    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        marker.unlink(missing_ok=True)
        return None

    name = payload.get("name") if isinstance(payload, dict) else None
    if not isinstance(name, str) or not NAME_RE.fullmatch(name):
        marker.unlink(missing_ok=True)
        return {"applied": False, "reason_key": "pending_restore_unavailable"}
    src = data_dir / BACKUP_DIR / name
    if not is_valid(src):
        marker.unlink(missing_ok=True)
        return {"applied": False, "reason_key": "pending_restore_unavailable"}

    db = data_dir / "db.sqlite"
    # 完整复制并验证后再原子替换；复制失败时主库和 WAL 都保持原样。
    staged = data_dir / "db.sqlite.restore-tmp"
    try:
        shutil.copy2(src, staged)
        if not is_valid(staged):
            raise OSError("恢复副本校验失败")
        if db.exists():
            import sqlite3
            with closing(sqlite3.connect(db)) as current:
                with closing(sqlite3.connect(data_dir / "db.sqlite.before-restore")) as safety:
                    current.backup(safety)
                current.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        os.replace(staged, db)
    finally:
        staged.unlink(missing_ok=True)
    for suffix in ("-wal", "-shm"):
        target = Path(str(db) + suffix)
        if target.exists():
            target.unlink()
    marker.unlink(missing_ok=True)
    return {"applied": True, "restored_from": src.name}


# --------------------------------------------------------------------------- 接口（R4）


@feature(name="backup.create", group="backup", http=("POST", "/api/v1/backups"))
def backup_create(name: str | None = None, force: bool = False) -> dict:
    return create(name, force)


@feature(name="backup.list", group="backup", http=("GET", "/api/v1/backups"), idempotent=True)
def backup_list(keep: int = KEEP_DEFAULT) -> dict:
    items = list_backups()
    return {"total": len(items), "keep": keep, "items": items,
            "dir": str(backups_dir())}


@feature(name="backup.prune", group="backup", http=("POST", "/api/v1/backups/prune"))
def backup_prune(keep: int = KEEP_DEFAULT) -> dict:
    if not 1 <= int(keep) <= 365:
        raise validation_error("keep 需在 1..365", got=keep)
    return {"removed": prune(int(keep))}


@feature(name="backup.restore", group="backup",
         http=("POST", "/api/v1/backups/{name}/restore"))
def backup_restore(name: str) -> dict:
    return schedule_restore(name)


@feature(name="backup.status", group="backup", http=("GET", "/api/v1/backup/status"),
         idempotent=True)
def backup_status() -> dict:
    """备份健康度 —— 数据放了很久却没备份机制是个实际风险，所以要能一眼看出
    "最近一次备份是多久之前"。"""
    items = list_backups()
    newest = next((item for item in items if item["valid"]), None)
    now_ms = int(time.time() * 1000)
    return {"count": len(items), "newest": newest,
            "age_days": round((now_ms - newest["mtime"]) / 86_400_000, 1) if newest else None,
            "stale": (now_ms - newest["mtime"]) > 3 * 86_400_000 if newest else True,
            "pending_restore": (Path(ctx.data_dir) / RESTORE_MARKER).is_file()
                               if ctx.data_dir else False}
