"""SQLite 存储层。

关键设计：每条 item 除了「当前版本」还存一组 `base_*`（上次同步快照）。
没有 base，「本地没有 + 远端没有」就是二义的，无法判断谁删的 —— 这是 R3b 的技术前提。

比较只需要 (rev, deleted)，所以 base 只存这两项，不存正文，省一半空间。
"""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable

from qingdan.core.clock import Clock, Rev
from qingdan.core.models import Item, new_id

SCHEMA = """
CREATE TABLE IF NOT EXISTS item (
  id            TEXT PRIMARY KEY,
  kind          TEXT NOT NULL,
  rev_ms        INTEGER NOT NULL,
  rev_seq       INTEGER NOT NULL,
  rev_dev       TEXT NOT NULL,
  deleted       INTEGER NOT NULL DEFAULT 0,
  deleted_at_ms INTEGER,
  data          TEXT NOT NULL,
  meta          TEXT NOT NULL DEFAULT '{}',
  schema        INTEGER NOT NULL DEFAULT 1,
  base_ms       INTEGER,
  base_seq      INTEGER,
  base_dev      TEXT,
  base_deleted  INTEGER,
  dirty         INTEGER NOT NULL DEFAULT 1,
  updated_at    INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_item_kind    ON item(kind, deleted);
CREATE INDEX IF NOT EXISTS idx_item_dirty   ON item(dirty) WHERE dirty = 1;
CREATE INDEX IF NOT EXISTS idx_item_rev     ON item(rev_ms, rev_seq, rev_dev);
CREATE INDEX IF NOT EXISTS idx_item_catalog ON item(kind, deleted,
  CAST(COALESCE(json_extract(data,'$.order'),0) AS INTEGER), json_extract(data,'$.name'), id);

-- 可重建的标签索引；分页时不再逐次展开所有任务的 JSON。
CREATE TABLE IF NOT EXISTS task_tag_ref (
  task_id TEXT NOT NULL, name TEXT NOT NULL, PRIMARY KEY(task_id,name)
);
CREATE INDEX IF NOT EXISTS idx_task_tag_name ON task_tag_ref(name,task_id);
CREATE TRIGGER IF NOT EXISTS item_tag_insert AFTER INSERT ON item BEGIN
  INSERT OR IGNORE INTO task_tag_ref(task_id,name)
    SELECT NEW.id, CAST(value AS TEXT) FROM json_each(NEW.data,'$.tags')
    WHERE NEW.kind='task' AND NEW.deleted=0 AND type='text' AND trim(CAST(value AS TEXT))<>'';
END;
CREATE TRIGGER IF NOT EXISTS item_tag_update AFTER UPDATE OF data,deleted,kind ON item BEGIN
  DELETE FROM task_tag_ref WHERE task_id=OLD.id;
  INSERT OR IGNORE INTO task_tag_ref(task_id,name)
    SELECT NEW.id, CAST(value AS TEXT) FROM json_each(NEW.data,'$.tags')
    WHERE NEW.kind='task' AND NEW.deleted=0 AND type='text' AND trim(CAST(value AS TEXT))<>'';
END;
CREATE TRIGGER IF NOT EXISTS item_tag_delete AFTER DELETE ON item BEGIN
  DELETE FROM task_tag_ref WHERE task_id=OLD.id;
END;

CREATE TABLE IF NOT EXISTS sync_state (k TEXT PRIMARY KEY, v TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS settings     (k TEXT PRIMARY KEY, v TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS conflict (
  id          TEXT PRIMARY KEY,
  item_id     TEXT NOT NULL,
  local_json  TEXT,
  remote_json TEXT,
  base_json   TEXT,
  reason      TEXT NOT NULL,
  created_at  INTEGER NOT NULL,
  resolved_by TEXT
);

-- 撤销：每次可撤销操作存一份「前像」。注意 undo 不是回滚历史，
-- 而是**用前像再写一个新版本** —— 否则墓碑/LWW 语义会被破坏。
CREATE TABLE IF NOT EXISTS ops (
  id        INTEGER PRIMARY KEY AUTOINCREMENT,
  feature   TEXT NOT NULL,
  created_at INTEGER NOT NULL,
  undone    INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS op_items (
  op_id     INTEGER NOT NULL,
  item_id   TEXT NOT NULL,
  kind      TEXT NOT NULL,          -- created | updated | deleted
  before_json TEXT,                 -- NULL 表示操作前该 id 不存在
  FOREIGN KEY(op_id) REFERENCES ops(id) ON DELETE CASCADE
);

-- 视图偏好：设备本地，**永不进同步**（§11.5）
CREATE TABLE IF NOT EXISTS view_pref (key TEXT PRIMARY KEY, value TEXT NOT NULL);

-- 重复任务的「已完成/已跳过的那些次」：同样是设备本地、**永不进同步**。
-- 真值只有 Item 上的 due_at（= 当前这一次）。历史之所以不同步：两台机器各自补做
-- 同一次会互相覆盖，而它只是日志、不参与任何裁决；同步它等于把 LWW 用在流水账上。
CREATE TABLE IF NOT EXISTS occurrence (
  item_id TEXT NOT NULL,
  due_at  TEXT NOT NULL,                 -- 被完成/跳过的那一次的到期（推进前的原值）
  at      TEXT NOT NULL,                 -- 实际操作时刻
  kind    TEXT NOT NULL DEFAULT 'done',  -- done | skipped
  PRIMARY KEY (item_id, due_at)
);
CREATE INDEX IF NOT EXISTS idx_occurrence_at ON occurrence(item_id, at);

-- 告警（M3）。作业是**派生数据**：由任务的 remind_at × 启用渠道重算，
-- 删了可以重建，所以它不是业务真值，也绝不能把 item 标成 dirty。
CREATE TABLE IF NOT EXISTS alert_job (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  item_id    TEXT NOT NULL,
  fire_at    INTEGER NOT NULL,          -- 绝对 epoch 毫秒
  channel    TEXT NOT NULL,             -- toast | dingtalk | email | digest
  status     TEXT NOT NULL DEFAULT 'pending',  -- pending|sent|failed|skipped|snoozed
  attempts   INTEGER NOT NULL DEFAULT 0,
  next_retry INTEGER,
  sent_at    INTEGER,
  error      TEXT,
  dedup_key  TEXT NOT NULL UNIQUE
);
CREATE INDEX IF NOT EXISTS idx_alert_due ON alert_job(status, fire_at);

CREATE TABLE IF NOT EXISTS alert_log (
  ts       INTEGER NOT NULL,
  item_id  TEXT,
  channel  TEXT NOT NULL,
  level    TEXT NOT NULL,               -- info | warn | error
  detail   TEXT NOT NULL
);

-- Device-local notification inbox. No row is serialized into sync Items.
CREATE TABLE IF NOT EXISTS notification_item (
  item_id TEXT PRIMARY KEY, kind TEXT NOT NULL, revision TEXT NOT NULL,
  fire_at INTEGER NOT NULL, received_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_notification_received ON notification_item(received_at DESC,item_id);
CREATE TABLE IF NOT EXISTS notification_revision(id INTEGER PRIMARY KEY CHECK(id=1), value INTEGER NOT NULL);
INSERT OR IGNORE INTO notification_revision(id,value) VALUES(1,0);
CREATE TRIGGER IF NOT EXISTS notification_insert AFTER INSERT ON notification_item BEGIN
  UPDATE notification_revision SET value=value+1 WHERE id=1; END;
CREATE TRIGGER IF NOT EXISTS notification_update AFTER UPDATE ON notification_item BEGIN
  UPDATE notification_revision SET value=value+1 WHERE id=1; END;
CREATE TRIGGER IF NOT EXISTS notification_delete AFTER DELETE ON notification_item BEGIN
  UPDATE notification_revision SET value=value+1 WHERE id=1; END;

-- 密钥：DPAPI 密文，绑定当前 Windows 用户 + 本机（换机需重填）
CREATE TABLE IF NOT EXISTS secret (k TEXT PRIMARY KEY, v TEXT NOT NULL);
"""


# Native SQLite triggers also observe writes made by older clients/processes.
# Keep item changes separate from preferences/sync progress: metadata refreshes
# must not invalidate task pages when their underlying items did not change.
REVISION_SCHEMA = """
CREATE TABLE IF NOT EXISTS change_revision (
  id INTEGER PRIMARY KEY CHECK(id=1), items INTEGER NOT NULL, metadata INTEGER NOT NULL
);
INSERT OR IGNORE INTO change_revision(id,items,metadata) VALUES(1,0,0);
""" + "".join(
    f"CREATE TRIGGER IF NOT EXISTS qd_revision_{table}_{operation.lower()} "
    f"AFTER {operation} ON {table} BEGIN "
    f"UPDATE change_revision SET {column}={column}+1 WHERE id=1; END;\n"
    for table, column in (("item", "items"), ("settings", "metadata"),
                          ("view_pref", "metadata"), ("sync_state", "metadata"))
    for operation in ("INSERT", "UPDATE", "DELETE")
)


class _Connection(sqlite3.Connection):
    """Keep existing commit behavior, except inside an explicit atomic scope."""
    _deferred_commits=0

    def commit(self):
        if not self._deferred_commits:
            return super().commit()


class Store:
    def __init__(self, path: str | Path, device_id: str | None = None):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._lock = threading.Lock()
        self.operation_lock = threading.RLock()
        self.sync_lock = threading.RLock()  # Git worktree operations, independent of task editing.
        self.autosync_lock = threading.Lock()
        self.db.executescript(SCHEMA + REVISION_SCHEMA)
        if not self._setting("catalog_tag_index_v1"):
            self.db.execute(
                "INSERT OR IGNORE INTO task_tag_ref(task_id,name) "
                "SELECT task.id,CAST(j.value AS TEXT) FROM item task,json_each(task.data,'$.tags') j "
                "WHERE task.kind='task' AND task.deleted=0 AND j.type='text' AND trim(CAST(j.value AS TEXT))<>''")
            self._setting_set("catalog_tag_index_v1", "1")

        self.device_id = device_id or self._setting("device_id") or new_id()[:8]
        self._setting_set("device_id", self.device_id)
        self.clock = Clock.restore(self._json_setting("clock"), self.device_id)

    @property
    def db(self) -> sqlite3.Connection:
        """线程本地连接。

        SQLite 连接不能跨线程使用 —— uvicorn 的请求跑在线程池里，TestClient 也是，
        直接共享一个连接会抛 ProgrammingError。用 `check_same_thread=False` 糊过去
        只是把崩溃换成数据竞争，所以这里做真正的每线程一连接。
        """
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(str(self.path), timeout=10,factory=_Connection)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            self._local.conn = conn
        return conn

    @contextmanager
    def atomic_write(self):
        """Hold SQLite's writer reservation across service-level commits.

        operation_lock serializes one process; BEGIN IMMEDIATE also protects
        against another local CLI process. A caller's transaction is preserved.
        """
        db=self.db
        depth=db._deferred_commits
        owned=not db.in_transaction
        savepoint=f'qd_atomic_{depth}'
        db.execute('BEGIN IMMEDIATE' if owned else f'SAVEPOINT {savepoint}')
        db._deferred_commits=depth+1
        try:
            yield
        except BaseException:
            db._deferred_commits=depth
            if owned:sqlite3.Connection.rollback(db)
            else:
                db.execute(f'ROLLBACK TO {savepoint}')
                db.execute(f'RELEASE {savepoint}')
            raise
        else:
            db._deferred_commits=depth
            if owned:
                try:sqlite3.Connection.commit(db)
                except BaseException:
                    sqlite3.Connection.rollback(db)
                    raise
            else:db.execute(f'RELEASE {savepoint}')

    # ------------------------------------------------------------------ 时钟
    def next_rev(self) -> Rev:
        """发号必须串行 —— 两个线程同时 tick 会产出相同版本，LWW 就失效了。"""
        with self._lock:
            rev = self.clock.tick()
            self._json_setting_set("clock", self.clock.state())
        return rev

    def observe(self, rev: Rev) -> None:
        with self._lock:
            if rev.dev == self.device_id:
                return
            before = (self.clock.last_ms, self.clock.last_seq)
            self.clock.observe(rev)
            if (self.clock.last_ms, self.clock.last_seq) != before:
                self._json_setting_set("clock", self.clock.state())

    # ------------------------------------------------------------------ 读写
    def put(self, item: Item, dirty: bool = True) -> Item:
        self.db.execute(
            """INSERT INTO item (id, kind, rev_ms, rev_seq, rev_dev, deleted, deleted_at_ms,
                                 data, meta, schema, dirty, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET
                 kind=excluded.kind, rev_ms=excluded.rev_ms, rev_seq=excluded.rev_seq,
                 rev_dev=excluded.rev_dev, deleted=excluded.deleted,
                 deleted_at_ms=excluded.deleted_at_ms, data=excluded.data,
                 meta=excluded.meta, schema=excluded.schema, dirty=excluded.dirty,
                 updated_at=excluded.updated_at""",
            (item.id, item.kind, item.rev.ms, item.rev.seq, item.rev.dev,
             int(item.deleted), item.deleted_at_ms,
             json.dumps(item.data, ensure_ascii=False, sort_keys=True),
             json.dumps(item.meta, ensure_ascii=False, sort_keys=True),
             item.schema, int(dirty), item.rev.ms),
        )
        self.db.commit()
        return item

    def get(self, item_id: str) -> Item | None:
        row = self.db.execute("SELECT * FROM item WHERE id = ?", (item_id,)).fetchone()
        return self._row_to_item(row) if row else None

    def base_of(self, item_id: str) -> Item | None:
        """返回上次同步快照。只还原比较所需的 (rev, deleted)。"""
        row = self.db.execute("SELECT * FROM item WHERE id = ?", (item_id,)).fetchone()
        if row is None or row["base_ms"] is None:
            return None
        return Item(kind=row["kind"], id=row["id"],
                    rev=Rev(row["base_ms"], row["base_seq"], row["base_dev"] or ""),
                    deleted=bool(row["base_deleted"]))

    def list_items(
        self,
        kind: str | None = None,
        include_deleted: bool = False,
        dirty_only: bool = False,
    ) -> list[Item]:
        sql, args = ["SELECT * FROM item"], []
        filters = []
        if kind:
            filters.append("kind = ?")
            args.append(kind)
        if not include_deleted:
            filters.append("deleted = 0")
        if dirty_only:
            filters.append("dirty = 1")
        if filters:
            sql.append("WHERE " + " AND ".join(filters))
        sql.append("ORDER BY kind, id")
        rows = self.db.execute(" ".join(sql), args).fetchall()
        return [self._row_to_item(r) for r in rows]

    def snapshot(self) -> dict[str, Item]:
        """含墓碑的全量快照，供同步引擎当 base 用。"""
        return {it.id: it for it in self.list_items(include_deleted=True)}

    def health_counts(self) -> dict:
        row = self.db.execute(
            "SELECT COUNT(*) AS items, COALESCE(SUM(kind='task' AND deleted=0),0) AS tasks, "
            "COALESCE(SUM(kind='task' AND deleted=1),0) AS trash_count, "
            "COALESCE(SUM(dirty=1),0) AS dirty FROM item").fetchone()
        return dict(row)

    def change_token(self) -> list[int]:
        row = self.db.execute("SELECT items,metadata FROM change_revision WHERE id=1").fetchone()
        return [int(row[0]), int(row[1])]

    def task_change_token(self) -> int:
        return int(self.db.execute("SELECT items FROM change_revision WHERE id=1").fetchone()[0])

    @contextmanager
    def read_snapshot(self):
        db = self.db
        owns_snapshot = not db.in_transaction
        if owns_snapshot:
            db.execute('BEGIN')
        try:
            yield db
        finally:
            if owns_snapshot:
                db.rollback()

    def list_names(self, ids: Iterable[str]) -> dict[str, str]:
        ids = list(dict.fromkeys(x for x in ids if x))
        result = {}
        for start in range(0, len(ids), 100):
            batch = ids[start:start + 100]
            rows = self.db.execute(
                "SELECT id,json_extract(data,'$.name') AS name FROM item "
                "WHERE kind='list' AND deleted=0 AND id IN (" + ",".join("?" for _ in batch) + ")", batch)
            result.update({row["id"]: row["name"] or "" for row in rows})
        return result

    def catalog_page(self, kind: str, limit: int | None = None, offset: int = 0,
                     q: str = "", include_deleted: bool = False) -> dict:
        """SQL 分页；任务内标签也属于目录，旧数据、导入和同步无需回填写入。"""
        if kind not in {"list", "tag", "filter"}:
            raise ValueError("Unsupported catalog")
        if offset < 0 or (limit is not None and not 1 <= limit <= 100):
            raise ValueError("limit 需在 1..100，offset 不能小于 0")
        task_tags = "SELECT task_id AS id,name FROM task_tag_ref"
        if kind == "tag":
            cte = f"""WITH task_tags AS ({task_tags}),
                counts AS (SELECT name, COUNT(*) AS n FROM task_tags GROUP BY name),
                catalog AS (
                  SELECT id, data, deleted FROM item WHERE kind='tag' AND (deleted=0 OR ?)
                  UNION ALL
                  SELECT 'tag-name-' || hex(CAST(counts.name AS BLOB)),
                         json_object('name', counts.name, 'order', 0, 'color', NULL), 0
                  FROM counts WHERE NOT EXISTS (
                    SELECT 1 FROM item WHERE kind='tag' AND deleted=0
                    AND json_extract(data,'$.name')=counts.name))"""
        else:
            cte = f"WITH catalog AS (SELECT id,data,deleted FROM item WHERE kind='{kind}' AND (deleted=0 OR ?))"
        where = " WHERE instr(lower(json_extract(data,'$.name')),lower(?))>0"
        args = [int(include_deleted), q]
        total = self.db.execute(cte + " SELECT COUNT(*) FROM catalog" + where, args).fetchone()[0]
        if kind == "tag":
            sql = cte + " SELECT catalog.*, COALESCE(counts.n,0) AS task_count FROM catalog LEFT JOIN counts ON counts.name=json_extract(catalog.data,'$.name')" + where
        else:
            sql = cte + " SELECT * FROM catalog" + where
        sql += " ORDER BY CAST(COALESCE(json_extract(data,'$.order'),0) AS INTEGER), json_extract(data,'$.name'), id"
        sql += " LIMIT ? OFFSET ?"
        rows = self.db.execute(sql, [*args, limit if limit is not None else -1, offset]).fetchall()
        items = []
        for row in rows:
            item = self.get(row["id"])
            if item is None:
                item = Item(kind="tag", id=row["id"], rev=Rev(0, 0, "derived"),
                            data=json.loads(row["data"]))
            out = item.to_dict()
            if kind == "tag":
                out["task_count"] = row["task_count"]
            items.append(out)
        return {"total": total, "items": items, "offset": offset, "limit": limit,
                "has_more": offset + len(items) < total,
                "next_offset": offset + len(items) if offset + len(items) < total else None}

    def task_tag_exists(self, name: str) -> bool:
        return self.db.execute(
            "SELECT 1 FROM task_tag_ref WHERE name=? LIMIT 1", (name,)
        ).fetchone() is not None

    def tag_name_exists(self, name: str, exclude_id: str = "") -> bool:
        return self.task_tag_exists(name) or self.db.execute(
            "SELECT 1 FROM item WHERE kind='tag' AND deleted=0 AND id<>? "
            "AND json_extract(data,'$.name')=? LIMIT 1", (exclude_id, name)
        ).fetchone() is not None

    # ------------------------------------------------------------------ 同步辅助
    def set_base(self, item_id: str, rev: Rev, deleted: bool) -> None:
        self.db.execute(
            "UPDATE item SET base_ms=?, base_seq=?, base_dev=?, base_deleted=?, dirty=0 WHERE id=?",
            (rev.ms, rev.seq, rev.dev, int(deleted), item_id))
        self.db.commit()

    def set_base_many(self, rows: Iterable[tuple[str, Rev, bool]]) -> int:
        """一轮推送成功后批量落 base。合并成一次事务，避免 N 次 commit。"""
        batch = [(rev.ms, rev.seq, rev.dev, int(deleted),
                  rev.ms, rev.seq, rev.dev, int(deleted), item_id)
                 for item_id, rev, deleted in rows]
        if not batch:
            return 0
        self.db.executemany(
            "UPDATE item SET base_ms=?, base_seq=?, base_dev=?, base_deleted=?, "
            "dirty=CASE WHEN rev_ms=? AND rev_seq=? AND rev_dev=? AND deleted=? THEN 0 ELSE 1 END WHERE id=?",
            batch)
        self.db.commit()
        return len(batch)

    def base_map(self) -> dict[str, Item]:
        """全部有基线的 item，供同步引擎做三方裁决。"""
        out: dict[str, Item] = {}
        for row in self.db.execute(
                "SELECT id, kind, base_ms, base_seq, base_dev, base_deleted FROM item "
                "WHERE base_ms IS NOT NULL").fetchall():
            out[row["id"]] = Item(kind=row["kind"], id=row["id"],
                                  rev=Rev(row["base_ms"], row["base_seq"], row["base_dev"] or ""),
                                  deleted=bool(row["base_deleted"]))
        return out

    def mark_clean(self, item_ids: Iterable[str] | None = None) -> int:
        if item_ids is None:
            cur = self.db.execute("UPDATE item SET dirty = 0 WHERE dirty = 1")
        else:
            ids = list(item_ids)
            if not ids:
                return 0
            cur = self.db.execute(
                f"UPDATE item SET dirty = 0 WHERE id IN ({','.join('?' * len(ids))})", ids)
        self.db.commit()
        return cur.rowcount

    def dirty_count(self) -> int:
        return self.db.execute("SELECT COUNT(*) c FROM item WHERE dirty = 1").fetchone()["c"]

    def record_conflict(self, item_id: str, reason: str,
                        local: Item | None = None, remote: Item | None = None,
                        base: Item | None = None) -> str:
        cid = new_id()
        dump = lambda it: json.dumps(it.payload(), ensure_ascii=False, sort_keys=True) if it else None
        self.db.execute(
            "INSERT INTO conflict (id, item_id, local_json, remote_json, base_json, reason, created_at)"
            " VALUES (?,?,?,?,?,?,?)",
            (cid, item_id, dump(local), dump(remote), dump(base), reason, self.clock.tick().ms))
        self.db.commit()
        return cid

    # ------------------------------------------------------------------ 状态与配置
    def state_get(self, key: str) -> Any | None:
        row = self.db.execute("SELECT v FROM sync_state WHERE k=?", (key,)).fetchone()
        return json.loads(row["v"]) if row else None

    def state_set(self, key: str, value: Any) -> None:
        self.db.execute("INSERT INTO sync_state (k,v) VALUES (?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
                        (key, json.dumps(value, ensure_ascii=False)))
        self.db.commit()

    def bootstrap_candidate(self) -> bool:
        """新装机器判据（§11.4）：没有同步基线，且本地没有任何待推送改动。

        不能靠「本地库文件是否存在」猜 —— 重装 exe 但保留了 db 的机器不该被当成新机。
        """
        return self.state_get("base_commit") is None and self.dirty_count() == 0

    # ------------------------------------------------------------------ 撤销日志
    def op_begin(self) -> None:
        self._local.ops = []

    def op_touch(self, before: Item | None, item_id: str, kind: str) -> None:
        """记录一次写入的前像。before=None 表示这条是新建。"""
        buf = getattr(self._local, "ops", None)
        if buf is None:
            return
        buf.append((item_id, kind,
                    json.dumps(before.payload(), ensure_ascii=False, sort_keys=True) if before else None))

    def op_flush(self, feature: str) -> int | None:
        """落一条撤销记录。feature 为空表示「只清缓冲、不记账」（不可撤销的功能）。"""
        buf = getattr(self._local, "ops", None) or []
        self._local.ops = []
        if not buf or not feature:
            return None
        cur = self.db.execute("INSERT INTO ops (feature, created_at) VALUES (?,?)",
                              (feature, self.clock.tick().ms))
        op_id = cur.lastrowid
        self.db.executemany("INSERT INTO op_items (op_id, item_id, kind, before_json) VALUES (?,?,?,?)",
                            [(op_id, iid, k, bj) for iid, k, bj in buf])
        self.db.commit()
        return op_id

    def last_open_op(self) -> dict | None:
        row = self.db.execute("SELECT * FROM ops WHERE undone = 0 ORDER BY id DESC LIMIT 1").fetchone()
        if row is None:
            return None
        items = self.db.execute(
            "SELECT item_id, kind, before_json FROM op_items WHERE op_id=? ORDER BY rowid",
            (row["id"],)).fetchall()
        return {"id": row["id"], "feature": row["feature"], "created_at": row["created_at"],
                "items": [{"item_id": i["item_id"], "kind": i["kind"],
                           "before": json.loads(i["before_json"]) if i["before_json"] else None}
                          for i in items]}

    def mark_op_undone(self, op_id: int) -> None:
        self.db.execute("UPDATE ops SET undone = 1 WHERE id = ?", (op_id,))
        self.db.commit()

    # ------------------------------------------------------------------ 视图偏好（本地）
    def pref_get(self, key: str | None = None) -> Any:
        if key is None:
            return {r["key"]: json.loads(r["value"])
                    for r in self.db.execute("SELECT * FROM view_pref ORDER BY key")}
        row = self.db.execute("SELECT value FROM view_pref WHERE key=?", (key,)).fetchone()
        return json.loads(row["value"]) if row else None

    def pref_set(self, key: str, value: Any) -> None:
        self.db.execute(
            "INSERT INTO view_pref (key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, json.dumps(value, ensure_ascii=False, sort_keys=True)))
        self.db.commit()

    # ------------------------------------------------------------------ 重复任务历史（本地）
    def occurrence_add(self, item_id: str, due_at: str, at: str, kind: str = "done") -> None:
        """记一次完成/跳过。

        `INSERT OR REPLACE` 而不是 `INSERT OR IGNORE`：同一次被重复记录时（撤销后再完成、
        或另一条路径重放）应该保留**最后一次**的操作时刻与类型，忽略会让「跳过」永远
        改不回「完成」。
        """
        if not due_at:
            return                            # 没有到期日就没有「哪一次」可记
        self.db.execute(
            "INSERT INTO occurrence (item_id,due_at,at,kind) VALUES (?,?,?,?) "
            "ON CONFLICT(item_id,due_at) DO UPDATE SET at=excluded.at, kind=excluded.kind",
            (item_id, str(due_at), str(at), kind))
        self.db.commit()

    def occurrence_delete(self, item_id: str, due_at: str) -> int:
        """撤销一次推进时把历史记录一并撤掉（否则历史里会留一条没发生过的完成）。"""
        cur = self.db.execute("DELETE FROM occurrence WHERE item_id=? AND due_at=?",
                              (item_id, str(due_at)))
        self.db.commit()
        return cur.rowcount

    def occurrence_list(self, item_id: str | None = None, limit: int = 100) -> list[dict]:
        sql = "SELECT item_id, due_at, at, kind FROM occurrence"
        args: list[Any] = []
        if item_id is not None:
            sql += " WHERE item_id=?"
            args.append(item_id)
        sql += " ORDER BY at DESC, due_at DESC LIMIT ?"
        args.append(int(limit))
        return [dict(r) for r in self.db.execute(sql, args)]

    def occurrence_count(self, item_id: str) -> dict[str, int]:
        rows = self.db.execute(
            "SELECT kind, COUNT(*) c FROM occurrence WHERE item_id=? GROUP BY kind",
            (item_id,)).fetchall()
        out = {"done": 0, "skipped": 0}
        for r in rows:
            out[r["kind"]] = int(r["c"])
        out["total"] = out["done"] + out["skipped"]
        return out

    # ------------------------------------------------------------------ 搜索
    def search_text(self, q: str, kinds: Iterable[str] | None = None,
                    include_deleted: bool = False, limit: int = 200) -> list[Item]:
        """子串搜索用 LIKE，不用 FTS5。

        实测（spike/fts_probe.py）：FTS5 的 trigram 分词器按 3 字符建索引，
        「立案」「周报」「猫粮」这类两字中文词命中数恒为 0；默认 unicode61 更是
        把整串中文当一个 token。而 LIKE 在 5000 行上单次扫描约 1.8ms，够用。
        """
        like = f"%{q}%"
        sql = ["SELECT * FROM item WHERE (data LIKE ? OR meta LIKE ?)"]
        args: list[Any] = [like, like]
        if kinds:
            ks = list(kinds)
            sql.append(f"AND kind IN ({','.join('?' * len(ks))})")
            args += ks
        if not include_deleted:
            sql.append("AND deleted = 0")
        sql.append("ORDER BY kind, id LIMIT ?")
        args.append(int(limit))
        return [self._row_to_item(r) for r in self.db.execute(" ".join(sql), args)]

    # ------------------------------------------------------------------ 密钥（DPAPI 密文）
    def secret_get(self, key: str) -> str | None:
        row = self.db.execute("SELECT v FROM secret WHERE k=?", (key,)).fetchone()
        return row["v"] if row else None

    def secret_set(self, key: str, value: str) -> None:
        if not key:
            raise ValueError("密钥位名不能为空")
        self.db.execute("INSERT INTO secret (k,v) VALUES (?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
                        (key, value))
        self.db.commit()

    def secret_delete(self, key: str) -> None:
        self.db.execute("DELETE FROM secret WHERE k=?", (key,))
        self.db.commit()

    # ------------------------------------------------------------------ 告警作业
    def alert_clear_pending(self, item_id: str) -> int:
        """重建前清掉该条目的未发送作业。已 sent 的保留，避免重启后重复推送。"""
        cur = self.db.execute("DELETE FROM alert_job WHERE item_id=? AND status='pending'", (item_id,))
        self.db.commit()
        return cur.rowcount

    def alert_add(self, item_id: str, fire_at: int, channel: str) -> bool:
        """插入作业。dedup_key 唯一 → 重复调用不会重发（幂等的核心）。"""
        try:
            self.db.execute(
                "INSERT INTO alert_job (item_id, fire_at, channel, status, dedup_key) "
                "VALUES (?,?,?,'pending',?)",
                (item_id, int(fire_at), channel, f"{item_id}:{int(fire_at)}:{channel}"))
            self.db.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def alert_due(self, now_ms: int, limit: int = 50) -> list[sqlite3.Row]:
        return self.db.execute(
            "SELECT * FROM alert_job WHERE status='pending' AND fire_at<=? "
            "AND (next_retry IS NULL OR next_retry<=?) ORDER BY fire_at LIMIT ?",
            (int(now_ms), int(now_ms), int(limit))).fetchall()

    def alert_pending_count(self, now_ms: int | None = None) -> int:
        if now_ms is None:
            row = self.db.execute("SELECT COUNT(*) c FROM alert_job WHERE status='pending'").fetchone()
        else:
            row = self.db.execute(
                "SELECT COUNT(*) c FROM alert_job WHERE status='pending' AND fire_at>?",
                (int(now_ms),)).fetchone()
        return int(row["c"])

    def alert_mark(self, job_id: int, status: str, error: str | None = None,
                   retry_at: int | None = None) -> None:
        self.db.execute(
            "UPDATE alert_job SET status=?, error=?, next_retry=?, "
            "attempts = attempts + CASE WHEN ? IN ('failed','pending') THEN 1 ELSE 0 END, "
            "sent_at = CASE WHEN ?='sent' THEN CAST(strftime('%s','now') AS INTEGER)*1000 ELSE sent_at END "
            "WHERE id=?",
            (status, error, retry_at, status, status, job_id))
        self.db.commit()

    def alert_skip_stale(self, cutoff_ms: int) -> int:
        """补发防轰炸：比 cutoff 还早的 pending 一律 skipped。

        没有这条，关机一天后开机瞬间会涌出几十条提醒。
        """
        cur = self.db.execute(
            "UPDATE alert_job SET status='skipped', error='stale_at_startup' "
            "WHERE status='pending' AND fire_at < ?", (int(cutoff_ms),))
        self.db.commit()
        return cur.rowcount

    def alert_log_add(self, channel: str, level: str, detail: str, item_id: str | None = None) -> None:
        self.db.execute("INSERT INTO alert_log (ts, item_id, channel, level, detail) VALUES (?,?,?,?,?)",
                        (self.clock.tick().ms, item_id, channel, level, detail[:500]))
        self.db.commit()

    def alert_log_list(self, limit: int = 100) -> list[dict]:
        rows = self.db.execute("SELECT * FROM alert_log ORDER BY ts DESC LIMIT ?", (int(limit),)).fetchall()
        return [{"ts": r["ts"], "item_id": r["item_id"], "channel": r["channel"],
                 "level": r["level"], "detail": r["detail"]} for r in rows]

    # ------------------------------------------------------------------ 内部
    def _row_to_item(self, row: sqlite3.Row) -> Item:
        return Item(kind=row["kind"], id=row["id"],
                    rev=Rev(row["rev_ms"], row["rev_seq"], row["rev_dev"]),
                    data=json.loads(row["data"]),
                    deleted=bool(row["deleted"]),
                    deleted_at_ms=row["deleted_at_ms"],
                    schema=row["schema"],
                    meta=json.loads(row["meta"]))

    def _setting(self, key: str) -> str | None:
        row = self.db.execute("SELECT v FROM settings WHERE k=?", (key,)).fetchone()
        return row["v"] if row else None

    def _setting_set(self, key: str, value: str) -> None:
        self.db.execute("INSERT INTO settings (k,v) VALUES (?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
                        (key, value))
        self.db.commit()

    def _json_setting(self, key: str) -> Any | None:
        raw = self._setting(key)
        return json.loads(raw) if raw else None

    def _json_setting_set(self, key: str, value: Any) -> None:
        self._setting_set(key, json.dumps(value, ensure_ascii=False, sort_keys=True))

    def close(self) -> None:
        """只关本线程的连接。其它线程的连接由各自的 close 负责。"""
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None
