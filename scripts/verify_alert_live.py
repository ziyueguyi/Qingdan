"""M3 真实发送验证：错误路径（A9.4 后半）+ 30 条合并成一条（A9.7）。

用**临时数据目录**跑，不污染 .m2demo 的演示库。webhook/secret 从环境变量读，
不落进任何文件。
"""
import os
import sys
import tempfile

sys.path.insert(0, "src")

import qingdan.bootstrap  # noqa: F401
from qingdan.core import context
from qingdan.core.alerting import scheduler
from qingdan.core.alerting.dingtalk import DingTalkChannel
from qingdan.core.registry import call

WH = os.environ["DINGTALK_WEBHOOK"]
SEC = os.environ["DINGTALK_SECRET"]

context.init(tempfile.mkdtemp(), device_id="D-verify")

# ---------- A9.4 后半：故意用错 secret，看真因有没有被带出来 ----------
print("=== 错误路径：签名不对时 UI 能不能看到真因 ===")
bad = DingTalkChannel(lambda: {
    "master": True, "channels": {"dingtalk": True}, "dingtalk": {},
    "_secrets": {"dingtalk_webhook": WH, "dingtalk_secret": "SECthis_is_wrong"},
})
r = bad.test()
print("ok:", r.ok, "| code:", r.code, "| message:", r.message)
print("→ 必须是失败，且 message 带上钉钉返回的 errmsg（HTTP 200 但 errcode!=0）")

# ---------- A9.7：30 条同分钟到期，只该发一条 ----------
print("\n=== 合并发送：30 条同分钟到期 ===")
call("alert.config_update", config={"secrets": {"dingtalk_webhook": WH,
                                                "dingtalk_secret": SEC},
                                    "channels": {"dingtalk": True},
                                    "dnd": {"enabled": False}})
for i in range(30):
    call("task.create", title=f"合并验证{i + 1}", due_at="2030-01-01T10:00:00", priority=2)

st = context.ctx.store
# 把所有作业的触发时间压到同一分钟，模拟「同时到期」
st.db.execute("UPDATE alert_job SET fire_at = ?", (1,))
st.db.commit()
print("待触发作业:", st.alert_pending_count())

sent: list[int] = []
real = scheduler.get_channel("dingtalk")
_orig_post = real._post
real._post = lambda url, payload: (lambda res: (sent.append(len(payload["markdown"]["text"])), res)[1])(
    _orig_post(url, payload))

out = scheduler.dispatch(9_999_999_999_999)
print("本轮发送次数:", len(sent), "（期望 1）")
print("dispatch 统计:", out)
print("剩余 pending:", st.alert_pending_count())
