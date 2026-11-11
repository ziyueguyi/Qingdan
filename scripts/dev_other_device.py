"""`python scripts/dev_other_device.py [数据目录] [新标题] [item_id]`

模拟"另一台设备"：直接改裸仓库里那条 item，用一个**更晚的版本戳**推上去。

为什么用这种土办法而不是再起一个 app 实例：单实例锁正是刚加的保护，两个进程抢同一个
数据目录会被拒（这是对的）。而真实的双设备冲突在协议上就是"远端树里多了一个更晚的版本"，
所以手工把那个版本推上去，走的是完全一样的合并路径 —— 引擎分辨不出也不该分辨出
对端是谁写的。
"""
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from dulwich import porcelain                                     # noqa: E402

DATA = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(".devcloud2")
TITLE = sys.argv[2] if len(sys.argv) > 2 else "另一台改的标题"
ITEM_ID = sys.argv[3] if len(sys.argv) > 3 else None
bare = (DATA / "cloud.git").resolve()
work = Path(os.environ["TEMP"]) / "qd-other-device"

if not work.is_dir():
    porcelain.clone(str(bare), str(work))
porcelain.pull(str(work), str(bare))

# 按 id 找而不是按标题找：标题正是我们要改的东西，用它当锚点第二次跑就找不到了
target = None
for root, _d, names in os.walk(work / "items"):
    for n in names:
        p = Path(root) / n
        text = p.read_text(encoding="utf-8")
        if (ITEM_ID and ITEM_ID in n) or (not ITEM_ID and '"冲突验证任务"' in text):
            target = p
            break
    if target:
        break
assert target, f"没找到那条任务（id={ITEM_ID}）"

item = json.loads(target.read_text(encoding="utf-8"))
item["data"]["title"] = TITLE
item["data"]["note"] = "另一台写的备注"
item["rev"] = {"ms": int(time.time() * 1000) + 120_000,     # 晚两分钟，确保远端胜出
               "seq": 0, "dev": "D-other"}
target.write_text(json.dumps(item, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                  encoding="utf-8")

porcelain.add(str(work), [str(target)])
porcelain.commit(str(work), message="另一台设备改了这条",
                 author=b"other <o@example.invalid>", committer=b"other <o@example.invalid>")
head = porcelain.open_repo(str(work)).head()
porcelain.push(str(work), str(bare), refspecs=[b"refs/heads/master:refs/heads/master"])
print("另一台已推送 rev:", item["rev"], "commit:", head.decode()[:8])
