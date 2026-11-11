"""把验收脚本留在远端仓库里的演示数据清出去（走产品自己的同步路径）。

为什么不用 git 直接删文件：同步协议把"文件消失但没有墓碑"定义为**协议外手工删除**，
客户端遇到会报警告并保留本地（那是防误删的保险丝）。如果我手删文件，用户第一次
真同步就会看到一条莫名其妙的异常告警——那等于我用清理动作制造了一个 bug。
所以这里：拉下来 → 逐条调用各自的 delete 接口 → 同步 → 远端留下正规墓碑。

墓碑不会立刻从工作区消失，这是**故意的**：`sync.compact` 只回收超过保留窗口（默认 90 天）
的墓碑，太早回收会让一台长期离线的机器把已删项静默复活（M5 的 D1 场景）。
想连历史 commit 一起物理清除需要重写历史 + 强推，那是另一个决定，脚本不做。

用法：
    python scripts/clean_remote_demo.py            # 预演，只列会删哪些
    python scripts/clean_remote_demo.py --apply    # 真的删并同步
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile

sys.path.insert(0, "src")

import qingdan.bootstrap                                   # noqa: E402
from qingdan.core import context                           # noqa: E402
from qingdan.core.registry import call                     # noqa: E402

DELETE_FEATURE = {
    "task": "task.delete", "countdown": "countdown.delete", "list": "group.delete",
    "folder": "folder.delete", "section": "section.delete", "tag": "tag.delete",
    "note": "note.delete", "filter": "filter.delete",
}
TOKEN = os.environ.get("GITEE_TOKEN", "")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ns = ap.parse_args()
    if not TOKEN:
        print("需要环境变量 GITEE_TOKEN")
        return 2

    work = tempfile.mkdtemp(prefix="qd-clean-")
    try:
        context.init(work, device_id="D-clean")
        me = call("auth.login", token=TOKEN)
        info = call("repo.ensure", name="qd-data")
        if not info["private"]:
            print("仓库不是私有，拒绝操作")     # 哨兵在前，这里只是二次确认
            return 1
        print(f"账号 {me['login']}，仓库 {info['full_name']}（私有）")

        first = call("sync.run")
        print(f"拉取：mode={first['mode']} pulled={first['pulled']} conflicts={first['conflicts']}")

        store = context.ctx.store
        alive = [it for it in store.list_items(include_deleted=True)
                 if not it.deleted and it.kind in DELETE_FEATURE]
        print(f"仓库里活着的数据 {len(alive)} 条：")
        for it in alive:
            d = it.data
            print(f"  {it.kind:<9} {(d.get('title') or d.get('name') or d.get('text') or '')[:30]:<32} {it.id}")
        if not alive:
            print("已经是干净的（只剩墓碑或空）。")
            return 0
        if not ns.apply:
            print("预演模式：以上都会被写成墓碑并推回。加 --apply 执行。")
            return 0

        for it in alive:
            call(DELETE_FEATURE[it.kind], item_id=it.id)
        report = call("sync.run")
        print(f"推送：pushed={report['pushed']} pulled={report['pulled']} ok={report['ok']}")
        if not report["ok"]:
            print(f"同步没成功，远端状态未确认：{report.get('error')}")
            return 1

        after = call("sync.run")
        print(f"再同步一轮：pushed={after['pushed']}（应为 0，证明幂等）")
        print("完成。墓碑按协议保留在仓库里，90 天后由 sync.compact 回收；"
              "要连历史一起物理清除需要重写历史 + 强推，请另行决定。")
        return 0
    finally:
        context.shutdown()
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
