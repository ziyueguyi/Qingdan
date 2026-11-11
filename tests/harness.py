"""同步测试的公共 harness：一台"设备"= 独立 Store + 独立 git 工作副本 + 独立设备指纹。

放在这里而不是各测试文件里复制一份，是因为这套 harness 承载了同步协议的
关键前提（设备隔离、真实 git 读写）。复制两份的话，改一处忘另一处，
测出来的"通过"就没有意义了。
"""
from __future__ import annotations

import os
import shutil
import tempfile
import time

import pytest
from dulwich import porcelain

from qingdan.core.gitrepo import GitRepo
from qingdan.core.models import Item, new_id
from qingdan.core.store import Store
from qingdan.core.sync import SyncEngine


class Machine:
    def __init__(self, root: str, name: str, remote: str, device: str | None = None):
        self.root = root
        self.name = name
        self.remote = remote
        self.store = Store(os.path.join(root, name, "db.sqlite"), device_id=device or f"D-{name}")
        self.git = GitRepo(os.path.join(root, name, "repo"), branch="master")
        self.anomalies: list[str] = []
        self.engine = SyncEngine(self.store, self.git, on_anomaly=self.anomalies.append)

    # -- 本地写入 ----------------------------------------------------------
    def add(self, kind="task", **data) -> Item:
        item = Item(kind=kind, id=new_id(), rev=self.store.next_rev(), data=data)
        self.store.put(item, dirty=True)
        return item

    def edit(self, item_id: str, **changes) -> Item:
        old = self.store.get(item_id)
        item = Item(kind=old.kind, id=old.id, rev=self.store.next_rev(),
                    data={**old.data, **changes}, meta=old.meta)
        self.store.put(item, dirty=True)
        return item

    def delete(self, item_id: str) -> Item:
        old = self.store.get(item_id)
        item = Item(kind=old.kind, id=old.id, rev=self.store.next_rev(), data=old.data,
                    deleted=True, deleted_at_ms=int(time.time() * 1000), meta=old.meta)
        self.store.put(item, dirty=True)
        return item

    # -- 同步 --------------------------------------------------------------
    def sync(self):
        return self.engine.sync_once(self.remote)

    def compact(self, days=0, now_ms=None):
        from qingdan.core import compact as compact_mod
        return compact_mod.run(self.store, self.git, days=days, now_ms=now_ms)

    # -- 断言辅助 ----------------------------------------------------------
    def titles(self, kind="task") -> set[str]:
        return {i.data.get("title") for i in self.store.list_items(kind=kind)}

    def item(self, item_id) -> Item | None:
        return self.store.get(item_id)

    def remote_paths(self) -> set[str]:
        head = self.git.head()
        return set(self.git.read_tree(head)) if head else set()

    def conflicts(self) -> list:
        return list(self.store.db.execute("SELECT item_id, reason FROM conflict"))


@pytest.fixture
def remote():
    d = tempfile.mkdtemp(prefix="qd-sync-")
    bare = os.path.join(d, "remote.git")
    porcelain.init(bare, bare=True)
    yield bare
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def root():
    d = tempfile.mkdtemp(prefix="qd-machines-")
    yield d
    shutil.rmtree(d, ignore_errors=True)
