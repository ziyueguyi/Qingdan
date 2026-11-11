"""时间缝（core/stamp.py）。

这些测试的价值不在"某个日子对不对"，而在**写入侧与统计侧必须同源**：
以前两边各读各的墙钟，测试传 `today="2026-11-05"` 而 `task.complete` 写真实时间，
于是断言只在跑测试那天刚好是 11 月 5 日时成立 —— 日历一跨午夜就红。
下面的不变量测试故意把时钟钉到一个毫不相干的日子，同源不成立就当场失败。
"""
from __future__ import annotations

import ast
from datetime import datetime
from pathlib import Path

import pytest

from qingdan.core import stamp
from qingdan.core.registry import call

PINNED = datetime(2027, 3, 14, 9, 30, 0)      # 故意用一个"未来且不同星期"的日子


@pytest.fixture
def pinned():
    stamp.set_clock(lambda: PINNED)
    yield PINNED
    stamp.set_clock(None)


def test_write_and_stats_share_one_clock(pinned):
    due = call("task.create", title="今天要做的", due_at="2027-03-14T18:00:00")["id"]
    done = call("task.create", title="今天做完的", due_at="2027-03-14T08:00:00")["id"]
    assert call("task.complete", item_id=done)["data"]["done_at"] == "2027-03-14T09:30:00"

    s = call("stats.today")            # 不传 today —— 默认值必须来自同一把缝
    assert s["today"] == "2027-03-14", "视图默认的「今天」没跟着缝走，说明还有第二处墙钟"
    assert s["due_today"] == 1, "只数还开着的"
    assert s["done_today"] == 1
    assert s["open_total"] == 1


def test_view_query_default_today_follows_the_seam(pinned):
    call("task.create", title="排在今天", due_at="2027-03-14T23:00:00")
    call("task.create", title="排在明天", due_at="2027-03-15T01:00:00")
    out = call("view.query", name="today")
    assert [x["data"]["title"] for x in out["items"]] == ["排在今天"]


def test_quick_add_parses_against_the_same_day(pinned):
    out = call("task.quick_add", text="明天下午3点 交材料", create=False)
    assert out["parsed"]["due_at"] == "2027-03-15T15:00:00", \
        "自然语言里的「明天」必须相对同一把缝"


def test_countdown_direction_follows_the_seam(pinned):
    call("countdown.create", title="就是今天", target_date="2027-03-14")
    call("countdown.create", title="还有三天", target_date="2027-03-17")
    items = {x["title"]: x for x in call("countdown.list")["items"]}
    assert items["就是今天"]["direction"] == "today"
    assert items["还有三天"]["direction"] == "left" and items["还有三天"]["days"] == 3


def test_snooze_uses_the_seam(pinned):
    t = call("task.create", title="待办", due_at="2027-03-14T10:00:00")["id"]
    out = call("task.snooze", item_id=t, minutes=90)
    assert out["data"]["snoozed_until"] == "2027-03-14T11:00:00"


def test_only_the_seam_reaches_for_the_calendar():
    """静态兜底：读「日历日期」只能经 stamp，例外要写数量与理由。

    本守卫只管**日历语义**（`datetime.now` / `date.today` / 带 %Y %m %d 的 `strftime`）。
    白名单**按文件计数**，所以行号怎么漂都不影响，而在已豁免文件里多加一处会立刻红。

    2026-11-10 修订：这里原来还写着「不约束 `time.time()` —— 把 13 处计时调用一并禁掉
    只会逼人在白名单里塞满条目」。前半句的理由成立（纯计时量的是"过了多少毫秒"），
    但结论被实测推翻了：做重复任务时一次性扫出 **6 处**拿 `time.time()*1000` 与
    **经缝写入的时间戳**比较（`alert.status` 的 pending、`dispatch` 的默认 now、
    `alert.silence` 写 `silence_until`、`alert.preview` 的地平线、`compact` 的墓碑截止、
    toast 测试发送）—— 那正是本文件开头说的"两边各读各的墙钟"，只是发生在毫秒侧。
    所以毫秒侧另有守卫：`tests/test_time_seam.py`（含豁免表与「可注入时钟默认值」识别，
    豁免表 9 条、且死条目同样算失败，没有出现当年担心的白名单膨胀）。
    """
    src = Path(__file__).resolve().parents[1] / "src" / "qingdan"
    allowed = {
        "core/backup.py": (3, "备份文件名要真实日期，与「今天统计」无关"),
        "core/export.py": (3, "导出文件名与正文时间戳，属文件内容"),
        "core/gitee.py": (1, "用服务器 Date 头算 offset，必须拿真实本地时刻做差"),
        "core/sync_api.py": (1, "仓库 manifest 的 created_at 是协议字段"),
    }
    counts: dict[str, list[int]] = {}
    for py in sorted((src / "core").rglob("*.py")):
        rel = py.relative_to(src).as_posix()
        tree = ast.parse(py.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                continue
            base, attr = node.func.value, node.func.attr
            name = f"{getattr(base, 'id', ast.unparse(base))}.{attr}"
            hit = name in {"datetime.now", "datetime.today", "date.today"}
            if not hit and name == "time.strftime" and node.args:
                fmt = node.args[0].value if isinstance(node.args[0], ast.Constant) else ""
                hit = any(tok in fmt for tok in ("%Y", "%m", "%d"))
            if hit:
                counts.setdefault(rel, []).append(node.lineno)

    unexpected = {f: ls for f, ls in counts.items() if f not in allowed}
    drift = {f: (len(ls), allowed[f][0]) for f, ls in counts.items()
             if f in allowed and len(ls) != allowed[f][0]}
    assert not unexpected, (
        "这些文件绕过时间缝直接读了日历（日期边界测试会因此只在特定日子成立）：\n"
        + "\n".join(f"  {f}:{ls}" for f, ls in sorted(unexpected.items()))
        + "\n要么改用 qingdan.core.stamp，要么在 allowed 里写明理由和数量")
    assert not drift, f"豁免文件里的读数变了（实际, 记录值）：{drift} —— 加了就要写理由"
