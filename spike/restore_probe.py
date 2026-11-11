"""探针：恢复流程到底换的是哪个库。"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, "src")

from qingdan.core import backup as bk
from qingdan.core import context
from qingdan.core.registry import call

d = Path(tempfile.mkdtemp()) / "data"
context.init(d)
tid = call("task.create", title="老数据")["id"]
snap = bk.create(force=True)
print("快照:", snap["name"], snap["size"], "B")
call("task.delete", item_id=tid)
print("删除后本地:", call("task.get", item_id=tid)["deleted"])

# 快照里到底是什么状态
import sqlite3
probe = sqlite3.connect(snap["path"])
print("快照内该条:", probe.execute(
    "SELECT id, deleted FROM item WHERE id=?", (tid,)).fetchall())
probe.close()

out = bk.schedule_restore(snap["name"])
print("调度:", out["restore_from"], "标记存在:",
      (d / bk.RESTORE_MARKER).is_file())
context.shutdown()

c2 = context.init(d)
print("换库后该条:", c2.store.get(tid))
print("before-restore 存在:", (d / "db.sqlite.before-restore").is_file())
print("backups 目录:", sorted(p.name for p in (d / "backups").iterdir()))
context.shutdown()
