"""给 M2 端到端验证灌一批演示数据（用真实接口写，不碰 SQLite）。"""
import sys
sys.path.insert(0, "src")

from qingdan.core import context
from qingdan.core.registry import call

context.init(sys.argv[1] if len(sys.argv) > 1 else ".m2demo", device_id="D-demo")

g_work = call("group.create", name="工作任务", color="#b8730f")["id"]
g_life = call("group.create", name="个人备忘", color="#3b82f6")["id"]
g_study = call("group.create", name="学习安排", color="#7457e8")["id"]
g_fit = call("group.create", name="锻炼计划", color="#0f9d8e")["id"]

for n in ["重要", "数据", "考编", "家务"]:
    call("tag.create", name=n)

rows = [
    ("整理滨江法院立案材料", g_work, 1, "2026-11-05T20:00:00", ["重要"], "含《依法立案申请书》、5 月首次提交的时间戳截图、12368 工单编号。"),
    ("12368 投诉工单编号回查", g_work, 2, "2026-11-05T10:00:00", [], ""),
    ("写周报", g_work, 3, "2026-11-04T18:00:00", [], "逾期示例"),
    ("导出采集清单并上传需求单", g_work, 2, "2026-11-06T09:00:00", ["数据"], ""),
    ("事业单位统考报名", g_study, 1, "2026-11-10T09:00:00", ["考编", "重要"], ""),
    ("行测资料分析 2 组", g_study, 3, "2026-11-07T20:00:00", ["考编"], ""),
    ("给鱼缸换水", g_life, 4, "2026-11-05T19:00:00", ["家务"], ""),
    ("跑步 5 公里", g_fit, 4, "2026-11-08T07:00:00", [], ""),
    ("Shovel 定时任务切 ON", None, 2, None, ["数据"], "未排期，应该在收集箱"),
    ("买猫粮", None, 4, None, [], ""),
]
ids = {}
for title, gid, prio, due, tags, note in rows:
    tid = call("task.create", title=title, list_id=gid, priority=prio,
               due_at=due, tags=tags, note=note)["id"]
    ids[title] = tid

call("task.complete", item_id=ids["回复钉钉消息"] if "回复钉钉消息" in ids else ids["买猫粮"])
call("task.reopen", item_id=ids["买猫粮"])
call("task.complete", item_id=ids["给鱼缸换水"])
call("task.delete", item_id=ids["写周报"])
call("task.set_color", item_id=ids["事业单位统考报名"], color="#7457e8")
call("task.set_repeat", item_id=ids["导出采集清单并上传需求单"], repeat="weekly")
call("note.append", item_id=ids["整理滨江法院立案材料"], text="窗口递交记得索要收件凭证")
call("note.append", item_id=ids["整理滨江法院立案材料"], text="网上上传留时间戳")
call("task.create", title="准备 EMS 材料", parent_id=ids["整理滨江法院立案材料"], list_id=g_work)

call("countdown.create", title="事业单位统考", target_date="2027-03-28")
call("countdown.create", title="立案监督材料截止", target_date="2026-11-10")
call("countdown.create", title="母亲生日", target_date="2026-12-10",
     repeat_yearly=True, show_age=True)

st = context.ctx.store
print(f"分组 {len(st.list_items(kind='list'))} · 标签 {len(st.list_items(kind='tag'))} · "
      f"任务 {len(st.list_items(kind='task'))} · 倒数日 {len(st.list_items(kind='countdown'))} · "
      f"待推送 {st.dirty_count()}")
st.mark_clean()
context.shutdown()
