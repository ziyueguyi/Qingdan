"""同步引擎（M5）。

对应 `docs/M5-同步测试矩阵.md`。三条不可妥协的规则：

1. **拉下来的版本不得重新发号**（B15）。远端 item 落地时 rev 原样保留 —— 如果给它
   发一个新 rev，另一台机器就会认为"本地更新"，把远端的真实改动压掉，LWW 当场失效。
2. **本地为空绝不等于"全都该删"**（B1/B3）。删除只能以墓碑形式显式表达；
   任何"远端有、本地没有"的情况在没有 base 佐证时一律按新增导入处理。
   这条保险丝保证任何 bug 或异常装机路径都不可能把远端整库清空。
3. **只用 fetch，不用 pull**（见 gitrepo 模块）。合并发生在这一层，按 item 三方裁决。
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Iterable

from qingdan.core import compact
from qingdan.core.gitrepo import GitRepo, GitError, PushRejected, redact
from qingdan.core.merge import (external_delete_detected, pick, same,
                                should_record_conflict)
from qingdan.core.models import Item
from qingdan.core.store import Store

MAX_PUSH_ATTEMPTS = 3
#: 本机钟与服务器钟差多少就**不再推送**（§10 的承诺，本轮补实现）。
#: 版本先后全靠 ms，钟差 5 分钟就意味着"谁的更新"是假的；这种覆盖事后看不出来。
#: 只挡推送不挡拉取 —— 拉下来是别人的事实，不会伤害任何人。
SKEW_LIMIT_MS = 300_000
ITEM_PREFIX = "items/"


class SyncAborted(GitError):
    """同步被主动中止（例如私有哨兵发现仓库变公开）。"""


@dataclass
class SyncReport:
    mode: str = "merge"                 # bootstrap | first_merge | merge
    pulled: int = 0
    pushed: int = 0
    conflicts: int = 0
    anomalies: list[str] = field(default_factory=list)
    base_commit: str | None = None
    ok: bool = True
    error: str | None = None
    error_key: str | None = None        # 机器键（clock_skew / …），展示文案归界面
    attempts: int = 0

    def as_dict(self) -> dict:
        return {"mode": self.mode, "pulled": self.pulled, "pushed": self.pushed,
                "conflicts": self.conflicts, "anomalies": self.anomalies,
                "base_commit": self.base_commit, "ok": self.ok, "error": self.error,
                "error_key": self.error_key, "attempts": self.attempts}


def parse_tree(files: dict[str, bytes]) -> dict[str, Item]:
    """把仓库 tree 解析成 {item_id: Item}。非 items/ 前缀与坏文件跳过并记录。"""
    out: dict[str, Item] = {}
    for path, blob in files.items():
        if not path.startswith(ITEM_PREFIX) or not path.endswith(".json"):
            continue
        try:
            item = Item.from_bytes(blob)
        except Exception:
            continue                     # 坏文件不能让整轮同步崩掉
        out[item.id] = item
    return out


class BaseSnapshots:
    """按需还原「上次双方一致时那条记录的完整内容」。

    为什么需要它：`store.base_map()` 只还原裁决要的 `(rev, deleted)`，这没错 ——
    但冲突界面要的是**逐字段解释谁改了什么**。没有基线字段，每个字段都只能显示成
    "两边都改了"，用户得自己猜哪边才是自己想要的；那这个界面就是把裁决的活儿
    又推回给人（实测过：真撞车生成的冲突记录里 base 的 data 全是 None，
    priority 明明只有 A 改了，界面却说两边都改）。

    基线的完整内容一直都在 git 里：`base_commit` 就是上次同步完成时的那棵树。
    所以这里只在**真要记冲突时**读一次树，之后按 id 取那一个 blob；
    读不到（首轮无基线、对象被 gc、payload 坏了）就退回 rev-only 快照，
    绝不因为还原不了基线而把整轮同步弄失败。
    """

    def __init__(self, git: GitRepo | None = None, sha_hex: str | None = None):
        self.git = git
        self.sha_hex = sha_hex
        self._files: dict[str, bytes] | None = None

    def _load(self) -> dict[str, bytes]:
        if self._files is None:
            if self.git is None or not self.sha_hex:
                self._files = {}
                return self._files
            try:
                sha = bytes.fromhex(self.sha_hex) if self.sha_hex else None
                self._files = self.git.read_tree(sha) if sha else {}
            except Exception:
                self._files = {}        # 基线读不出来就退回 fallback，不能连累同步
        return self._files

    def get(self, item_id: str, fallback: Item | None) -> Item | None:
        """带完整字段的基线快照；没有就返回原来的 rev-only 版本。"""
        if fallback is None:
            return None                 # 基线里本来就没有这条 = 新增，无需字段
        blob = self._load().get(f"items/{fallback.kind}/{item_id}.json")
        if blob is None:
            return fallback
        try:
            return Item.from_payload(json.loads(blob.decode("utf-8")))
        except Exception:
            return fallback


class SyncEngine:
    def __init__(self, store: Store, git: GitRepo, on_anomaly=None):
        self.store = store
        self.git = git
        self.on_anomaly = on_anomaly or (lambda msg: None)
        #: 本轮的基线快照。sync_once 开头会换成带 sha 的实例；不在 sync 流程里
        #: 直接调 `_merge`（单测、将来的自建 git）也能跑，只是不做字段还原。
        self._base_snap = BaseSnapshots()

    def _base_of(self, item_id: str, fallback: Item | None) -> Item | None:
        """记冲突时用的基线：尽量带完整字段，退而求其次用 rev-only。"""
        return self._base_snap.get(item_id, fallback)

    # ------------------------------------------------------------------ 模式判定
    def detect_mode(self) -> str:
        """新装机判据（§11.4）——不能靠"本地库文件是否存在"猜。

        重装 exe 但保留了 db 的机器不该被当成新机；离线用过再首次登录的机器
        必须走 FIRST_MERGE，否则本地数据会被远端覆盖。
        """
        if self.store.state_get("base_commit") is not None:
            return "merge"
        return "bootstrap" if self.store.dirty_count() == 0 else "first_merge"

    # ------------------------------------------------------------------ 主流程
    def sync_once(self, remote_url: str | None = None) -> SyncReport:
        """跑一轮同步，**结束时一定清一次凭据**。

        为什么包一层而不是在每个 git 调用后面补：fetch 有四个返回点、push 有重试循环，
        clone/fetch/push 都会往 `.git/logs`、`FETCH_HEAD` 里写带凭据的 URL。
        漏一处的后果不是报错，是**令牌静静留在磁盘上** —— 这种"部分正确"的清理
        比不清理更危险，因为它会让人以为已经安全了（今天就是这么翻车的：
        第一版只清了 .git/config，我据此宣布磁盘上没有令牌，直到扫 reflog 才发现还有）。
        """
        try:
            return self._sync_once(remote_url)
        finally:
            self.git.scrub_credentials_after_use()

    def _sync_once(self, remote_url: str | None = None) -> SyncReport:
        mode = self.detect_mode()
        report = SyncReport(mode=mode)
        url = remote_url or self.git.remote_url
        if not url:
            raise SyncAborted("未配置远端仓库")

        try:
            if not self.git.initialized:
                self.git.clone(url)
            remote_head = self.git.fetch(url)
        except GitError as e:
            report.ok = False
            report.error = str(e)
            return report                     # B9：断网时 dirty 完整保留

        remote_files = self.git.read_tree(remote_head) if remote_head else {}
        remote_items = parse_tree(remote_files)
        local_items = self.store.snapshot()
        base_items = self.store.base_map()
        # 基线 commit 必须在 _merge 之前取：本轮结束时会把它覆写成新的
        self._base_snap = BaseSnapshots(self.git, self.store.state_get("base_commit"))

        if mode == "bootstrap":
            self._pull_only(remote_items, remote_head, report)
            return report

        self._merge(remote_items, local_items, base_items, report)

        # 必须在 _push 之前处理：_merge 看到「远端没有 + 本地有更新的编辑」会判本地胜出，
        # 于是把回收掉的已删项重新推回远端 —— 这就是 D1 的静默复活。
        graveyard = compact.parse_graveyard(remote_files)
        self._apply_graveyard(graveyard, base_items, report)

        # B3/B4 保险丝：远端相对 base 消失且本地没有墓碑 → 绝不生成删除
        for item_id, base in base_items.items():
            if item_id in remote_items or item_id in graveyard:
                continue                      # 名册里的缺失是「已确认删除」，不是协议外
            if item_id in local_items and external_delete_detected(local_items[item_id], None, base):
                report.anomalies.append(f"external_delete:{item_id}")
                self.on_anomaly(f"远端 {item_id} 被手工删除（无墓碑），已保留本地版本")

        report.pushed = self._push(local_items, base_items, remote_head, remote_items, report)
        # base_commit 记「本轮结束时本地分支的位置」：推送成功时它已经叠在
        # remote_head 之上，直接记 remote_head 会永远落后一个提交。
        head_after = self.git.head() or remote_head
        self.store.state_set("base_commit", head_after.hex() if head_after else None)
        report.base_commit = self.store.state_get("base_commit")
        return report

    # ------------------------------------------------------------------ 各阶段
    def _pull_only(self, remote_items: dict[str, Item], remote_head, report: SyncReport) -> None:
        """新装机器：全量导入，不推任何东西。"""
        for item in remote_items.values():
            self._apply_remote(item)
            report.pulled += 1
        self.store.state_set("base_commit", remote_head.hex() if remote_head else None)
        self.store.state_set("bootstrap_done", int(time.time() * 1000))

    def _merge(self, remote_items, local_items, base_items, report: SyncReport,
               ) -> None:
        """逐 id 三方裁决。"""
        for item_id in set(local_items) | set(remote_items) | set(base_items):
            local = local_items.get(item_id)
            remote = remote_items.get(item_id)
            base = base_items.get(item_id)
            winner, loser = pick(local, remote, base)
            # 冲突留档要过策略：两边都删时结果一致，不该打扰用户（A11）
            if loser is not None and should_record_conflict(winner, loser):
                self.store.record_conflict(item_id, "both_changed", local=local, remote=remote,
                                           base=self._base_of(item_id, base))
                report.conflicts += 1
            if winner is not None and not same(winner, local):
                self._apply_remote(winner)
                report.pulled += 1

    def _apply_graveyard(self, graveyard: dict, base_items: dict, report: SyncReport,
                         ) -> None:
        """按回收名册把本地压回"已删除"，编辑保留到冲突表但不复活（D1）。"""
        for item_id, entry in graveyard.items():
            local = self.store.get(item_id)     # 用 _merge 之后的最新状态
            if local is None or local.deleted:
                continue
            tombstone_rev = compact.entry_rev(entry)
            if local.rev > tombstone_rev:
                # 离线期间的编辑比删除还新：LWW 本来会复活它。
                # 这里保住编辑（进 conflict 可人工恢复），但维持删除决定。
                self.store.record_conflict(
                    item_id, "resurrect_after_compaction", local=local, remote=None,
                    base=self._base_of(item_id, base_items.get(item_id)))
                report.conflicts += 1
                report.anomalies.append(f"resurrect_blocked:{item_id}")
                self.on_anomaly(f"{item_id} 离线期间被改过，但已在其它设备删除；"
                                f"改动已存入冲突记录，未复活")
            compact.apply_deletion(self.store, item_id, entry)
            report.pulled += 1

    def apply_remote_only(self, files: dict[str, bytes]) -> int:
        """只把远端内容落到本地，不推任何东西。返回落地条数。"""
        count = 0
        for item in parse_tree(files).values():
            local = self.store.get(item.id)
            if local is not None and not same(item, local) and local.rev > item.rev:
                continue                       # 本地更新，不被"只拉"覆盖
            self._apply_remote(item)
            count += 1
        return count

    def _apply_remote(self, item: Item) -> None:
        """落地远端版本：**保留原 rev、不标 dirty**（B15/B22）。"""
        self.store.observe(item.rev)
        self.store.put(item, dirty=False)
        self.store.set_base(item.id, item.rev, item.deleted)

    def _push(self, local_items, base_items, remote_head, remote_items,
              report: SyncReport) -> int:
        """把本地改动写成一次提交并推送；被拒就重新 fetch + 合并再来一轮（B7/B8）。"""
        skew = abs(int(self.store.clock.offset_ms))
        if skew > SKEW_LIMIT_MS:
            # 宁可这轮不推。版本先后完全靠 ms，钟不准时"谁的更新"是假的 ——
            # 推上去会把对方真实的改动判成旧的，那种覆盖事后看不出来。
            # 拉取侧不受影响：远端版本照常落地，本地改动仍是 dirty，钟校好了自己就推。
            report.ok = False
            report.error_key = "clock_skew"
            report.error = f"offset={self.store.clock.offset_ms}ms limit={SKEW_LIMIT_MS}ms"
            report.anomalies.append(f"push_blocked:clock_skew:{skew}")
            return 0

        for attempt in range(1, MAX_PUSH_ATTEMPTS + 1):
            report.attempts = attempt
            dirty = [it for it in self.store.list_items(include_deleted=True, dirty_only=True)]
            if not dirty and attempt == 1:
                # 没有本地改动也要把本地分支对齐到远端 head，否则工作区永远落后，
                # 下一轮的 base_sha 就是过期的（实测导致两台机器 tree 不一致）。
                current = self.git.head()
                if remote_head is not None and current != remote_head:
                    self.git.reset_to(remote_head)
                self._finalize_base(remote_items)
                return 0

            files = {it.path(): it.to_bytes() for it in dirty}
            try:
                sha = self.git.commit_and_push(files, message=f"sync: {len(files)} items "
                                                             f"dev={self.store.device_id}",
                                               base_sha=remote_head)
            except PushRejected:
                # 远端在我们 fetch 之后又前进了 → 重新拉、重新合并、再推（B7）
                try:
                    remote_head = self.git.fetch(self.git.remote_url)
                except GitError as e:
                    report.ok, report.error = False, str(e)
                    return 0
                remote_items = parse_tree(self.git.read_tree(remote_head))
                self._merge(remote_items, self.store.snapshot(), self.store.base_map(), report)
                continue
            except GitError as e:
                report.ok, report.error = False, str(e)
                return 0

            self.store.set_base_many([(it.id, it.rev, it.deleted) for it in dirty])
            report.pushed = len(files)
            self._finalize_base(remote_items)
            self.store.state_set("last_sync_at", int(time.time() * 1000))
            _ = sha
            return report.pushed

        # 三次都没推上去：改动必须原样留在 dirty 里，一个都不丢（B8）
        report.ok = False
        report.error = f"连续 {MAX_PUSH_ATTEMPTS} 次推送被拒，改动已保留待下次同步"
        return 0

    def _finalize_base(self, remote_items: dict[str, Item]) -> None:
        """远端有、本地没有 base 的项，把 base 补上 —— 否则下一轮无法判断"谁删的"。"""
        for item_id, item in remote_items.items():
            if self.store.base_of(item_id) is None and self.store.get(item_id) is not None:
                current = self.store.get(item_id)
                self.store.set_base(item_id, current.rev, current.deleted)
