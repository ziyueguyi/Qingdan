"""把仓库里"无法解析的文件"正规化成合法墓碑。

判据不是"能不能 json.loads"，而是**能不能被 Item.from_payload 吃下**：缺 rev/kind 的
文件在每次同步里都会被 parse_tree 当坏文件跳过，等于永久留一个哑弹。

为什么不直接删：同步协议把「文件消失但没有墓碑」定义成协议外手工删除，客户端保险丝会
报警告并保留本地。用删除来清理，就是在训练自己用错方法。这里改写成带 rev 的
`deleted:true` 正规记录，之后由 `sync.compact` 按保留窗口正常回收。

做法故意用最朴素的一种：工作区里改文件 → add → commit → push，然后**重新 clone 回读**。
（"push 报了成功但读回没变"这个坑本项目踩过，所以回读不是可选步骤。）

用法：
    python scripts/quarantine_repo_junk.py           # 预演：列出坏文件
    python scripts/quarantine_repo_junk.py --apply   # 正规墓碑化并推送
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dulwich import porcelain                                  # noqa: E402

TOKEN = os.environ.get("GITEE_TOKEN", "")
OWNER = os.environ.get("GITEE_OWNER", "purple_moon")
REPO = "qd-data"


def is_bad(text: str) -> bool:
    try:
        d = json.loads(text)
    except (ValueError, TypeError):
        return True
    return not isinstance(d, dict) or "rev" not in d or "kind" not in d


def walk(repo, sha, prefix=""):
    for e in repo[sha].items():
        name = e.path.decode()
        sub = repo[e.sha]
        if sub.type_name == b"tree":
            yield from walk(repo, e.sha, prefix + name + "/")
        else:
            yield prefix + name, (getattr(sub, "data", None) or sub.as_raw()).decode("utf-8", "replace")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--work", default=os.environ.get("TEMP", ".") + os.sep + "qd-junk-work")
    ns = ap.parse_args()
    if not TOKEN:
        print("需要环境变量 GITEE_TOKEN")
        return 2

    url = f"https://{OWNER}:{TOKEN}@gitee.com/{OWNER}/{REPO}.git"
    target = Path(ns.work) / "clone"
    target.mkdir(parents=True, exist_ok=True)

    repo = porcelain.open_repo(str(target)) if (target / ".git").is_dir() else None
    if repo is None:
        porcelain.clone(url, str(target))
        repo = porcelain.open_repo(str(target))
    else:
        porcelain.pull(str(target), url)

    bad = [(p, v) for p, v in walk(repo, repo[repo.head()].tree) if p.startswith("items/") and is_bad(v)]
    print(f"items/ 里无法解析的文件：{len(bad)}")
    for p, v in bad:
        print("  ", p, "→", v[:70].replace("\n", " "))
    if not bad:
        print("仓库里没有坏文件。")
        return 0
    if not ns.apply:
        print("预演模式：以上每个文件会被改写成合法墓碑（deleted:true + 新 rev）。加 --apply。")
        return 0

    now_ms = int(time.time() * 1000)
    paths = []
    for p, _v in bad:
        f = target / p
        kind = p.split("/")[1]
        payload = {"schema": 1, "kind": kind, "id": f.stem,
                   "rev": {"ms": now_ms, "seq": 0, "dev": "D-clean"},
                   "data": {"title": f"（规范化：原文件无法解析 {f.name}）"},
                   "deleted": True, "deleted_at_ms": now_ms,
                   "meta": {"creator_dev": "D-clean"}}
        f.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                     encoding="utf-8")
        paths.append(str(f))

    for p in paths:
        porcelain.add(str(target), paths=[p])
    porcelain.commit(str(target), message=f"规范化 {len(paths)} 个无法解析的 items 文件".encode(),
                     author=b"clean <clean@local>", committer=b"clean <clean@local>")
    porcelain.push(str(target), url, refspecs=[b"refs/heads/master:refs/heads/master"])

    # 回读必须用一次全新的 clone：本地工作副本正是刚改过的那份，用它自证等于没验
    verify = Path(ns.work) / "verify"
    if verify.is_dir():
        porcelain.fetch(str(verify), url)
        vrepo = porcelain.open_repo(str(verify))
    else:
        porcelain.clone(url, str(verify))
        vrepo = porcelain.open_repo(str(verify))
    still = [p for p, v in walk(vrepo, vrepo[vrepo.head()].tree) if p.startswith("items/") and is_bad(v)]
    print(f"回读校验：远端仍无法解析的文件 = {len(still)}")
    return 0 if not still else 1


if __name__ == "__main__":
    raise SystemExit(main())
