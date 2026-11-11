"""真实 Gitee 端到端验收（M4 + M5）。

假 API 只能证明逻辑分支对，证明不了「Gitee 认这套凭据、私有仓真能推、
推上去的内容真在远端」。这一步用真令牌打真仓库，令牌从环境变量读。
"""
import os
import shutil
import sys
import tempfile

sys.path.insert(0, "src")

import qingdan.bootstrap
from qingdan.core import context
from qingdan.core.gitee import GiteeClient
from qingdan.core.gitrepo import GitRepo, redact
from qingdan.core.registry import call

TOKEN = os.environ["GITEE_TOKEN"]
work = tempfile.mkdtemp(prefix="qd-live-")
context.init(work, device_id="D-live")

try:
    print("=== 1. 登录 ===")
    me = call("auth.login", token=TOKEN)
    print(f"  账号 {me['login']}（{me.get('name')}），时钟偏差 {me['clock_offset_ms']} ms")

    print("=== 2. 仓库就绪（应命中已存在的私有仓）===")
    info = call("repo.ensure", name="qd-data")
    print(f"  {info['full_name']}  private={info['private']}  created={info['created']}")
    assert info["private"] is True, "必须是私有仓库"

    print("=== 3. 造几条真实数据并同步 ===")
    g = call("group.create", name="工作任务", color="#b8730f")["id"]
    t1 = call("task.create", title="整理滨江法院立案材料", list_id=g, priority=1,
              due_at="2026-11-06T20:00:00", tags=["重要"])["id"]
    t2 = call("task.create", title="导出采集清单并上传需求单", list_id=g, priority=2)["id"]
    call("countdown.create", title="事业单位统考", target_date="2027-03-28")

    report = call("sync.run")
    print(f"  mode={report['mode']} pushed={report['pushed']} pulled={report['pulled']} "
          f"conflicts={report['conflicts']} attempts={report['attempts']}")
    assert report["ok"], report["error"]

    print("=== 4. 直接从远端读回来核对（不信本地缓存）===")
    client = GiteeClient(lambda: TOKEN)
    repo = client.repo(me["login"], "qd-data")
    print(f"  远端 private={repo['private']} public={repo['public']} branch={repo['default_branch']}")
    verify = os.path.join(work, "verify")
    GitRepo(verify).clone(client.push_url(me["login"], "qd-data"))
    g2 = GitRepo(verify)
    files = g2.read_tree(g2.head())
    item_files = sorted(p for p in files if p.startswith("items/"))
    print(f"  远端文件 {len(files)} 个，其中 items/ {len(item_files)} 个:")
    for p in item_files:
        print(f"    {p}")
    assert "manifest.json" in files and ".gitattributes" in files
    assert any("task" in p for p in item_files)

    print("=== 5. 第二轮同步：应当没有任何东西要推 ===")
    second = call("sync.run")
    print(f"  pushed={second['pushed']} pulled={second['pulled']}")
    assert second["pushed"] == 0, "幂等性：没有改动就不该再产生提交"

    print("=== 6. 修改一条，再同步，确认远端跟着变 ===")
    call("task.update", item_id=t1, title="整理滨江法院立案材料（已补充 EMS 单号）")
    call("sync.run")
    # fetch 不会移动本地分支（我们不用 pull），所以必须用它返回的远端 head，
    # 用 head() 读到的是本地旧位置
    head = g2.fetch(client.push_url(me["login"], "qd-data"))
    back = g2.read_tree(head)[f"items/task/{t1}.json"]
    assert "已补充 EMS 单号" in back.decode("utf-8"), "远端内容没跟着更新"
    print("  远端内容已更新 ✓")

    print("=== 7. 删除一条，确认远端留下墓碑而不是文件消失 ===")
    call("task.delete", item_id=t2)
    call("sync.run")
    blob = g2.read_tree(g2.fetch(client.push_url(me["login"], "qd-data")))[f"items/task/{t2}.json"]
    assert b'"deleted": true' in blob, "删除必须是墓碑"
    print("  远端墓碑已写入 ✓")

    print("\n[LIVE] PASS  真实 Gitee 私有仓端到端同步可用")
    print(f"  仓库: https://gitee.com/{me['login']}/qd-data （私有）")
finally:
    context.shutdown()
    shutil.rmtree(work, ignore_errors=True)
