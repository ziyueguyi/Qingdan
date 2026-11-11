"""M4：鉴权、仓库就绪、私有哨兵，以及走真实 git 的端到端同步。

Gitee API 用 conftest 的假传输（测试不出网），但 **git 通道是真的** ——
用本地裸仓库跑完整 clone/fetch/push，这样端到端断言才有意义。
"""
from __future__ import annotations

import ast
import json
import os
import shutil
import tempfile
import time
from pathlib import Path

import pytest
from dulwich import porcelain

from qingdan.core import secrets
from qingdan.core.context import ctx
from qingdan.core.models import Item
from qingdan.core.registry import QdError, call


@pytest.fixture
def bare():
    d = tempfile.mkdtemp(prefix="qd-m4-")
    path = os.path.join(d, "remote.git")
    porcelain.init(path, bare=True)
    yield path
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def logged_in_only(app_ctx, fake_gitee):
    """只登录，不 ensure —— 测「首次建仓」「拒绝接管公开仓」必须从这个状态出发。"""
    call("auth.login", token="good-token")
    return app_ctx


@pytest.fixture
def wired(logged_in_only, bare):
    """已登录 + 仓库就绪 + git 远端指向本地裸仓库。"""
    call("repo.ensure", name="qd-data")
    conf = logged_in_only.store.state_get("repo")
    conf["remote_url"] = bare
    logged_in_only.store.state_set("repo", conf)
    return logged_in_only


# --------------------------------------------------------------------------- 令牌存储


def test_login_stores_dpapi_ciphertext(app_ctx):
    call("auth.login", token="abc123token")
    raw = app_ctx.store.secret_get("gitee_token")
    assert raw.startswith("dpapi:"), "令牌必须加密落库"
    assert "abc123token" not in raw
    assert secrets.unprotect(raw) == "abc123token"


def test_login_with_bad_token_stores_nothing(app_ctx, fake_gitee):
    with pytest.raises(QdError) as e:
        call("auth.login", token="bad-token")
    assert e.value.code == "TOKEN_INVALID"
    assert app_ctx.store.secret_get("gitee_token") is None, "坏令牌不能落库"
    assert app_ctx.authed is False


def test_login_applies_server_clock_offset(app_ctx, fake_gitee):
    """偏差要断言**具体值**。

    上一版这条只写 `isinstance(out["clock_offset_ms"], int)` —— 于是整条校准链后来
    静默失效（`_client()` 每次新建对象，事后读 `last_offset_ms` 永远是 0），
    测试却一直是绿的。弱断言是假测试：它让 bug 看起来有人守着。
    """
    fake_gitee.skew_ms = 300_000            # 服务器比本机快 5 分钟
    out = call("auth.login", token="good-token")
    assert 299_000 <= out["clock_offset_ms"] <= 301_000, out
    assert app_ctx.store.clock.offset_ms == out["clock_offset_ms"], "必须真的写进 HLC"


def _unwired_clients(src: Path) -> list[str]:
    """返回"没接上校准回调的 GiteeClient 构造"位置。只认 _client() 里的构造。"""
    offenders = []
    for py in sorted(src.rglob("*.py")):
        tree = ast.parse(py.read_text(encoding="utf-8"))
        factory_calls = set()
        for fn in ast.walk(tree):
            if isinstance(fn, ast.FunctionDef) and fn.name == "_client":
                factory_calls.update(id(n) for n in ast.walk(fn) if isinstance(n, ast.Call))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == "GiteeClient"):
                wired = any(kw.arg == "on_offset" for kw in node.keywords)
                if not (wired and id(node) in factory_calls):
                    offenders.append(f"{py.name}:{node.lineno}")
    return offenders


def test_no_unwired_gitee_client():
    """构造 GiteeClient 只能经 `sync_api._client()`。

    直接 new 出来的 client 少了 `on_offset`，不会报错，只会让 HLC 的服务器校准时钟
    静默失效 —— 登录路径原来就是这样漏的，而它是唯一必然发生的一次调用。
    这类"参数忘了接上"的 bug review 抓不住，就用 AST 禁掉。
    认的是**外层函数名**而不是行号：行号一漂，守卫要么误报要么白名单失效。
    """
    src = Path(__file__).resolve().parents[1] / "src" / "qingdan"
    offenders = _unwired_clients(src)
    assert not offenders, (
        f"这些 GiteeClient 构造不在 _client() 里或没带 on_offset：{offenders} —— "
        f"请改用 sync_api._client()，否则服务器时钟校准会静默失效")


def test_gitee_client_guard_actually_bites(tmp_path):
    """守卫不能是空转：漏接回调必须被抓到，工厂里正确写法不能误报。"""
    (tmp_path / "bad.py").write_text(
        "from qingdan.core.gitee import GiteeClient\n\n\n"
        "def login(token):\n    return GiteeClient(lambda: token)\n", encoding="utf-8")
    (tmp_path / "good.py").write_text(
        "from qingdan.core.gitee import GiteeClient\n\n\n"
        "def _client(token):\n    return GiteeClient(token, on_offset=apply)\n"
        "def apply(ms): return None\n", encoding="utf-8")
    found = _unwired_clients(tmp_path)
    assert len(found) == 1 and found[0].startswith("bad.py"), found


def test_offset_recalibrates_on_every_api_call(wired, fake_gitee):
    """校准不能只发生在登录那一次：机器时钟是会在用了几天之后漂的。

    另一半更关键 —— 偏差回到 0 时也要写回去。老代码写的是 `if offset:`，
    0 是假值，意味着"钟已经正了"这个事实永远应用不上，机器会一直带着旧偏差发号。
    """
    store = wired.store
    assert abs(store.clock.offset_ms) < 1000, "假服务器默认时钟健康"

    fake_gitee.skew_ms = -600_000           # 本机比服务器快 10 分钟
    call("repo.info")
    assert -601_000 <= store.clock.offset_ms <= -599_000, "任意一次 API 响应都该重新校准"

    st = call("sync.status")
    assert st["push_blocked_key"] == "clock_skew", "超限偏差要在状态里看得见，不该闷声推完"
    assert st["skew_limit_ms"] == 300_000

    fake_gitee.skew_ms = 0                  # 用户校正了系统时间
    call("repo.info")
    assert abs(store.clock.offset_ms) <= 1000, "归零也要写回 —— `if offset:` 会漏掉 0"
    assert call("sync.status")["push_blocked_key"] is None, "校好了就该自动恢复推送，不需要重启"


def test_credentialed_remote_url_is_stripped_from_the_database(logged_in_only, bare):
    """自建 git 的 `remote_url` 覆盖项里不许留凭据 —— 它存在 db.sqlite 里。

    触发点很实际：产品对用户的说法是"整个数据目录拷走就是备份"，
    所以任何写进 db.sqlite 的东西都会跟着备份包跑到 U 盘和另一台机器上。
    令牌的正确归宿是 DPAPI 密文（以及 Gitee 推送地址的现拼），不是配置表。
    """
    from qingdan.core.gitrepo import has_credentials
    from qingdan.core.sync_api import _engine

    tok = "tok-in-database-should-not-survive"
    logged_in_only.store.state_set("repo", {
        "owner": "tester", "name": "qd-data", "branch": "master",
        "remote_url": f"https://u:{tok}@git.example.invalid/a/b.git"})

    engine, git = _engine()
    assert tok in (git.remote_url or ""), "本次连接应照旧可用，别突然打断别人配好的自建 git"
    assert not has_credentials(str(logged_in_only.store.state_get("repo")["remote_url"])), \
        "落库的那份必须已剥掉凭据，否则下次备份包照旧带着令牌"
    # 清理这件事必须留下痕迹，不能悄悄改掉用户的配置
    logs = [r["detail"] for r in logged_in_only.store.alert_log_list(20)]
    assert "remote_url_credentials_stripped" in logs, logs


def test_stripped_gitee_url_still_connects_on_the_next_round(logged_in_only):
    """剥完凭据的下一轮，地址要由本机令牌补回来。

    这条是被**真 Gitee** 逼出来的：第一版清理写成"剥掉后继续用剥掉的地址"，
    而 `url or push_url(...)` 里剥过的地址是非空字符串，兜底永远走不到，
    于是第二次同步直接 HTTPUnauthorized。单元测试当时只断言"令牌没了"，
    没断言"还能同步" —— 弱断言让一个会把用户同步搞坏的改动看起来是有测试守着的。
    """
    from qingdan.core.sync_api import connect_url

    conf = {"owner": "tester", "name": "qd-data", "branch": "master",
            "remote_url": "https://gitee.com/tester/qd-data.git"}
    url = connect_url(conf)
    assert "good-token" in url, f"Gitee 地址必须由本机令牌补全，实际：{url}"
    assert "tester/qd-data.git" in url
    assert conf["remote_url"] == "https://gitee.com/tester/qd-data.git", "连接时不该反过来改配置"


def test_self_hosted_url_without_credentials_is_not_touched(logged_in_only):
    """自建 git 的无凭据地址原样使用 —— 不能把 Gitee 令牌塞进别人的服务器地址里。"""
    from qingdan.core.sync_api import connect_url

    conf = {"owner": "tester", "name": "qd-data",
            "remote_url": "file:///srv/git/qd-data.git"}
    assert connect_url(conf) == "file:///srv/git/qd-data.git"

    conf2 = {"owner": "tester", "name": "qd-data",
             "remote_url": "https://git.example.com/a/b.git"}
    assert connect_url(conf2) == "https://git.example.com/a/b.git"
    assert "good-token" not in connect_url(conf2)


def test_status_and_logout(app_ctx):
    call("auth.login", token="good-token")
    assert call("auth.status")["authed"] is True
    call("auth.logout")
    assert call("auth.status")["authed"] is False
    assert app_ctx.store.secret_get("gitee_token") is None


def test_logout_keeps_local_data_and_dirty_queue(app_ctx):
    """登出只清凭据：数据、待推送队列、远端仓库都不能被动。"""
    item = call("task.create", title="登出前建的")
    assert app_ctx.store.dirty_count() == 1
    call("auth.logout")
    assert app_ctx.store.get(item["id"]) is not None
    assert app_ctx.store.dirty_count() == 1, "登出不得清掉待推送改动"


def test_authed_survives_restart(tmp_path):
    """重启后仍应已登录 —— 登录态从本机凭据推导，不是只存在内存里。"""
    from qingdan.core import context
    c = context.init(tmp_path / "data", device_id="D-restart")
    call("auth.login", token="good-token")
    context.shutdown()

    context.init(tmp_path / "data", device_id="D-restart")
    assert context.ctx.authed is True
    assert call("auth.status")["authed"] is True
    context.shutdown()


# --------------------------------------------------------------------------- 仓库就绪


def test_repo_ensure_creates_private_when_missing(logged_in_only, fake_gitee):
    out = call("repo.ensure", name="qd-data")
    assert out["created"] is True
    assert out["private"] is True
    assert fake_gitee.repos["tester/qd-data"]["private"] is True
    conf = logged_in_only.store.state_get("repo")
    assert conf["owner"] == "tester" and conf["name"] == "qd-data"


def test_repo_ensure_is_idempotent(logged_in_only, fake_gitee):
    call("repo.ensure", name="qd-data")
    before = len([m for m, u in fake_gitee.calls if m == "POST"])
    second = call("repo.ensure", name="qd-data")
    after = len([m for m, u in fake_gitee.calls if m == "POST"])
    assert second["created"] is False, "已存在就不能再建一次"
    assert after == before, "第二次调用不该再发建仓请求"


def test_repo_ensure_refuses_public_repo(logged_in_only, fake_gitee):
    """同名仓库是公开的 → 拒绝接管，绝不把清单写进公网仓库。"""
    fake_gitee.repos["tester/qd-data"] = {"full_name": "tester/qd-data", "private": False,
                                          "public": True, "default_branch": "master"}
    with pytest.raises(QdError) as e:
        call("repo.ensure", name="qd-data")
    assert e.value.code == "REPO_NOT_PRIVATE"
    assert logged_in_only.store.state_get("repo") is None, "拒绝接管时不能留下半截配置"


def test_repo_info_redacts_push_url(wired):
    info = call("repo.info")
    assert "good-token" not in str(info)
    assert "***" in info["push_url"], "push_url 必须脱敏后才出接口"


# --------------------------------------------------------------------------- 私有哨兵


def test_sentinel_blocks_sync_when_repo_becomes_public(wired, fake_gitee):
    """R10 的核心：建仓时私有 ≠ 一直私有。变公开必须中止推送，而不是安静地把数据挂上网。"""
    call("task.create", title="不该被推出去的数据")
    fake_gitee.repos["tester/qd-data"]["private"] = False
    fake_gitee.repos["tester/qd-data"]["public"] = True

    with pytest.raises(QdError) as e:
        call("sync.run")
    assert e.value.code == "REPO_PUBLIC"

    # 远端确实没被写过
    repo_dir = os.path.join(os.path.dirname(wired.store.path), "repo")
    if os.path.isdir(repo_dir):
        g = porcelain.open_repo(repo_dir)
        tree = g[g[g.head()].tree]
        assert not any(b"qingdan" in e.path or e.path.startswith(b"items") for e in tree.items())
    log = wired.store.alert_log_list(10)
    assert any("哨兵中止" in x["detail"] for x in log), "中止必须留痕"


def test_sentinel_allows_private_repo(wired):
    call("task.create", title="正常同步的数据")
    out = call("sync.run")
    assert out["ok"] is True


# --------------------------------------------------------------------------- 端到端同步


def test_first_sync_creates_a_missing_remote_repo(logged_in_only, bare, fake_gitee):
    """新用户第一次点"立即同步"：远端仓还不存在，也得自己建出来再推。

    实测发现的断链：`repo.ensure` 只在"本地没有仓库配置"时才会被界面调用，
    而 `auth.login` 之后本地已经有 owner/name 了 —— 于是第一次同步必然
    REPO_NOT_FOUND，用户看到的就是"配好了却同步不上"。建仓是"要推之前必须成立的前提"，
    属于同步路径本身，不该指望界面记得调。
    """
    logged_in_only.store.state_set("repo", {
        "owner": "tester", "name": "qd-data", "remote_url": bare, "branch": "master"})
    assert fake_gitee.repos == {}, "前提是云端确实还没有仓库"

    call("task.create", title="开机第一条")
    report = call("sync.run")
    assert report["ok"] is True, f"首次同步必须自己把仓建出来：{report}"
    assert report["pushed"] == 1

    info = fake_gitee.repos.get("tester/qd-data")
    assert info is not None, "缺的仓库要被补建"
    assert info["private"] is True, "自动建的一律私有，没有例外"


def test_public_repo_is_never_auto_created_over(app_ctx, fake_gitee):
    """哨兵优先于便利：变公开的仓不能被"顺手重建"绕过去。"""
    call("auth.login", token="good-token")
    fake_gitee.repos["tester/qd-data"] = {"full_name": "tester/qd-data",
                                          "private": False, "public": True,
                                          "default_branch": "master"}
    app_ctx.store.state_set("repo", {"owner": "tester", "name": "qd-data",
                                     "remote_url": "git@example.invalid:x.git"})
    call("task.create", title="不该推出去")
    with pytest.raises(QdError) as e:
        call("sync.run")
    assert e.value.code == "REPO_PUBLIC", "公开仓要中止，而不是再建一个同名的完事"


def test_every_pushing_sync_feature_goes_through_prepare_remote():
    """凡是会把改动推出去的功能，都必须走 `prepare_remote()`。

    只测一条路径不够：`sync.run` / `sync.push` / `sync.compact` 三个入口都能推，
    漏一个就还是"从那条路进来就建仓、从这条路进来就 404"。
    """
    import inspect

    import qingdan.core.sync_api as sa

    for name in ("sync_run", "sync_push", "sync_compact"):
        body = inspect.getsource(getattr(sa, name))
        assert "prepare_remote()" in body, f"{name} 没有走建仓+哨兵统一入口"
        assert "assert_repo_private()" not in body, f"{name} 绕过了首次建仓"


def test_sync_run_pushes_items_to_remote(wired, bare):
    for i in range(3):
        call("task.create", title=f"任务{i}")
    report = call("sync.run")
    assert report["pushed"] == 3

    # 用另一个工作副本直接读远端，确认真的数据进去了
    check = tempfile.mkdtemp()
    target = os.path.join(check, "c")
    porcelain.clone(bare, target)
    g = porcelain.open_repo(target)
    tree_files = []

    def walk(t, prefix=""):
        for e in t.items():
            p = f"{prefix}{e.path.decode()}"
            obj = g[e.sha]
            if obj.type_name == b"tree":
                walk(obj, p + "/")
            else:
                tree_files.append(p)
    walk(g[g[g.head()].tree])
    assert sum(1 for p in tree_files if p.startswith("items/task/")) == 3
    shutil.rmtree(check, ignore_errors=True)


def test_sync_pull_brings_remote_items_in(wired, bare):
    """A 推上去，B 拉下来 —— 两台机器真的能互通。"""
    from qingdan.core import context
    call("task.create", title="A 的任务")
    call("sync.run")

    # 换一套库当第二台机器，指向同一个裸仓库
    second = context.init(os.path.join(os.path.dirname(wired.store.path), "..", "machine2"),
                          device_id="D-two")
    call("auth.login", token="good-token")
    second.store.state_set("repo", {"owner": "tester", "name": "qd-data",
                                    "branch": "master", "remote_url": bare})
    out = call("sync.pull")
    assert out["pulled"] >= 1
    assert "A 的任务" in {t["data"]["title"] for t in call("task.list")["items"]}
    context.shutdown()
    ctx.data_dir = wired.data_dir
    ctx.store = wired.store
    ctx.authed = True


def test_sync_status_and_history(wired):
    call("task.create", title="看状态")
    call("sync.run")
    st = call("sync.status")
    assert st["pending"] == 0 and st["base_commit"]
    hist = call("sync.history")["items"]
    assert hist and hist[0]["message"].startswith("sync:")


def test_sync_compact_endpoint_now_registered(wired):
    """M5 时逻辑做完但没注册接口（没有仓库上下文）；M4 之后接上一行装饰器就成立。"""
    item = call("task.create", title="要被回收的")
    call("sync.run")
    call("task.delete", item_id=item["id"])
    call("sync.run")

    # 回收窗口最小 7 天（生产里 0 天太危险），所以这里把墓碑时间往前推，
    # 模拟"90 天后才来做回收"这个真实场景
    wired.store.db.execute(
        "UPDATE item SET deleted_at_ms = ? WHERE id = ?",
        (int(time.time() * 1000) - 100 * 86400_000, item["id"]))
    wired.store.db.commit()

    out = call("sync.compact", days=90)
    assert out["removed"] == 1
    assert "graveyard.json" in os.listdir(os.path.join(os.path.dirname(wired.store.path), "repo"))


def test_conflict_resolve_restore_edit(wired):
    """D1 的出口：被拦下的编辑，用户选择恢复时真的能恢复。"""
    from qingdan.core.models import Item
    item = call("task.create", title="原始")
    st = wired.store
    old = st.get(item["id"])
    st.record_conflict(item["id"], "resurrect_after_compaction",
                       local=Item(kind="task", id=item["id"], rev=st.next_rev(),
                                  data={"title": "离线改过的"}, meta={}),
                       remote=None, base=old)

    out = call("sync.conflicts")
    assert out["total"] == 1
    call("sync.conflict_resolve", item_id=item["id"], choice="restore_edit")
    assert st.get(item["id"]).data["title"] == "离线改过的"
    assert st.get(item["id"]).deleted is False
    assert call("sync.conflicts")["total"] == 0, "已裁决的冲突不该继续挂着"


def test_conflict_resolve_keep_deleted(wired):
    item = call("task.create", title="原始")
    st = wired.store
    st.record_conflict(item["id"], "resurrect_after_compaction",
                       local=Item(kind="task", id=item["id"], rev=st.next_rev(),
                                  data={"title": "离线改过的"}, meta={}),
                       remote=None, base=st.get(item["id"]))
    call("sync.conflict_resolve", item_id=item["id"], choice="keep_deleted")
    assert st.get(item["id"]).data["title"] == "原始", "选择维持删除就不能改动内容"


def test_conflict_resolve_rejects_bad_choice(wired):
    item = call("task.create", title="x")
    with pytest.raises(QdError):
        call("sync.conflict_resolve", item_id=item["id"], choice="whatever")


# --------------------------------------------------------------------------- 冲突界面后端


def _conflict(wired, title="周报", local=None, remote=None):
    """造一条"两边都改了同一项"的冲突记录，走真实 record_conflict。"""
    from qingdan.core.models import Item
    item = call("task.create", title=title)
    st = wired.store
    base = st.get(item["id"])
    ldata = {**base.data, **(local or {})}
    rdata = {**base.data, **(remote or {})}
    st.record_conflict(item["id"], "both_changed",
                       local=Item(kind="task", id=item["id"], rev=st.next_rev(), data=ldata),
                       remote=Item(kind="task", id=item["id"], rev=st.next_rev(), data=rdata),
                       base=base)
    return item["id"]


def test_conflict_list_reports_field_diff_not_raw_payloads(wired):
    """差异必须在后端算：前端再实现一遍就会有两套判断（视图层那次同类 bug 已吃过）。"""
    _conflict(wired, local={"title": "本地改的周报", "priority": 1},
              remote={"title": "远端改的周报", "due_at": "2026-12-20T10:00:00"})
    out = call("sync.conflicts")
    assert out["total"] == 1
    row = out["items"][0]

    assert row["title"] in ("本地改的周报", "远端改的周报"), "列表要能认出是哪一条"
    assert "local" not in row, "默认不整份回吐 payload"
    by_key = {f["key"]: f for f in row["fields"]}
    assert set(by_key) == {"title", "priority", "due_at"}, f"只该列出有差异的字段：{list(by_key)}"
    assert by_key["priority"]["state"] == "local_only"
    assert by_key["due_at"]["state"] == "remote_only"
    assert by_key["title"]["state"] == "both_diff"
    for f in row["fields"]:
        assert f["label_key"], f
        assert f["type"], f


def test_conflict_diff_resolves_reference_ids_into_names(wired):
    """引用字段存的是 ULID，直接显示给用户是一串乱码 —— 必须换成名字（用户数据，不翻译）。"""
    g1 = call("group.create", name="工作任务")["id"]
    g2 = call("group.create", name="学习安排")["id"]
    _conflict(wired, local={"list_id": g1}, remote={"list_id": g2})
    row = call("sync.conflicts")["items"][0]
    ref = [f for f in row["fields"] if f["key"] == "list_id"][0]
    assert ref["type"] == "ref"
    assert ref["local"] == "工作任务" and ref["remote"] == "学习安排", ref
    assert ref["local_id"] == g1 and ref["remote_id"] == g2, "提交合并还得拿回真 id"


def test_conflict_diff_ignores_tag_order(wired):
    """tags 只是顺序不同不算冲突，否则用户会被假差异骚扰。"""
    _conflict(wired, local={"tags": ["重要", "急"]}, remote={"tags": ["急", "重要"]})
    row = call("sync.conflicts")["items"][0]
    assert row["fields"] == [], "顺序差异不该出现在冲突界面里"


def test_resolve_local_keeps_local_content_and_marks_dirty(wired):
    tid = _conflict(wired, local={"title": "本地版"}, remote={"title": "远端版"})
    out = call("sync.conflict_resolve", item_id=tid, choice="local")
    assert call("task.get", item_id=tid)["data"]["title"] == "本地版"
    assert out["resolved_by"] == "local"
    assert wired.store.dirty_count() >= 1, "裁决完必须待推送，否则另一台永远看不到这个决定"


def test_resolve_remote_does_not_reissue_a_revision(wired):
    """远端已经胜出并落库时，采用远端只是结案，不能凭空再发一个版本号。"""
    from qingdan.core.models import Item
    item = call("task.create", title="远端已生效")["id"]
    st = wired.store
    cur = st.get(item)
    st.record_conflict(item, "both_changed",
                       local=Item(kind="task", id=item, rev=st.next_rev(),
                                  data={**cur.data, "title": "本地被压下去的版本"}),
                       remote=cur, base=cur)
    rev_before = st.get(item).rev
    call("sync.conflict_resolve", item_id=item, choice="remote")
    assert st.get(item).rev == rev_before, "采用远端不该重新发号"
    assert call("sync.conflicts")["total"] == 0


def test_resolve_merge_by_picking_sides_field_by_field(wired):
    tid = _conflict(wired, local={"title": "要这个标题", "priority": 1},
                    remote={"title": "远端标题", "due_at": "2026-12-20T10:00:00"})
    out = call("sync.conflict_resolve", item_id=tid, choice="merge",
               picks={"title": "local", "priority": "remote", "due_at": "remote"})
    data = call("task.get", item_id=tid)["data"]
    assert data["title"] == "要这个标题"
    assert data.get("priority", 4) == 4, "priority 选了远端（没设过）就该回到无"
    assert data["due_at"] == "2026-12-20T10:00:00"
    assert out["resolved_by"] == "merge"
    assert sorted(out["applied_fields"]) == ["due_at", "priority", "title"]


def test_resolve_merge_can_type_a_value_but_must_pass_the_same_validation(wired):
    """手打值要走和 task.update 一样的校验 —— 冲突界面不是校验的豁免区。"""
    tid = _conflict(wired, local={"title": "A"}, remote={"title": "B"})
    call("sync.conflict_resolve", item_id=tid, choice="merge",
         values={"title": "我自己写的标题", "priority": 2})
    data = call("task.get", item_id=tid)["data"]
    assert data["title"] == "我自己写的标题" and data["priority"] == 2

    tid2 = _conflict(wired, local={"title": "A"}, remote={"title": "B"})
    with pytest.raises(QdError) as e:
        call("sync.conflict_resolve", item_id=tid2, choice="merge", values={"priority": 9})
    assert e.value.code == "VALIDATION_ERROR"

    tid3 = _conflict(wired, local={"color": "#ff8800"}, remote={"color": "#00aaff"})
    with pytest.raises(QdError):
        call("sync.conflict_resolve", item_id=tid3, choice="merge", values={"color": "red"})

    tid4 = _conflict(wired, local={"due_at": "2026-12-01T09:00:00"},
                     remote={"due_at": "2026-12-02T09:00:00"})
    with pytest.raises(QdError):
        call("sync.conflict_resolve", item_id=tid4, choice="merge",
             values={"due_at": "下周三下午"})


def test_resolve_merge_rejects_unknown_and_empty(wired):
    tid = _conflict(wired, local={"title": "A"}, remote={"title": "B"})
    with pytest.raises(QdError):
        call("sync.conflict_resolve", item_id=tid, choice="merge",
             values={"is_deleted": True})
    tid2 = _conflict(wired, local={"title": "A"}, remote={"title": "B"})
    with pytest.raises(QdError):
        call("sync.conflict_resolve", item_id=tid2, choice="merge", values={"title": ""})


def test_resolve_merge_bad_pick_side(wired):
    tid = _conflict(wired, local={"title": "A"}, remote={"title": "B"})
    with pytest.raises(QdError) as e:
        call("sync.conflict_resolve", item_id=tid, choice="merge", picks={"title": "both"})
    assert e.value.code == "VALIDATION_ERROR"


# --------------------------------------------------------------------------- 令牌不外泄


def test_token_never_appears_in_any_endpoint_output(wired):
    """令牌出现在任何接口响应里都是事故。逐个功能扫一遍，而不是只测已知会返回它的那两个。"""
    call("task.create", title="扫一遍")
    call("sync.run")
    for name in sorted(k for k in _readable() if not k.startswith(("auth.login", "repo.ensure"))):
        try:
            out = call(name)
        except QdError:
            continue
        assert "good-token" not in str(out), f"{name} 的返回值里出现了令牌"


def _readable() -> list[str]:
    from qingdan.core.registry import REGISTRY
    return [n for n, f in REGISTRY.items() if f.idempotent and not f.needs_auth or n in (
        "auth.status", "repo.info", "sync.status", "sync.history", "sync.conflicts",
        "task.list", "app.health", "app.info", "alert.status", "alert.log", "view.counts")]
