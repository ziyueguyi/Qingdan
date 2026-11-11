"""探针：直接跑一遍告警作业生成，把被吞掉的异常暴露出来。"""
import sys, tempfile
sys.path.insert(0, "src")

import qingdan.bootstrap
from qingdan.core import context
from qingdan.core.alerting import config as acfg
from qingdan.core.alerting import scheduler
from qingdan.core.alerting.base import SendResult, register_channel
from qingdan.core.registry import call

class Fake:
    def __init__(self, name): self.name = name
    def is_configured(self): return True
    def enabled(self): return True
    def send(self, msgs): return SendResult(ok=True, sent_count=1, covered=len(msgs))
    def test(self): return SendResult(ok=True)

for n in ("toast", "dingtalk", "email"):
    register_channel(Fake(n))

ctx = context.init(tempfile.mkdtemp(), device_id="D-probe")
print("配置 channels:", acfg.get_config()["channels"], "master:", acfg.get_config()["master"])
print("启用渠道:", [c.name for c in scheduler._enabled_channels()])

tid = call("task.create", title="探针任务", due_at="2030-01-01T10:00:00")["id"]
item = ctx.store.get(tid)
print("提醒时间:", scheduler.reminder_times(item))
print("task.create 后作业数:", ctx.store.alert_pending_count())
print("直接 rebuild_for_item:", scheduler.rebuild_for_item(item))
print("rebuild_all:", scheduler.rebuild_all())

cid = call("countdown.create", title="倒数日", target_date="2030-06-01", remind_days_before=3)["id"]
print("countdown 作业数:", ctx.store.alert_pending_count())
print("alert.rebuild 接口:", call("alert.rebuild"))
