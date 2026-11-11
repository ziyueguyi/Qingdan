"""Git 传输层。用本地裸仓库跑真实 git 操作，不依赖网络与 Gitee。"""
from __future__ import annotations

import ast
import os
import shutil
import tempfile
from pathlib import Path

import pytest
from dulwich import porcelain

from qingdan.core.gitrepo import (GitRepo, NotInitialized, PushRejected,
                                  redact)


@pytest.fixture
def remote():
    """一个裸仓库当远端。"""
    d = tempfile.mkdtemp(prefix="qd-git-")
    bare = os.path.join(d, "remote.git")
    porcelain.init(bare, bare=True)
    yield bare
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def work(tmp_path):
    return str(tmp_path / "repo")


def read_all(repo: GitRepo) -> dict[str, str]:
    return {k: v.decode() for k, v in repo.read_tree().items()}


# --------------------------------------------------------------------------- 空仓库


def test_clone_empty_remote_has_no_head(remote, work):
    r = GitRepo(work)
    r.clone(remote)
    assert r.initialized
    assert r.head() is None
    assert r.read_tree() == {}
    assert r.history() == []


def test_first_push_creates_branch(remote, work):
    """Gitee 刚建好的空仓库走的就是这条路：没有 HEAD 也能提交并推出分支。"""
    r = GitRepo(work)
    r.clone(remote)
    sha = r.commit_and_push({"manifest.json": b'{"product":"qingdan"}\n',
                             "items/task/A.json": b'{"id":"A"}\n'}, message="sync: init")
    assert sha
    assert read_all(r) == {"manifest.json": '{"product":"qingdan"}\n',
                           "items/task/A.json": '{"id":"A"}\n'}
    # 另一台机器 clone 得到同样的内容
    other = GitRepo(work + "-2")
    other.clone(remote)
    assert read_all(other) == read_all(r)


def test_uninitialized_operations_raise(work):
    r = GitRepo(work)
    with pytest.raises(NotInitialized):
        r.head()
    with pytest.raises(NotInitialized):
        r.fetch("file:///nope")


# --------------------------------------------------------------------------- 批量读


def test_read_tree_walks_nested_dirs_once(remote, work):
    r = GitRepo(work)
    r.clone(remote)
    files = {
        "manifest.json": "{}\n",
        "items/task/A.json": '{"a":1}\n',
        "items/task/B.json": '{"b":2}\n',
        "items/list/G.json": '{"g":3}\n',
        "items/countdown/C.json": '{"c":4}\n',
    }
    r.commit_and_push({k: v.encode() for k, v in files.items()}, message="many files")
    assert read_all(r) == files, "同步必须一次拿到全量文件，不能一个文件一次请求"


def test_read_tree_at_historical_commit(remote, work):
    r = GitRepo(work)
    r.clone(remote)
    old = r.commit_and_push({"items/task/A.json": b'{"v":1}\n'}, message="v1")
    r.commit_and_push({"items/task/A.json": b'{"v":2}\n'}, message="v2")
    assert read_all(r)["items/task/A.json"] == '{"v":2}\n'
    # 「从某天恢复」靠的就是读任意历史版本的 tree
    hist = r.history()
    assert len(hist) == 2 and hist[0]["message"] == "v2" and hist[1]["message"] == "v1"
    past = {k: v.decode() for k, v in r.read_tree(old).items()}
    assert past["items/task/A.json"] == '{"v":1}\n'


def test_remove_files_is_committed(remote, work):
    r = GitRepo(work)
    r.clone(remote)
    r.commit_and_push({"items/task/A.json": b"{}\n", "items/task/B.json": b"{}\n"}, message="two")
    r.commit_and_push({}, remove=["items/task/B.json"], message="drop B")
    assert set(read_all(r)) == {"items/task/A.json"}, "删除必须真的从仓库消失"


# --------------------------------------------------------------------------- fetch / 冲突


def test_fetch_sees_remote_advance(remote, work):
    a = GitRepo(work)
    a.clone(remote)
    a.commit_and_push({"items/task/A.json": b"{}\n"}, message="from A")

    b = GitRepo(work + "-b")
    b.clone(remote)
    b.commit_and_push({"items/task/B.json": b"{}\n"}, message="from B")

    head = a.fetch(remote)
    assert head is not None
    assert "items/task/B.json" in {k for k in a.read_tree(head)}


def test_push_after_remote_advance_raises_push_rejected(remote, work):
    """两台机器同时改：落后的一方直接推必须被拒，而不是静默覆盖远端。"""
    a = GitRepo(work)
    a.clone(remote)
    a.commit_and_push({"items/task/A.json": b"{}\n"}, message="init")

    b = GitRepo(work + "-b")
    b.clone(remote)                       # b 拿到 init

    a.commit_and_push({"items/task/X.json": b"{}\n"}, message="a advances first")

    with pytest.raises(PushRejected):     # b 现在落后，推不动
        b.commit_and_push({"items/task/Y.json": b"{}\n"}, message="b collides")


def test_recovery_after_rejection(remote, work):
    """被拒后的正确动作：fetch → 对齐 → 重新合并提交 → push 成功。"""
    a = GitRepo(work)
    a.clone(remote)
    a.commit_and_push({"items/task/A.json": b"{}\n"}, message="init")

    b = GitRepo(work + "-b")
    b.clone(remote)
    a.commit_and_push({"items/task/X.json": b"{}\n"}, message="a advances")

    try:
        b.commit_and_push({"items/task/Y.json": b"{}\n"}, message="b collides")
        pytest.fail("本应被拒")
    except PushRejected:
        pass

    # 真实引擎里合并结果来自 SQLite，不是工作区，所以 reset 后重新写入即可
    head = b.fetch(remote)
    b.reset_to(head)
    b.commit_and_push({"items/task/X.json": b"{}\n",       # 远端的
                       "items/task/Y.json": b"{}\n"},      # 我自己的
                      message="b merged on top", base_sha=head)

    check = GitRepo(work + "-check")
    check.clone(remote)
    assert set(read_all(check)) == {"items/task/A.json", "items/task/X.json", "items/task/Y.json"}


# --------------------------------------------------------------------------- 凭据安全


def test_redact_strips_credentials():
    url = "https://sxzhang1:tokENSECRETVALUE@gitee.com/sxzhang1/qd-data.git"
    out = redact(url)
    assert "tokENSECRETVALUE" not in out and "sxzhang1:" not in out
    assert out.endswith("gitee.com/sxzhang1/qd-data.git")


def test_redact_leaves_plain_url_alone():
    assert redact("https://gitee.com/a/b.git") == "https://gitee.com/a/b.git"


# --------------------------------------------------------------------------- 架构红线


@pytest.mark.parametrize("module", ["gitrepo.py", "sync.py"])
def test_sync_code_never_uses_pull(module):
    """`pull` 会让 git 做行级文本合并，把两个 JSON 合成非法内容，墓碑语义直接失效。

    spike 实测 dulwich 的 pull 在分叉时抛 DivergedBranches，但**不能指望它一直报错** ——
    能干净快进的场景它会照做。所以用源码断言把 `pull` 彻底禁掉。

    用 AST 找真实调用，不做子串匹配：文档里正当提到 `porcelain.pull` 不该被误判，
    否则这条守卫会在下一次改动时被当成噪音删掉。
    """
    p = Path(__file__).resolve().parents[1] / "src" / "qingdan" / "core" / module
    if not p.exists():
        pytest.skip(f"{module} 尚未创建")
    tree = ast.parse(p.read_text(encoding="utf-8"))
    called = {node.func.attr for node in ast.walk(tree)
              if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)}
    assert "pull" not in called, f"{module} 里出现了 pull() 调用，同步层只允许 fetch"


# --------------------------------------------------------------------------- 凭据不外泄到磁盘

TOKENISH = "abcdefgh0123456789"


def _mk_repo_with_credentialed_config(tmp_path, url: str, name: str = "repo"):
    """造一个"clone 完的样子"：.git/config 里写着带凭据的 remote URL。

    不能在测试里真去 clone `https://user:token@...`（没有网络，也不该有），
    而 dulwich 的 clone 就是这么写的 —— 所以直接摆出它的产物来验清理逻辑。
    目录名必须能参数化：第一版写死 "repo"，第二次调用把第一次的仓库盖掉了。
    """
    work = tmp_path / name
    (work / ".git").mkdir(parents=True)
    (work / ".git" / "config").write_text(
        "[core]\n\trepositoryformatversion = 0\n"
        '[remote "origin"]\n'
        f"\turl = {url}\n"
        "\tfetch = +refs/heads/*:refs/remotes/origin/*\n", encoding="utf-8")
    return work


def test_clone_scrubs_credentials_from_git_config(tmp_path):
    from qingdan.core.gitrepo import GitRepo

    work = _mk_repo_with_credentialed_config(
        tmp_path, f"https://purple_moon:{TOKENISH}@gitee.com/purple_moon/qd-data.git")
    git = GitRepo(work)
    git._scrub_credentials()

    text = (work / ".git" / "config").read_text(encoding="utf-8")
    assert TOKENISH not in text, "令牌还以明文留在 .git/config 里"
    assert "url = https://gitee.com/purple_moon/qd-data.git" in text, f"URL 被抹坏了：{text}"
    assert "fetch = +refs/heads" in text, "清理不能顺手改掉别的键"


def test_scrub_keeps_the_in_memory_url_for_fetch_and_push(tmp_path):
    """config 干净了，内存里那条必须还是完整的 —— 否则 fetch/push 直接没凭据可用。"""
    from qingdan.core.gitrepo import GitRepo

    url = f"https://purple_moon:{TOKENISH}@gitee.com/purple_moon/qd-data.git"
    work = _mk_repo_with_credentialed_config(tmp_path, url)
    git = GitRepo(work)
    git.remote_url = url
    git._scrub_credentials()
    assert git.remote_url == url, "self.remote_url 才是真正拿去连远端的那一条"


def test_scrub_is_idempotent_and_safe_on_clean_config(tmp_path):
    from qingdan.core.gitrepo import GitRepo

    url = f"https://purple_moon:{TOKENISH}@gitee.com/a/b.git"
    work = _mk_repo_with_credentialed_config(tmp_path, url, "dirty")
    git = GitRepo(work)
    git._scrub_credentials()
    once = (work / ".git" / "config").read_text(encoding="utf-8")
    git._scrub_credentials()
    assert (work / ".git" / "config").read_text(encoding="utf-8") == once, "重复执行要稳定"

    # 本来就没凭据：一个字都不该动
    clean = _mk_repo_with_credentialed_config(tmp_path, "https://gitee.com/a/b.git", "clean")
    before = (clean / ".git" / "config").read_text(encoding="utf-8")
    GitRepo(clean)._scrub_credentials()
    assert (clean / ".git" / "config").read_text(encoding="utf-8") == before

    # 目录压根不存在（还没 clone 过）：不能崩，也不能顺手造出东西来
    missing = tmp_path / "never-cloned"
    GitRepo(missing)._scrub_credentials()
    assert not missing.exists()


def test_local_file_url_is_left_alone(tmp_path):
    """本地裸仓库（file 路径）没有凭据段，清理不该动它 —— 测试环境全靠它。"""
    from dulwich import porcelain

    from qingdan.core.gitrepo import GitRepo
    bare = tmp_path / "remote.git"
    porcelain.init(str(bare), bare=True)
    work = tmp_path / "wc"
    git = GitRepo(work)
    git.clone(str(bare))

    cfg = (work / ".git" / "config").read_text(encoding="utf-8")
    assert "remote.git" in cfg, f"file 路径被改坏了：{cfg}"
    git._scrub_credentials()
    assert (work / ".git" / "config").read_text(encoding="utf-8") == cfg, "无凭据就不该重写文件"

    # 清完之后 fetch/push 用的还是内存里那条完整地址，同步链路照常
    git.fetch(str(bare))
    assert git.remote_url == str(bare)


def test_scrub_covers_every_git_file_that_records_urls(tmp_path):
    """扫描**整个 .git 目录**，不是只看 config。

    第一版只清了 .git/config 就宣布磁盘上没有令牌；真跑一遍才发现
    `.git/logs/refs/remotes/origin/master` 里还写着 `clone: from https://user:TOKEN@...`，
    FETCH_HEAD 同理。断言范围必须等于风险范围，否则"清干净"这三个字没资格说出口。
    """
    from qingdan.core.gitrepo import GitRepo

    tok = "tok-must-not-survive-anywhere"
    url = f"https://purple_moon:{tok}@gitee.com/purple_moon/qd-data.git"
    work = _mk_repo_with_credentialed_config(tmp_path, url, "wide")
    gitdir = work / ".git"
    logs = gitdir / "logs" / "refs" / "remotes" / "origin"
    logs.mkdir(parents=True)
    (logs / "master").write_text(
        f"0000 1111 me <me@x> 1700000000 +0000\tclone: from {url}\n", encoding="utf-8")
    (gitdir / "FETCH_HEAD").write_text(
        f"1111\t\tbranch {url} of gitee.com/purple_moon/qd-data.git\n", encoding="utf-8")

    GitRepo(work)._scrub_credentials()

    hits = [str(f.relative_to(gitdir)) for f in gitdir.rglob("*") if f.is_file()
            and tok in f.read_text(encoding="utf-8", errors="ignore")]
    assert hits == [], f"这些地方还留着明文令牌：{hits}"
    kept = (logs / "master").read_text(encoding="utf-8")
    assert "clone: from https://gitee.com/purple_moon/qd-data.git" in kept, \
        "只该去掉凭据段，reflog 记录本身要留着"


def test_scrub_never_rewrites_the_object_store(tmp_path):
    """清理范围限定在已知存 URL 的文本文件，压缩对象一律不碰。

    误伤 objects 就是仓库损坏，这个代价比"可能漏一处凭据"更严重。
    所以这里显式断言对象库没被动过 —— 而不是只相信"我只写了几个文件"。
    """
    from dulwich import porcelain

    from qingdan.core.gitrepo import GitRepo

    bare = tmp_path / "remote.git"
    porcelain.init(str(bare), bare=True)
    work = tmp_path / "wc"
    git = GitRepo(work)
    git.clone(str(bare))
    # 用产品自己的写入路径造内容，不去裸仓库里手工提交（裸仓没有工作区，
    # 第一版我还编了个 porcelain.commit(files=...) 的参数，接口根本不存在）
    git.commit_and_push({"items/task/A.json": b'{"schema":1}\n'},
                        message="seed", base_sha=None)
    obj_dir = work / ".git" / "objects"
    before = sorted((str(f), f.stat().st_size) for f in obj_dir.rglob("*") if f.is_file())
    assert before, "这个仓库里应该有对象，否则这条测试什么都没验"

    git._scrub_credentials()
    after = sorted((str(f), f.stat().st_size) for f in obj_dir.rglob("*") if f.is_file())
    assert before == after, "对象库被清理逻辑碰过 —— 这比漏清一处更严重"
    assert git.read_tree(git.head()), "清理之后仍要能正常读到已提交的内容"


def test_token_checker_script_detects_and_exonerates(tmp_path):
    """自查脚本必须**双向**验证过才能进 README。

    起因：我先后写过两版"检查磁盘有没有令牌"的 PowerShell，两版都在真有凭据的样本上
    输出空 —— 一版是中文注释被 PowerShell 5.1 按 GBK 解码、把正则字面量吃坏了，
    一版是相对路径没解析。假阴性的安全自查比没有自查更危险：它会让人以为磁盘是干净的。
    所以这条测试同时断言"脏样本要报 HIT"和"干净样本要报 CLEAN"。
    """
    if os.name != "nt":
        pytest.skip("自查脚本是给 Windows 用户的")
    import subprocess

    script = Path(__file__).resolve().parents[1] / "scripts" / "check_token.ps1"
    tok = "SECRETTOK-not-a-real-one"

    def build(root: Path, url: str) -> None:
        gitdir = root / "repo" / ".git" / "logs" / "refs" / "remotes" / "origin"
        gitdir.mkdir(parents=True)
        (root / "repo" / ".git" / "config").write_text(
            f'[remote "origin"]\n\turl = {url}\n', encoding="utf-8")
        (gitdir / "master").write_text(
            f"000 111 m <m@x> 1 +0000\tclone: from {url}\n", encoding="utf-8")

    dirty = tmp_path / "dirty"
    build(dirty, f"https://user:{tok}@gitee.com/a/b.git")
    clean = tmp_path / "clean"
    build(clean, "https://gitee.com/a/b.git")

    def run(target: Path) -> str:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-File", str(script), "-DataDir", str(target)],
            capture_output=True, text=True, timeout=120)
        return r.stdout + r.stderr

    out_dirty = run(dirty)
    assert "HIT" in out_dirty, f"脏样本没被查出来（假阴性）：{out_dirty}"
    assert tok not in out_dirty, "自查脚本自己不能把令牌打印出来"

    out_clean = run(clean)
    assert "CLEAN" in out_clean, f"干净样本应当无命中：{out_clean}"
