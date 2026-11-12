"""鉴权、仓库就绪、同步接口（M4 + M5 的接口层）。

私有哨兵（R10）在这里：**每轮 push 前重新确认 `private == true`**。
改公开不会有任何报错提示，清单会安静地挂到公网上 —— 所以不能只在建仓时检查一次。
"""
from __future__ import annotations

import time
import threading
from functools import wraps
from typing import Any

from qingdan import __version__
from qingdan.core import compact as compact_mod
from qingdan.core import merge
from qingdan.core import secrets
from qingdan.core.context import ctx
from qingdan.core.gitee import GiteeClient, GiteeError
from qingdan.core.gitrepo import (GitRepo, GitError, has_credentials, redact,
                                strip_credentials)
from qingdan.core.registry import QdError, auth_required, feature, not_found, validation_error
from qingdan.core.sync import SKEW_LIMIT_MS, SyncEngine

TOKEN_KEY = "gitee_token"
REPO_STATE = "repo"
DEFAULT_REPO_NAME = "qd-data"          # 刻意取无语义名，降低在账号列表里的辨识度
REPO_DESC = "Qingdan 任务清单数据仓库（客户端自动同步，请勿手工编辑）"
PROGRESS_KEY = 'sync_progress'
_active_stores = set()
_active_lock = threading.Lock()


def _update_progress(store, **values):
    with store.operation_lock:
        current = store.state_get(PROGRESS_KEY) or {}
        current.update(values, updated_at=int(time.time()*1000))
        store.state_set(PROGRESS_KEY, current)


def _tracked_sync(fn):
    @wraps(fn)
    def run(*args, **kwargs):
        store = _store()
        if not store.sync_lock.acquire(blocking=False):
            raise QdError('SYNC_BUSY', '已有同步操作正在执行', 409)
        with _active_lock:
            _active_stores.add(store)
        try:
            _update_progress(store, running=True, operation=fn.__name__, phase='preparing',
                             started_at=int(time.time()*1000), finished_at=None,
                             error_code=None, pulled=0, pushed=0, conflicts=0)
            result = fn(*args, **kwargs)
            ok = result.get('ok', True)
            _update_progress(store, running=False, phase='completed' if ok else 'failed',
                             finished_at=int(time.time()*1000),
                             error_code=None if ok else result.get('error_key') or 'SYNC_FAILED',
                             pulled=result.get('pulled', 0), pushed=result.get('pushed', 0),
                             conflicts=result.get('conflicts', 0))
            return result
        except Exception as error:
            _update_progress(store, running=False, phase='failed', finished_at=int(time.time()*1000),
                             error_code=error.code if isinstance(error, QdError) else 'SYNC_FAILED')
            raise
        finally:
            with _active_lock:
                _active_stores.discard(store)
            store.sync_lock.release()
    return run


def _sync_configuration(fn):
    """Credentials and repository selection cannot change midway through a sync.

    Wait outside the task-operation lock so ordinary local edits remain available.
    """
    @wraps(fn)
    def run(*args, **kwargs):
        with _store().sync_lock:
            return fn(*args, **kwargs)
    return run


# --------------------------------------------------------------------------- 内部


def _store():
    return ctx.need_store()


def _token() -> str:
    raw = _store().secret_get(TOKEN_KEY) or ""
    if not raw:
        return ""
    try:
        return secrets.unprotect(raw)
    except secrets.SecretsUnavailable:
        return ""          # 换机器/换用户后解不开，等同未登录


def _client(token: str | None = None) -> GiteeClient:
    """唯一允许的 GiteeClient 构造口 —— 每次都带上校准回调。

    为什么要有这个函数而不是各处 `GiteeClient(...)`：漏掉 `on_offset` 不会报错，
    只会让时钟校准静默不做（登录路径就漏过一次：它要传刚拿到的新令牌，
    于是自己 new 了一个 client，把回调带走了）。这类"忘记接上"的 bug 靠review 抓不住，
    所以 `test_no_unwired_gitee_client` 用 AST 把它禁掉。
    """
    getter = (lambda: (token or "").strip()) if token is not None else _token
    return GiteeClient(getter, on_offset=apply_server_offset)


def apply_server_offset(offset_ms: int) -> None:
    """收到任何一个 Gitee 响应就把偏差写进 HLC —— 不等到同步收尾。

    原先是 `sync_*` 结束时 `_persist_clock()` 再读一次 client 的 `last_offset_ms`，
    但 `_client()` 每次都新建对象，新对象那个字段是 0，于是 `if offset:` 永远不成立，
    **登录后就再也没校准过**。写这个 bug 的当时我以为它有测试守着。
    """
    store = ctx.store
    if store is not None:
        with store.operation_lock:
            store.clock.set_offset(offset_ms)


def _repo_conf() -> dict | None:
    return _store().state_get(REPO_STATE)


def _engine() -> tuple[SyncEngine, GitRepo]:
    if not ctx.authed:
        raise auth_required("同步需要先登录 Gitee")
    conf = _repo_conf()
    if not conf:
        raise QdError("REPO_NOT_READY", "仓库尚未就绪，先调用 repo.ensure", 409)
    git = GitRepo(_store().path.parent / "repo", branch=conf.get("branch", "master"))
    # remote_url 允许覆盖：测试用本地裸仓库跑真实 git 往返，
    # 将来也支持自建 git 服务。没覆盖时才用「令牌拼进 URL」的 Gitee HTTPS 形式。
    git.remote_url = connect_url(conf)
    store = _store()
    return SyncEngine(store, git, on_progress=lambda phase: _update_progress(store, phase=phase)), git


#: Gitee 的 HTTPS 主机。只有它我们能自动补上本机令牌，别的自建主机不能瞎猜。
GITEE_HOST = "gitee.com"


def connect_url(conf: dict) -> str:
    """算出**这一次连接要用的地址**，并保证落库的那份不带凭据。

    三条规则，缺一不可：

    1. 覆盖项里带凭据 → 本次仍然用它（不然别人配好的自建 git 会突然连不上），
       但把剥掉凭据的版本回写数据库，并记一条警告。
    2. 覆盖项不带凭据、且主机就是 Gitee → 用本机 DPAPI 里的令牌现拼一条。
       少了这一步，第 1 条清理过的地址下次启动就会以 HTTPUnauthorized 失败 ——
       这个坑是真 Gitee 跑出来的，单元测试当时只断言了"令牌没了"、没断言"还能同步"。
    3. 没有覆盖项 → 走 push_url（现拼，只在内存，不落库）。
    """
    store = _store()
    url = conf.get("remote_url")
    if not url:
        return _client().push_url(conf["owner"], conf["name"])

    if has_credentials(url):
        clean = strip_credentials(url)
        conf = {**conf, "remote_url": clean}
        store.state_set(REPO_STATE, conf)
        _note_warning("remote_url_credentials_stripped")
        return url                     # 本次照旧可用，别突然打断别人

    if f":/{GITEE_HOST}" in url.replace("\\", "/") or f"://{GITEE_HOST}" in url:
        # 库里这条已经没有凭据了：Gitee 的地址由本机令牌补全，自建主机不在此列
        return _client().push_url(conf.get("owner") or "", conf.get("name") or "")

    return url


def _note_warning(key: str) -> None:
    """把需要界面说一声的安全事件记进 alert_log（channel=sync）。"""
    st = _store()
    st.alert_log_add("sync", "warning", key)


# --------------------------------------------------------------------------- 鉴权


@feature(name="auth.login", group="auth", http=("POST", "/api/v1/auth/session"), needs_auth=False, manages_lock=True)
@_sync_configuration
def auth_login(token: str) -> dict:
    """校验令牌后存入 DPAPI。令牌无效时不落库，避免"存了个坏令牌"这种更难查的状态。"""
    if not token or not token.strip():
        raise validation_error("令牌不能为空")
    # 走同一个构造口：这条路径要拿"刚输入、还没落库"的令牌去探，所以以前是自己 new 的，
    # 结果把校准回调一起漏掉了 —— 登录恰好是唯一必然发生的一次 API 调用，漏掉就等于从不校准。
    probe = _client(token)
    try:
        me = probe.whoami()
    except GiteeError as e:
        raise QdError(e.code, e.message, e.status or 401) from e

    protected = secrets.protect(token.strip())
    with _store().operation_lock:
        _store().secret_set(TOKEN_KEY, protected)
        ctx.authed = True
        ctx.gitee_user = me["login"]
        from qingdan.core.autosync import resume_after_auth
        resume_after_auth()
    # 偏差已由 client 的 on_offset 回调写进 HLC（每个响应都更新），这里只回给界面看。
    return {"login": me["login"], "name": me.get("name"), "clock_offset_ms": probe.last_offset_ms,
            # 令牌怎么存的属于界面文案（前端 i18n 里讲），不放进响应体：
            # 后端写中文 = 英文界面残留中文，契约测试扫响应体时也会报。
            "token_stored": "dpapi"}


@feature(name="auth.status", group="auth", http=("GET", "/api/v1/auth/session"),
         idempotent=True, needs_auth=False)
def auth_status() -> dict:
    conf = _repo_conf()
    return {"authed": bool(_token()), "login": ctx.gitee_user,
            "repo": conf, "clock_offset_ms": _store().clock.offset_ms}


@feature(name="auth.logout", group="auth", http=("DELETE", "/api/v1/auth/session"),
         needs_auth=False, manages_lock=True)
@_sync_configuration
def auth_logout() -> dict:
    """只清凭据、停自动同步。**不动本地数据，不删 dirty 队列，不碰远端仓库。**"""
    with _store().operation_lock:
        _store().secret_delete(TOKEN_KEY)
        ctx.authed = False
        ctx.gitee_user = None
    return {"authed": False}


# --------------------------------------------------------------------------- 仓库就绪


@feature(name="repo.ensure", group="repo", http=("POST", "/api/v1/repo/prepare"), needs_auth=True, manages_lock=True)
@_sync_configuration
def repo_ensure(name: str = DEFAULT_REPO_NAME) -> dict:
    """查仓库 → 不存在则建私有仓。幂等，可反复调用。

    只管「仓库存在不存在」，**不碰 git**：初始提交挪到同步路径里做
    （见 `_ensure_bootstrapped`）。否则这个接口会依赖网络，测试也逼得真连 Gitee。
    """
    client = _client()
    me = client.whoami()
    info = client.repo(me["login"], name)
    created = False

    if info is None:
        info = client.create_repo(name, description=REPO_DESC, private=True)
        created = True

    if not info["private"]:
        # 绝不接管一个公开仓库 —— 清单是个人数据
        raise QdError("REPO_NOT_PRIVATE",
                      f"{me['login']}/{name} 不是私有仓库，客户端拒绝使用它存放清单数据", 409)

    conf = {"owner": me["login"], "name": name, "branch": info.get("default_branch") or "master",
            "prepared_at": int(time.time() * 1000)}
    stored = _repo_conf() or {}
    if stored.get("remote_url"):
        conf["remote_url"] = stored["remote_url"]      # 测试/自建 git 的覆盖不被建仓流程冲掉
    with _store().operation_lock:
        _store().state_set(REPO_STATE, conf)
    return {"full_name": f"{me['login']}/{name}", "private": True, "created": created,
            "branch": conf["branch"]}


def _ensure_bootstrapped(git: GitRepo) -> bool:
    """确保本地有工作副本；远端是空仓库时推初始提交。返回是否做了初始化。"""
    if not git.initialized:
        git.clone(git.remote_url)
    if git.head() is not None:
        return False
    git.commit_and_push({"manifest.json": _manifest_bytes(),
                         ".gitattributes": b"*.json text eol=lf\n"},
                        message="qingdan: init repository", base_sha=None)
    return True


def _manifest_bytes() -> bytes:
    import json
    payload = {"product": "qingdan", "schema": 1,
               "tombstone_days": compact_mod.DEFAULT_TOMBSTONE_DAYS,
               "device_id": _store().device_id,
               "created_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    return (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


@feature(name="repo.info", group="repo", http=("GET", "/api/v1/repo"),
         idempotent=True, needs_auth=True, manages_lock=True)
@_sync_configuration
def repo_info() -> dict:
    conf = _repo_conf()
    if not conf:
        raise QdError("REPO_NOT_READY", "仓库尚未就绪", 409)
    info = _client().repo(conf["owner"], conf["name"])
    if info is None:
        raise not_found("远端仓库（可能已被删除或改名）")
    return {**conf, **info, "push_url": redact(_client().push_url(conf["owner"], conf["name"]))}


# --------------------------------------------------------------------------- 私有哨兵


def assert_repo_private() -> dict:
    """push 前调用。发现仓库变公开 → 立即中止本轮同步。"""
    conf = _repo_conf()
    if not conf:
        raise QdError("REPO_NOT_READY", "仓库尚未就绪", 409)
    info = _client().repo(conf["owner"], conf["name"])
    if info is None:
        raise QdError("REPO_NOT_FOUND", "远端仓库不存在或无权访问", 404)
    if not info["private"]:
        _store().alert_log_add("sync", "error",
                               f"哨兵中止：{conf['owner']}/{conf['name']} 已变为公开仓库，本轮未推送")
        raise QdError("REPO_PUBLIC",
                      f"{conf['owner']}/{conf['name']} 已变为公开仓库，本轮同步已中止。"
                      f"请在仓库设置中改回私有后再同步", 409)
    return info


def prepare_remote() -> dict:
    """哨兵 + 「远端还没有就先建」。**所有要推出去的路径都走这里**。

    为什么要单独一层：`repo.ensure` 只在本地没有仓库配置时才被界面调用，可
    `auth.login` 之后本地就已经有 owner/name 了 —— 于是新用户第一次点"立即同步"
    必然 REPO_NOT_FOUND，看到的正是"配好了却同步不上"这种最难自证的故障。
    建仓是"要推之前必须成立的前提"，属于同步路径，不该指望界面记得调。
    """
    try:
        return assert_repo_private()
    except QdError as e:
        if e.code != "REPO_NOT_FOUND":
            raise
        conf = _repo_conf() or {}
        return repo_ensure(conf.get("name") or DEFAULT_REPO_NAME)


# --------------------------------------------------------------------------- 同步


@feature(name="sync.status", group="sync", http=("GET", "/api/v1/sync/status"),
         idempotent=True, needs_auth=True)
def sync_status() -> dict:
    st = _store()
    offset = int(st.clock.offset_ms)
    # 钟差过大时推送会被引擎挡下（sync.SKEW_LIMIT_MS）。状态里必须说清楚，
    # 否则用户的改动"同步了却没上去"，界面上看起来一切正常。
    return {"mode": _engine()[0].detect_mode(),
            "base_commit": st.state_get("base_commit"),
            "last_sync_at": st.state_get("last_sync_at"),
            "pending": st.dirty_count(),
            "conflicts": st.db.execute("SELECT COUNT(*) c FROM conflict WHERE resolved_by IS NULL").fetchone()["c"],
            "items": st.db.execute("SELECT COUNT(*) c FROM item").fetchone()['c'],
            "clock_offset_ms": offset,
            "skew_limit_ms": SKEW_LIMIT_MS,
            "push_blocked_key": "clock_skew" if abs(offset) > SKEW_LIMIT_MS else None}


@feature(name='sync.progress', group='sync', http=('GET', '/api/v1/sync/progress'),
         idempotent=True, manages_lock=True)
def sync_progress() -> dict:
    store = _store()
    result = store.state_get(PROGRESS_KEY) or {'running':False, 'phase':'idle'}
    with _active_lock:
        active = store in _active_stores
    if result.get('running') and not active:
        result.update(running=False, phase='interrupted', error_code='SYNC_INTERRUPTED')
    return result


@feature(name="sync.run", group="sync", http=("POST", "/api/v1/sync/run"), needs_auth=True, manages_lock=True)
@_tracked_sync
def sync_run() -> dict:
    prepare_remote()               # 哨兵 + 首次自动建仓
    engine, git = _engine()
    _ensure_bootstrapped(git)
    report = engine.sync_once(git.remote_url)
    return report.as_dict()


@feature(name="sync.pull", group="sync", http=("POST", "/api/v1/sync/pull"), needs_auth=True, manages_lock=True)
@_tracked_sync
def sync_pull() -> dict:
    """只拉不推。新装机器或想看远端现状时用。"""
    engine, git = _engine()
    if not git.initialized:
        git.clone(git.remote_url)
    head = git.fetch(git.remote_url)
    files = git.read_tree(head)
    items = engine.apply_remote_only(files)
    return {"pulled": items, "head": head.hex() if head else None}


@feature(name="sync.push", group="sync", http=("POST", "/api/v1/sync/push"), needs_auth=True, manages_lock=True)
@_tracked_sync
def sync_push() -> dict:
    prepare_remote()               # 哨兵 + 首次自动建仓
    engine, git = _engine()
    _ensure_bootstrapped(git)
    report = engine.sync_once(git.remote_url)
    return report.as_dict()


@feature(name="sync.compact", group="sync", http=("POST", "/api/v1/sync/compact"),
         needs_auth=True, manages_lock=True)
@_tracked_sync
def sync_compact(days: int = compact_mod.DEFAULT_TOMBSTONE_DAYS) -> dict:
    """回收超期墓碑并写名册。名册是之后判断"离线复活"的唯一依据，不能省。"""
    prepare_remote()               # 哨兵 + 首次自动建仓
    if not 7 <= int(days) <= 3650:
        raise validation_error("days 需在 7..3650", got=days)
    engine, git = _engine()
    _ensure_bootstrapped(git)
    head = git.fetch(git.remote_url)
    if head:
        git.reset_to(head)
    return compact_mod.run(_store(), git, days=int(days), remote_head=head)


@feature(name="sync.history", group="sync", http=("GET", "/api/v1/sync/history"),
         idempotent=True, needs_auth=True, manages_lock=True)
def sync_history(limit: int = 30) -> dict:
    with _store().sync_lock:
        engine, git = _engine()
        if not git.initialized:
            git.clone(git.remote_url)
        return {"items": git.history(limit)}


@feature(name="sync.conflicts", group="sync", http=("GET", "/api/v1/sync/conflicts"),
         idempotent=True, needs_auth=True)
def sync_conflicts(include_resolved: bool = False, include_payload: bool = False) -> dict:
    """冲突清单：每条带**字段级差异**，界面只负责翻译 `label_key` / `state`。

    默认不再整份返回 local/remote/base 三个 payload —— 那等于让前端自己算差异
    （算出两套判断就是两套结果），而且一份任务详情里可能夹着用户备注全文，
    列表页并不需要。要看原文用 `include_payload=True`。
    """
    sql = "SELECT * FROM conflict" + ("" if include_resolved else " WHERE resolved_by IS NULL")
    rows = _store().db.execute(sql + " ORDER BY created_at DESC").fetchall()
    names = _name_map()
    items = []
    for r in rows:
        local, remote, base = _loads(r["local_json"]), _loads(r["remote_json"]), _loads(r["base_json"])
        kind = (local or remote or base or {}).get("kind") or "task"
        data_of = lambda p: (p or {}).get("data")
        entry = {"id": r["id"], "item_id": r["item_id"], "kind": kind, "reason": r["reason"],
                 "created_at": r["created_at"],
                 "title": _title_of(local) or _title_of(remote) or _title_of(base),
                 "local_deleted": bool((local or {}).get("deleted")),
                 "remote_deleted": bool((remote or {}).get("deleted")),
                 "fields": merge.diff_data(kind, data_of(local), data_of(remote),
                                            data_of(base), names)}
        if include_payload:
            entry.update({"local": local, "remote": remote, "base": base})
        items.append(entry)
    return {"total": len(items), "items": items}


def _loads(raw):
    import json
    return json.loads(raw) if raw else None


def _title_of(payload: dict | None) -> str | None:
    if not payload:
        return None
    data = payload.get("data") or {}
    return data.get("title") or data.get("name") or data.get("text")


def _name_map() -> dict[str, str]:
    """id → 名字，给引用类字段用（分组/文件夹/小节/父任务）。"""
    st = _store()
    out: dict[str, str] = {}
    for it in st.list_items(include_deleted=True):
        d = it.data
        out[it.id] = d.get("name") or d.get("title") or d.get("text") or ""
    return {k: v for k, v in out.items() if v}


#: 规范选择器；老键 `keep_deleted`/`restore_edit` 仍然接受，D1 那条链已经按它写测试。
RESOLVE_ALIASES = {"local": "local", "remote": "remote", "merge": "merge",
                   "restore_edit": "local", "keep_deleted": "remote"}


@feature(name="sync.conflict_resolve", group="sync",
         http=("POST", "/api/v1/sync/conflicts/{item_id}/resolve"), needs_auth=True)
def sync_conflict_resolve(item_id: str, choice: str = "remote",
                          picks: dict | None = None,
                          values: dict | None = None) -> dict:
    """冲突出口：保留本地 / 采用远端 / 手工合并（逐字段选边 + 可改写）。

    三条硬约束：
    1. **合并不能成为绕过校验的后门**。`picks` 的值只取自本地或远端其中一份（两份
       都已合法），`values` 是用户手打的值 —— 那必须走和 task.update 同一套校验，
       否则这个界面就成了"往库里塞非法字段"的唯一入口。
    2. 解决后一定写回**新 rev 并标 dirty**（除 remote 外），否则另一台机器永远看不到
       用户的裁决，下次同步还会再撞一遍。
    3. 合并的结果不能再是"已删除"：用户花了时间逐字段裁决，说明他要这条数据活着；
       想彻底丢弃就选 remote（远端是墓碑时即维持删除）。
    """
    if choice not in RESOLVE_ALIASES:
        raise validation_error("choice 只能是 local|remote|merge",
                               allowed=["local", "remote", "merge"], got=choice)
    kind_choice = RESOLVE_ALIASES[choice]
    st = _store()
    row = st.db.execute("SELECT * FROM conflict WHERE item_id=? AND resolved_by IS NULL "
                        "ORDER BY created_at DESC LIMIT 1", (item_id,)).fetchone()
    if row is None:
        raise not_found(f"{item_id} 的未决冲突")

    local = _loads(row["local_json"])
    remote = _loads(row["remote_json"])
    result: dict[str, Any] = {"item_id": item_id, "resolved_by": kind_choice, "choice": choice}

    if kind_choice == "local":
        result["item"] = _adopt(st, local, keep_deleted=False)
    elif kind_choice == "remote":
        # 合并时远端已经胜出并落库，这里只是结案；若本地才是当前值，则把远端那份取回来
        result["item"] = _adopt(st, remote, keep_deleted=True, only_if_missing=local)
    else:
        merged_data = _merge_payload(local, remote, picks or {}, values or {})
        result["item"] = _write_merged(st, item_id, merged_data)
        result["applied_fields"] = sorted((picks or {}) | (values or {}))

    st.db.execute("UPDATE conflict SET resolved_by=? WHERE id=?", (kind_choice, row["id"]))
    st.db.commit()
    return result


def _adopt(st, payload: dict | None, keep_deleted: bool,
           only_if_missing: dict | None = None) -> dict | None:
    """把某一份 payload 写成当前版本（新 rev + dirty）。空 payload = 这一侧整条不存在。"""
    from qingdan.core.models import Item
    if payload is None:
        if not keep_deleted:
            raise validation_error("这一侧没有内容可选", side="missing")
        return None
    old = Item.from_payload(payload)
    current = st.get(old.id)
    if only_if_missing is not None and current is not None and not current.deleted:
        return current.to_dict()                  # 远端已被采纳过，别重复发号
    st.put(Item(kind=old.kind, id=old.id, rev=st.next_rev(), data=old.data,
                deleted=bool(old.deleted) if keep_deleted else False, meta=old.meta),
           dirty=True)
    return st.get(old.id).to_dict()


def _merge_payload(local: dict | None, remote: dict | None, picks: dict,
                   values: dict) -> dict:
    """按字段拼出合并后的 data。`values` 覆盖 `picks`（用户最后改的就是最终答案）。"""
    base_data = dict((remote or local or {}).get("data") or {})
    l_data = (local or {}).get("data") or {}
    r_data = (remote or {}).get("data") or {}

    for field, side in (picks or {}).items():
        if side == "local":
            base_data[field] = l_data.get(field)
        elif side == "remote":
            base_data[field] = r_data.get(field)
        else:
            raise validation_error("picks 的值只能是 local 或 remote",
                                   field=field, got=side)
    for field, value in (values or {}).items():
        base_data[field] = value

    if not base_data.get("title") and not base_data.get("name") and not base_data.get("text"):
        raise validation_error("合并结果没有标题，不能保存")
    _validate_merged((local or remote or {}).get("kind") or "task", base_data)
    return base_data


def _write_merged(st, item_id: str, data: dict) -> dict:
    """把合并结果写成当前版本：新 rev、标 dirty、维持存活。

    以本地现存记录为底（`meta` 里有 creator_dev，被删过的项也要能救回来），
    所以就算冲突的另一侧整条不存在也不会丢创建者信息。
    """
    from qingdan.core.models import Item

    current = st.get(item_id)
    if current is None:
        raise not_found(f"{item_id} 在本机已不存在，请在另一台设备上处理")
    st.put(Item(kind=current.kind, id=current.id, rev=st.next_rev(), data=data,
                deleted=False, meta=current.meta), dirty=True)
    return st.get(item_id).to_dict()


def _validate_merged(kind: str, data: dict) -> None:
    """合并后的数据要和正常写入口一样严格 —— 冲突界面不是校验的豁免区。"""
    from qingdan.core import service
    from qingdan.core.org import _check_color
    from qingdan.core.view import parse_dt

    allowed = set(merge.KIND_FIELDS.get(kind, ()))
    unknown = {k for k in data if k not in allowed and k not in
               {"created_at", "due_all_day", "remind_mode", "alert_channels"}}
    if unknown:
        raise validation_error("不支持的字段", fields=sorted(unknown))
    if "priority" in data and data["priority"] is not None:
        data["priority"] = service._validate_priority(data["priority"])
    if data.get("status") and data["status"] not in service.VALID_STATUS:
        raise validation_error("status 非法", allowed=sorted(service.VALID_STATUS),
                               got=data["status"])
    if "color" in data:
        data["color"] = _check_color(data["color"])
    for date_field in ("due_at", "done_at", "target_date"):
        if data.get(date_field):
            iso = str(data[date_field]).replace(" ", "T")
            if parse_dt(iso) is None:
                raise validation_error(f"{date_field} 不是合法时间", got=data[date_field])
    for list_field in ("tags", "remind_at", "alert_channels"):
        if list_field in data and data[list_field] is not None and not isinstance(
                data[list_field], list):
            raise validation_error(f"{list_field} 必须是数组", got=data[list_field])
