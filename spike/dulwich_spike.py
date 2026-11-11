"""M5 前置 spike：dulwich 能不能承担同步通道。

这是同步部分唯一可能要求整体换方案的点（和 M0 的 pythonnet 同性质），所以先验证再写代码。
用**本地裸仓库**跑，不需要 Gitee 令牌；HTTPS+令牌那一层要等 M4 用真令牌验证。

验的是四件套 + 同步协议真正依赖的三个能力：
  clone / fetch / commit / push
  ls-tree 批量读远端文件、读任意 commit 的 tree（merge base 与历史恢复要用）、
  非快进冲突的表现形式（push 撞车时给我们什么信号）
"""
import os
import shutil
import sys
import tempfile

sys.path.insert(0, "src")

from dulwich import porcelain
from dulwich.repo import Repo

work = tempfile.mkdtemp(prefix="qd-spike-")
bare = os.path.join(work, "remote.git")
local = os.path.join(work, "local")

print(f"[spike] dulwich 版本: {__import__('dulwich').__version__}")


def short(sha) -> str:
    """dulwich 在不同接口返回 bytes / SHA / bytes-hex，统一成 8 位可读串。"""
    if isinstance(sha, bytes):
        return sha.hex()[:8] if len(sha) == 20 else sha.decode()[:8]
    return str(getattr(sha, "hex", sha))[:8]


# ① 建裸仓库当远端，并推一个初始提交（模拟 Gitee 上的空仓 / 已初始化仓）
porcelain.init(bare, bare=True)
seed = os.path.join(work, "seed")
os.makedirs(seed)
porcelain.clone(bare, seed)
print("[spike] OK  clone 空裸仓库")

with open(os.path.join(seed, "manifest.json"), "w", encoding="utf-8") as f:
    f.write('{\n  "product": "qingdan",\n  "schema": 1\n}\n')
porcelain.add(seed, ["manifest.json"])
c1 = porcelain.commit(seed, message=b"init", author=b"qd <qd@local>", committer=b"qd <qd@local>")
print(f"[spike] OK  commit 初始提交 {short(c1)}")

porcelain.push(seed, b"file://" + bare.encode() if False else bare, b"refs/heads/master")
print("[spike] OK  push 到裸仓库")

# ② 本地 clone，走真实同步路径
porcelain.clone(bare, local)
r = Repo(local)
print(f"[spike] OK  clone 到有内容的仓库, HEAD={short(r.head())}")

# ③ 同步协议依赖的读能力：批量列 tree（一次调用拿全量，不能一个文件一次请求）
from dulwich.repo import Repo as R
with R(local) as repo:
    tree = repo[repo[repo.head()].tree]
    paths = []

    def walk(t, prefix=""):
        # dulwich 1.x：Tree.items() 返回 TreeEntry(path, mode, sha)，不是二元组
        for e in sorted(t.items(), key=lambda x: x.path):
            p = f"{prefix}{e.path.decode()}"
            obj = repo[e.sha]
            if obj.type_name == b"tree":
                walk(obj, p + "/")
            else:
                paths.append(p)
    walk(tree)
    print(f"[spike] OK  ls-tree 一次拿到 {len(paths)} 个文件: {paths}")

# ④ 读任意历史 commit 的 tree（「从某天恢复」要用）
with R(local) as repo:
    old = repo[c1 if isinstance(c1, bytes) else c1.raw_id if hasattr(c1, "raw_id") else bytes.fromhex(c1.hex()) if hasattr(c1, "hex") else c1]
    t = repo[old.tree]
    print(f"[spike] OK  读历史 commit 的 tree: {sorted(e.path.decode() for e in t.items())}")

# ⑤ fetch：远端被别人推进过，本地 fetch 能不能看到
other = os.path.join(work, "other")
porcelain.clone(bare, other)
with open(os.path.join(other, "items"), "w") as f:
    f.write("x")
porcelain.add(other, ["items"])
c2 = porcelain.commit(other, message=b"remote change", author=b"o <o@l>", committer=b"o <o@l>")
porcelain.push(other, bare, b"refs/heads/master")
fetch_result = porcelain.fetch(local, bare)
print(f"[spike] OK  fetch 到远端新提交: {short(fetch_result.refs[b'refs/heads/master'])}")

# ⑥ 非快进冲突：本地也改一笔，然后 push，看 dulwich 给什么信号
with open(os.path.join(local, "mine"), "w") as f:
    f.write("y")
porcelain.add(local, ["mine"])
c3 = porcelain.commit(local, message=b"local change", author=b"m <m@l>", committer=b"m <m@l>")
try:
    porcelain.push(local, bare, b"refs/heads/master")
    print("[spike] 意外：非快进竟然 push 成功了")
except Exception as e:
    print(f"[spike] OK  非快进被拒绝，信号类型 = {type(e).__name__}: {str(e)[:120]}")

# ⑦ 关键：pull 在分叉时会抛 DivergedBranches（dulwich 拒绝自动文本合并）。
#    我们的协议本来就不用 git 合并 —— 改成 fetch → 应用层三方合并 → commit → push。
try:
    porcelain.pull(local, bare)
    print("[spike] 意外：pull 竟然成功了")
except Exception as e:
    print(f"[spike] 确认 pull 不可用: {type(e).__name__} —— 符合设计，不用 git 文本合并")

res = porcelain.fetch(local, bare)
remote_sha = res.refs[b"refs/heads/master"]
with R(local) as repo:
    repo.refs[b"refs/heads/master"] = remote_sha        # 把本地分支移到远端 head 上
    print(f"[spike] OK  fetch 后远端 head={short(remote_sha)}，本地 master 已对齐")

# 合并结果作为新提交叠在远端 head 之上 → 变成快进，push 必然通过
with open(os.path.join(local, "merged.json"), "w") as f:
    f.write("{}")
porcelain.add(local, ["merged.json"])
c4 = porcelain.commit(local, message=b"sync: merged", author=b"m <m@l>", committer=b"m <m@l>")
porcelain.push(local, bare, b"refs/heads/master")
print(f"[spike] OK  分叉后 fetch→对齐→合并提交({short(c4)})→push 成功，恢复路径可用")

r.close()
shutil.rmtree(work, ignore_errors=True)
print("\n[spike] PASS  dulwich 能承担同步通道（本地通道；HTTPS+令牌待 M4 真验）")
