"""M4 spike：dulwich 走 HTTPS + Gitee 私人令牌，能不能真的推送。

这是同步链路最后一个未证明的环节 —— 本地裸仓库跑通不代表 HTTPS 智能服务也行。
令牌从环境变量读，不落文件；所有输出经脱敏。
"""
import os
import shutil
import sys
import tempfile

sys.path.insert(0, "src")

from dulwich import porcelain
from dulwich.repo import Repo

from qingdan.core.gitrepo import GitRepo, redact

USER = os.environ["GITEE_USER"]
TOKEN = os.environ["GITEE_TOKEN"]
OWNER = USER
NAME = "qd-data"

CANDIDATES = [
    f"https://{USER}:{TOKEN}@gitee.com/{OWNER}/{NAME}.git",
    f"https://oauth2:{TOKEN}@gitee.com/{OWNER}/{NAME}.git",
]

work = tempfile.mkdtemp(prefix="qd-m4-")
ok_url = None

for url in CANDIDATES:
    target = os.path.join(work, "try")
    shutil.rmtree(target, ignore_errors=True)
    print(f"\n=== 尝试 {redact(url).split('://')[0]} + {'oauth2' if 'oauth2:' in url else 'login'} ===")
    try:
        porcelain.clone(url, target)
        print("  clone OK")
    except Exception as e:
        print(f"  clone FAIL {type(e).__name__}: {str(e)[:160]}")
        continue

    with open(os.path.join(target, "manifest.json"), "w", encoding="utf-8") as f:
        f.write('{\n  "product": "qingdan",\n  "schema": 1\n}\n')
    with open(os.path.join(target, ".gitattributes"), "w", encoding="utf-8") as f:
        f.write("*.json text eol=lf\n")
    try:
        porcelain.add(target, paths=["manifest.json", ".gitattributes"])
        porcelain.commit(target, message=b"spike: init",
                         author=b"qingdan <q@local>", committer=b"qingdan <q@local>")
        porcelain.push(target, url, b"refs/heads/master")
        print("  commit + push OK")
    except Exception as e:
        print(f"  push FAIL {type(e).__name__}: {str(e)[:200]}")
        continue

    # 二次 clone 验证远端真的有内容
    verify = os.path.join(work, "verify")
    shutil.rmtree(verify, ignore_errors=True)
    porcelain.clone(url, verify)
    with Repo(verify) as r:
        head = r.head()
        tree = r[r[head].tree]
        names = sorted(e.path.decode() for e in tree.items())
    print(f"  远端确认存在文件: {names}")
    ok_url = url
    break

if not ok_url:
    print("\n[M4] FAIL —— 两种凭据形式都推不上去，同步通道需要换方案（git.exe 或 SSH）")
    sys.exit(1)

# 用真实 GitRepo 再跑一轮，确认封装层也能走 HTTPS
print("\n=== 用 GitRepo 封装层复验 ===")
g = GitRepo(os.path.join(work, "wrapped"))
g.clone(ok_url)
sha = g.commit_and_push({"items/task/SPIKE1.json": b'{"id":"SPIKE1"}\n'}, message="spike: item")
head = g.fetch(ok_url)
print(f"  commit={sha[:8] if isinstance(sha, bytes) else sha} fetch_head={head[:8] if head else None}")
paths = set(g.read_tree(head))
print(f"  远端文件: {sorted(paths)}")
assert "items/task/SPIKE1.json" in paths, "封装层推送的内容没出现在远端"

print("\n[M4] PASS  dulwich HTTPS + 私人令牌可推送，GitRepo 封装层同样可用")
shutil.rmtree(work, ignore_errors=True)
