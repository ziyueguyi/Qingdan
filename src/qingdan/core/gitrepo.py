"""Git 传输层（M5）。把 dulwich 包成同步引擎需要的四个动作。

**只用 `fetch`，代码里禁止出现 `pull`**（`tests/test_sync.py` 有断言守着）。
原因来自 M5 前置 spike 的实测：`porcelain.pull` 在分叉时抛 `DivergedBranches`
拒绝自动合并 —— 而我们本来就不要 git 的行级文本合并：清单的合并是**应用层按 item
的三方合并**（LWW + 墓碑），让 git 去合并两个 JSON 只会产出非法内容。

凭据安全：HTTPS 远端地址里带私人令牌，**任何异常信息、日志、返回值都不得回显完整 URL**。
"""
from __future__ import annotations

import os
import re
import shutil
from pathlib import Path
from typing import Iterable

from dulwich import porcelain
from dulwich.repo import Repo

#: 我们自己的异常类型，避免上层直接依赖 dulwich 的异常
class GitError(RuntimeError):
    pass


class NotInitialized(GitError):
    pass


class PushRejected(GitError):
    """非快进：远端在我们 fetch 之后又前进了，调用方应重新 fetch + 合并再来一轮。"""

    def __init__(self, local_sha: bytes, remote_sha: bytes):
        super().__init__("远端分支已前进，需要重新合并后再推")
        self.local_sha = local_sha
        self.remote_sha = remote_sha


def _short(sha) -> str:
    if isinstance(sha, bytes):
        return sha.hex()[:8] if len(sha) == 20 else sha.decode("ascii", "replace")[:8]
    return str(getattr(sha, "hex", sha))[:8]


#: URL 里的凭据段（`scheme://user:pass@`）。日志与 reflog 用同一个模式清理。
CRED_RE = re.compile(r"://[^/@\s]+@")


def strip_credentials(url: str) -> str:
    """去掉 URL 里的 `scheme://user:pass@` 凭据段。

    三处要用，而且必须共用 `CRED_RE` —— 之前 config 用 bytes 版、URL 用 str 版、
    数据库覆盖项又写了一版，同一个概念三份正则，改一处漏两处（今天漏的就是这么来的）：
    `.git/config`（clone 会写）、`.git/logs` 与 FETCH_HEAD（fetch/push 会写）、
    以及 `repo` 配置里的 `remote_url` 覆盖项（存进 db.sqlite 的 settings 表，
    而产品对用户的说法是"整个数据目录拷走就是备份"）。
    """
    return CRED_RE.sub("://", url) if isinstance(url, str) else url


def has_credentials(url: str) -> bool:
    return isinstance(url, str) and bool(CRED_RE.search(url))


def redact(url: str) -> str:
    """去掉 URL 里的凭据，用于日志与异常信息。"""
    if "@" not in url:
        return url
    scheme, _, rest = url.partition("://")
    _creds, _, host = rest.partition("@")
    return f"{scheme}://***@{host}"


class GitRepo:
    """一个本地工作副本 + 它的远端。工作目录与 SQLite 同层级，程序目录只读。"""

    def __init__(self, work_dir: str | os.PathLike, branch: str = "master"):
        self.work_dir = Path(work_dir)
        self.branch = branch.encode()
        self.remote_url: str | None = None

    # -- 生命周期 ----------------------------------------------------------
    @property
    def initialized(self) -> bool:
        return (self.work_dir / ".git").is_dir()

    def clone(self, remote_url: str) -> None:
        """clone 远端。远端是空仓库（0 commit）时 dulwich 也能 clone成功，
        但不会有 HEAD —— 所有读操作都要能容忍这一点。"""
        self.remote_url = remote_url
        if self.initialized:
            shutil.rmtree(self.work_dir, ignore_errors=True)
        self.work_dir.parent.mkdir(parents=True, exist_ok=True)
        try:
            porcelain.clone(remote_url, str(self.work_dir))
        except Exception as e:                      # 不把 URL 带进异常信息
            raise GitError(f"clone 失败：{type(e).__name__}: {e}") from e
        self._scrub_credentials()

    def scrub_credentials_after_use(self) -> None:
        """同步收尾用：把这一轮 git 操作可能写下的凭据全部清掉。公开入口，理由见 _scrub_credentials。"""
        self._scrub_credentials()

    def _scrub_credentials(self) -> None:
        """抹掉 `.git` 里所有记录 remote URL 的凭据。令牌只该活在 DPAPI 密文里。

        为什么必须做：`porcelain.clone(url)` 会原样把 `https://user:TOKEN@gitee.com/...`
        写进 `.git/config` —— 那是个**明文文件**。而产品的说法是"整个数据目录拷走就是备份"，
        于是每一次同步都在教用户把令牌复制到 U 盘、备份盘、别人的机器上。

        为什么不能只清 config：真跑一遍才发现 `.git/logs/refs/remotes/origin/master`
        里写着 `clone: from https://user:TOKEN@gitee.com/...`，FETCH_HEAD 同理。
        我第一版只查了 config 就宣布"磁盘上没有令牌"，是过度推广 —— 现在按位置逐个清，
        并由测试扫描整个 `.git` 目录来兜底。

        内存里 `self.remote_url` 仍保留完整地址（fetch/push 每次都显式传它），
        所以清理不影响任何操作。清理范围**限定在已知会存 URL 的文本文件**：
        遍历重写整个 .git 一旦误伤压缩对象就是仓库损坏，这个风险不该由清理逻辑去换。
        """
        git_dir = self.work_dir / ".git"
        if not git_dir.is_dir():
            return

        cfg_path = git_dir / "config"
        if cfg_path.is_file():
            try:
                from dulwich.config import ConfigFile

                cfg = ConfigFile.from_path(str(cfg_path))
                changed = False
                for section in cfg.sections():
                    if section[0] != b"remote":
                        continue
                    try:
                        url = cfg.get(section, b"url")
                    except KeyError:
                        continue
                    clean = strip_credentials(url.decode("utf-8")).encode("utf-8")
                    if clean != url:
                        cfg.set(section, b"url", clean)
                        changed = True
                if changed:
                    cfg.write_to_path(str(cfg_path))
            except Exception:
                # 抹不干净也不能让同步失败——但绝不能因此静默：这条路径的意义就是防泄露
                print("[git] 警告：未能清除 .git/config 中的凭据，令牌可能以明文留在磁盘", flush=True)

        targets = [git_dir / "FETCH_HEAD"]
        logs = git_dir / "logs"
        if logs.is_dir():
            targets += [f for f in logs.rglob("*") if f.is_file()]
        for f in targets:
            if not f.is_file() or f.stat().st_size > 1_000_000:
                continue
            try:
                text = f.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            cleaned = CRED_RE.sub("://", text)
            if cleaned != text:
                try:
                    f.write_text(cleaned, encoding="utf-8")
                except OSError:
                    print(f"[git] 警告：{f.name} 里的凭据未能清除", flush=True)

    def _repo(self) -> Repo:
        if not self.initialized:
            raise NotInitialized("本地仓库尚未初始化，先 clone")
        return Repo(str(self.work_dir))

    def clone_ok(self, remote_url: str) -> bool:
        """能 clone 就 clone，返回是否可用（已初始化也算可用）。

        repo.ensure 用它判断"要不要推初始提交"，clone 失败必须能被发现而不是抛穿。
        """
        if self.initialized:
            self.remote_url = remote_url
            return True
        try:
            self.clone(remote_url)
            return True
        except GitError:
            return False

    # -- 读 ----------------------------------------------------------------
    def head(self) -> bytes | None:
        """本地分支 head；空仓库返回 None。"""
        with self._repo() as repo:
            try:
                return repo.refs[b"refs/heads/" + self.branch]
            except (KeyError, ValueError):
                return None

    def fetch(self, remote_url: str | None = None) -> bytes | None:
        """拉远端，返回远端分支 head（空仓库为 None）。**这就是我们唯一的"拉"动作。**

        注意：fetch **不会移动本地分支** —— 这是刻意的（我们不用 pull，合并发生在应用层）。
        所以读远端内容必须用本方法的返回值，不能用 `head()`；后者是本地分支位置。
        需要"当前远端在哪"时用 `remote_head()`。
        """
        url = remote_url or self.remote_url
        if not url:
            raise NotInitialized("没有配置远端地址")
        self.remote_url = url
        with self._repo() as repo:
            try:
                result = porcelain.fetch(str(self.work_dir), url)
            except Exception as e:
                raise GitError(f"fetch 失败：{type(e).__name__}: {e}") from e
        refs = getattr(result, "refs", None) or {}
        for key in (b"refs/heads/" + self.branch, self.branch):
            if key in refs:
                self._last_remote_head = refs[key]
                return refs[key]
        with self._repo() as repo:
            try:
                got = repo.refs[b"refs/remotes/origin/" + self.branch]
            except (KeyError, ValueError):
                return None
            self._last_remote_head = got
            return got

    def remote_head(self) -> bytes | None:
        """最近一次 fetch 到的远端 head（没 fetch 过则为 None）。"""
        return getattr(self, "_last_remote_head", None)

    def read_tree(self, sha: bytes | None = None) -> dict[str, bytes]:
        """一次调用拿到该版本下**全部**文件内容。

        同步必须批量读：一个文件一次请求，5000 条任务就是一次同步打 5000 个请求。
        """
        with self._repo() as repo:
            if sha is None:
                try:
                    sha = repo.refs[b"refs/heads/" + self.branch]
                except (KeyError, ValueError):
                    return {}
            commit = repo[sha]
            out: dict[str, bytes] = {}

            def walk(tree, prefix: str = "") -> None:
                # dulwich 1.x：Tree.items() 返回 TreeEntry(path, mode, sha) 三元组
                for entry in sorted(tree.items(), key=lambda e: e.path):
                    path = f"{prefix}{entry.path.decode()}"
                    obj = repo[entry.sha]
                    if obj.type_name == b"tree":
                        walk(obj, path + "/")
                    else:
                        out[path] = obj.data

            walk(repo[commit.tree])
            return out

    def history(self, limit: int = 50) -> list[dict]:
        """提交历史，供「从某天恢复」与排障使用。"""
        with self._repo() as repo:
            try:
                sha = repo.refs[b"refs/heads/" + self.branch]
            except (KeyError, ValueError):
                return []
            out = []
            seen = 0
            while sha and seen < limit:
                c = repo[sha]
                out.append({"sha": _short(sha), "message": c.message.decode("utf-8", "replace"),
                            "author": c.author.decode("utf-8", "replace"),
                            "timestamp": int(c.commit_time)})
                # dulwich 1.x：属性名是 parents（不是 parent）
                sha = c.parents[0] if c.parents else None
                seen += 1
            return out

    # -- 写 ----------------------------------------------------------------
    def commit_and_push(self, files: dict[str, bytes], remove: Iterable[str] = (),
                        message: str = "sync", base_sha: bytes | None = None) -> bytes:
        """把合并结果写成一次提交并推送。

        `base_sha` 是本轮 fetch 到的远端 head：先把本地分支对齐到它，我们的提交
        叠在其上，push 就是快进。若期间远端又前进 → 抛 `PushRejected`，调用方重来。
        """
        with self._repo() as repo:
            target = base_sha if base_sha is not None else self.head()
            if target is not None:
                repo.refs[b"refs/heads/" + self.branch] = target

        for rel, content in files.items():
            fp = self.work_dir / rel
            fp.parent.mkdir(parents=True, exist_ok=True)
            fp.write_bytes(content)

        # 只删「当前 tree 里确实存在」的路径，并且交给 porcelain.rm 去删磁盘文件。
        # 自己先 unlink 再 rm 的话，dulwich 会因为文件已不在磁盘上而报
        # "did not match any files"（实测踩过）。
        tracked = set(self.read_tree(self.head()))
        gone = [rel for rel in remove if rel in tracked]
        present = [p for p in files if (self.work_dir / p).exists()]

        if not present and not gone:
            return self.head() or b""
        try:
            if present:
                porcelain.add(str(self.work_dir), paths=present)
            if gone:
                porcelain.rm(str(self.work_dir), paths=gone)
            sha = porcelain.commit(
                str(self.work_dir), message=message.encode("utf-8"),
                author=b"qingdan <qingdan@local>", committer=b"qingdan <qingdan@local>")
        except Exception as e:
            raise GitError(f"commit 失败：{type(e).__name__}: {e}") from e

        try:
            porcelain.push(str(self.work_dir), self.remote_url, b"refs/heads/" + self.branch)
        except Exception as e:
            from dulwich.porcelain import DivergedBranches
            if isinstance(e, DivergedBranches):
                raise PushRejected(e.args[0], e.args[1]) from e
            raise GitError(f"push 失败：{type(e).__name__}: {e}") from e
        return sha if isinstance(sha, bytes) else bytes.fromhex(str(sha))

    def reset_to(self, sha: bytes, clean_worktree: bool = True) -> None:
        """把本地分支与工作区强制对齐到某个版本（拉取远端结果、或放弃本地脏改动）。"""
        with self._repo() as repo:
            repo.refs[b"refs/heads/" + self.branch] = sha
        if clean_worktree:
            try:
                porcelain.checkout(str(self.work_dir), branch=self.branch.decode())
            except Exception:
                # 工作区重置失败不致命：下一次 commit 会覆盖
                pass
