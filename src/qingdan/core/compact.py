"""墓碑回收（M5 / D1）。

回收墓碑是为了不让仓库无限增长，但它撕掉了一个关键证据：**删除这件事本身**。
墓碑没了之后，一台离线超过回收窗口的机器再上线，会看到「远端没有这个文件」，
而它本地还留着一条更新的编辑 —— 按 LWW 判，编辑压过「什么都没有」，
于是**已删项被静默复活**。

所以回收不能只删文件，必须同时写一份名册 `graveyard.json`，记下每个被回收项的
最后墓碑版本。同步时先查名册：

  · 远端缺项 + 名册里有 → 这是**已确认的删除**，本地维持已删；
    若本地存在比墓碑更新的编辑 → 进 conflict 表（编辑不丢），但**不复活**，
    由用户在冲突界面决定要不要恢复。
  · 远端缺项 + 名册里没有 → 协议外手工删除，走 B4 的 keep + 告警。

这条区分是整块设计的核心：**同样是"文件不见了"，语义完全相反**。
"""
from __future__ import annotations

import json
import time
from typing import Any

from qingdan.core import stamp
from qingdan.core.clock import Rev
from qingdan.core.gitrepo import GitRepo
from qingdan.core.models import Item
from qingdan.core.store import Store

GRAVEYARD_PATH = "graveyard.json"
DEFAULT_TOMBSTONE_DAYS = 90


def empty_graveyard() -> dict:
    return {"schema": 1, "entries": {}}


def parse_graveyard(files: dict[str, bytes]) -> dict[str, dict[str, Any]]:
    blob = files.get(GRAVEYARD_PATH)
    if not blob:
        return {}
    try:
        return json.loads(blob.decode("utf-8")).get("entries", {})
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {}          # 名册坏了不能拖垮同步；缺项会退化成 B4 的保守路径


def dump_graveyard(entries: dict[str, dict[str, Any]]) -> bytes:
    payload = {"schema": 1, "entries": dict(sorted(entries.items()))}
    return (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def entry_rev(entry: dict[str, Any]) -> Rev:
    return Rev.from_json(entry["rev"])


def plan(store: Store, days: int = DEFAULT_TOMBSTONE_DAYS,
         now_ms: int | None = None,
         already_graveyarded: Iterable[str] = ()) -> list[Item]:
    """列出可以回收的墓碑：已删除、超过保留窗口、且**尚未进名册**。

    必须排除已进名册的项：本地 SQLite 的墓碑行在回收后仍然存在（它是删除证据，
    不该删），不排除的话每次 compact 都会把同一批项再"回收"一遍，
    然后因为文件早就不在仓库里而报错。
    """
    # deleted_at_ms 是 stamp 写的，截止也必须同一把缝，否则「90 天」会随真实时钟漂
    cutoff = (now_ms if now_ms is not None else stamp.now_ms()) - days * 86_400_000
    skip = set(already_graveyarded)
    return [it for it in store.list_items(include_deleted=True)
            if it.deleted and it.id not in skip
            and (it.deleted_at_ms or 0) > 0 and it.deleted_at_ms < cutoff]


def apply_deletion(store: Store, item_id: str, entry: dict[str, Any]) -> Item | None:
    """按名册把本地状态压回"已删除"，**保留名册里的权威版本戳**。

    不能给这条删除重新发号：发号会让它看起来是"本地新改动"，
    反过来推给其它机器，把删除的时间线搅乱。
    """
    current = store.get(item_id)
    if current is None:
        return None
    rev = entry_rev(entry)
    tombstone = Item(kind=current.kind, id=current.id, rev=rev, data=current.data,
                     deleted=True, deleted_at_ms=entry.get("deleted_at_ms"),
                     meta=current.meta)
    store.put(tombstone, dirty=False)
    store.set_base(item_id, rev, True)
    return tombstone


def run(store: Store, git: GitRepo, days: int = DEFAULT_TOMBSTONE_DAYS,
        now_ms: int | None = None, remote_head=None) -> dict:
    """执行回收：删文件 + 写名册，一次提交。"""
    head = remote_head if remote_head is not None else git.head()
    entries = parse_graveyard(git.read_tree(head)) if head else {}
    doomed = plan(store, days, now_ms, already_graveyarded=entries.keys())
    if not doomed:
        return {"removed": 0, "graveyard_size": len(entries)}

    for it in doomed:
        entries[it.id] = {"rev": it.rev.to_json(), "deleted_at_ms": it.deleted_at_ms}

    git.commit_and_push(
        files={GRAVEYARD_PATH: dump_graveyard(entries)},
        remove=[it.path() for it in doomed],
        message=f"compact: drop {len(doomed)} tombstones (older than {days}d)",
        base_sha=head,
    )
    return {"removed": len(doomed), "graveyard_size": len(entries),
            "ids": [it.id for it in doomed]}
