"""重复任务（定时任务）端到端测试。

覆盖的不是 rrule 的算术（那在 test_rrule.py / test_rrule_props.py），而是**接线**：
完成/跳过真的推进了 Item、提醒跟着平移、历史落在本地表、撤销连历史一起撤、
日历真的画出未来次数、三个新接口三种入口都通、以及「历史永不进同步」。

一条贯穿全文件的纪律：不静默。推不动就必须给出机器键说明原因。
"""
from __future__ import annotations

import json
from datetime import datetime

import pytest

from harness import Machine, remote, root      # noqa: F401  （fixture 由 conftest 注入）
from qingdan.core import stamp
from qingdan.core.models import Item
from qingdan.core.registry import QdError, call
from qingdan.core.store import Store

NOW = datetime(2026, 11, 9, 20, 0, 0)


@pytest.fixture(autouse=True)
def _clock_pinned(app_ctx):
    """钉住写入侧的墙钟。

    顺延断言的全是日期，如果不钉时钟，跨午夜跑测试就会「今天过、明天红」——
    这正是 test_views_api.py 里记着的那个坑。
    """
    stamp.set_clock(lambda: NOW)
    yield
    stamp.set_clock(None)


def _task(**kw) -> str:
    return call("task.create", **{"title": "喝水", **kw})["id"]


def _data(item_id: str) -> dict:
    return call("task.get", item_id=item_id)["data"]


# --------------------------------------------------------------------------- 完成即顺延


def test_completing_daily_task_rolls_to_next_day():
    tid = _task(due_at="2026-11-09T20:00:00", repeat="daily")
    out = call("task.complete", item_id=tid)
    d = out["data"]
    assert d["due_at"] == "2026-11-10T20:00:00"      # 时刻守恒
    assert d["status"] == "todo"                      # 完成的是那一次，任务继续
    assert d["done_at"] is None
    assert out["repeat_roll"] == {"rolled": True, "kind": "done",
                                  "next_due_at": "2026-11-10T20:00:00",
                                  "occurrence": "2026-11-09T20:00:00"}


def test_rolling_three_times_walks_forward():
    tid = _task(due_at="2026-11-09", repeat="weekly")
    dues = []
    for _ in range(3):
        dues.append(call("task.complete", item_id=tid)["data"]["due_at"])
    assert dues == ["2026-11-16", "2026-11-23", "2026-11-30"]


def test_date_only_due_stays_date_only():
    """全天任务推进后不许多出个 00:00 —— 那会让 due_all_day 与 due_at 自相矛盾。"""
    tid = _task(due_at="2026-11-09", repeat="daily")
    got = call("task.complete", item_id=tid)["data"]["due_at"]
    assert got == "2026-11-10" and "T" not in got


def test_month_end_task_clamps_to_feb_28():
    tid = _task(due_at="2026-01-31", repeat="monthly")
    assert call("task.complete", item_id=tid)["data"]["due_at"] == "2026-02-28"


def test_non_repeating_task_completes_normally():
    tid = _task(due_at="2026-11-09T20:00:00")
    out = call("task.complete", item_id=tid)
    assert out["data"]["status"] == "done"
    assert out["data"]["done_at"] == "2026-11-09T20:00:00"
    assert out["repeat_roll"]["rolled"] is False
    assert call("task.occurrences", item_id=tid)["total"] == 0


# --------------------------------------------------------------------------- 不静默


def test_repeat_without_due_says_why_it_cannot_roll():
    tid = _task(repeat="daily")                      # 没有到期日
    out = call("task.create", title="x", repeat="daily")
    assert out["needs_due_key"] == "repeat_needs_due"
    done = call("task.complete", item_id=tid)
    assert done["data"]["status"] == "done"           # 仍然正常完成
    assert done["repeat_roll"]["repeat_blocked_key"] == "repeat_needs_due"


def test_invalid_rule_synced_from_other_machine_is_reported_not_crashing():
    """另一台机器塞来的畸形规则：视图不许崩，完成时必须说出原因。"""
    from qingdan.core.context import ctx
    st = ctx.need_store()
    tid = _task(due_at="2026-11-09", repeat="daily")
    old = st.get(tid)
    st.put(Item(kind=old.kind, id=old.id, rev=st.next_rev(),
                data={**old.data, "repeat": "FREQ=HOURLY"}, meta=old.meta), dirty=True)

    out = call("task.complete", item_id=tid)
    assert out["repeat_roll"]["repeat_blocked_key"] == "repeat_rule_invalid"
    assert out["data"]["status"] == "done"

    info = call("task.repeat_info", item_id=tid)
    assert info["invalid_rule_key"] == "repeat_rule_invalid"
    # 日历必须照常出图（脏数据不许把整页带崩）
    cells = call("view.calendar", start="2026-11-01", days=30, today="2026-11-09")["cells"]
    assert len(cells) == 30


def test_setting_unsupported_rule_is_rejected_at_write_time():
    """曾经任何 FREQ= 串都原样收下，界面画 ↻ 却永不推进 —— 那就是空壳。"""
    for bad in ("FREQ=HOURLY", "FREQ=DAILY;BYSETPOS=2", "每两小时"):
        with pytest.raises(QdError):
            call("task.create", title="x", due_at="2026-11-09", repeat=bad)
    tid = _task(due_at="2026-11-09")
    with pytest.raises(QdError):
        call("task.set_repeat", item_id=tid, repeat="FREQ=SECONDLY")
    with pytest.raises(QdError):
        call("task.update", item_id=tid, repeat="FREQ=HOURLY")


def test_count_exhaustion_ends_the_series():
    tid = _task(due_at="2026-11-09", repeat="FREQ=DAILY;COUNT=2")
    first = call("task.complete", item_id=tid)
    assert first["data"]["due_at"] == "2026-11-10" and first["data"]["status"] == "todo"
    last = call("task.complete", item_id=tid)
    assert last["data"]["status"] == "done"                 # 最后一次收尾
    assert last["repeat_roll"]["repeat_ended"] is True
    assert last["repeat_roll"]["repeat_ended_key"] == "repeat_ended"


# --------------------------------------------------------------------------- 跳过


def test_skip_advances_without_completing():
    tid = _task(due_at="2026-11-09T20:00:00", repeat="daily")
    out = call("task.skip", item_id=tid)
    assert out["data"]["due_at"] == "2026-11-10T20:00:00"
    assert out["data"]["status"] == "todo" and out["data"]["done_at"] is None
    rows = call("task.occurrences", item_id=tid)["occurrences"]
    assert len(rows) == 1 and rows[0]["kind"] == "skipped"


def test_skip_on_non_repeating_task_is_an_error_not_a_noop():
    tid = _task(due_at="2026-11-09")
    with pytest.raises(QdError):
        call("task.skip", item_id=tid)


# --------------------------------------------------------------------------- 提醒与贪睡


def test_reminders_shift_with_the_due_date():
    """任务滚到明天、提醒留在昨天 = 用户被叫醒一次然后再也不响。"""
    tid = _task(due_at="2026-11-09T20:00:00", repeat="daily")
    call("task.set_reminder", item_id=tid, remind_at=["2026-11-09T19:30:00"], mode="once")
    out = call("task.complete", item_id=tid)
    assert out["data"]["remind_at"] == ["2026-11-10T19:30:00"]     # 提前量保住了


def test_alert_jobs_are_rebuilt_to_the_new_fire_time():
    """任务滚到明天、提醒留在昨天 = 用户被叫醒一次然后再也不响。

    到期时刻必须**晚于**钉住的"现在"：`alert.status` 的 pending 只数未来的作业，
    取成同一时刻会得到 0，测的就不是推进而是自己的取值。
    """
    call("alert.channel_enable", name="toast", on=True)
    tid = _task(due_at="2026-11-09T23:00:00", repeat="daily")
    assert call("alert.status")["pending"] >= 1
    call("task.complete", item_id=tid)
    from qingdan.core.context import ctx
    rows = list(ctx.need_store().db.execute(
        "SELECT fire_at FROM alert_job WHERE item_id=? AND status='pending'", (tid,)))
    assert len(rows) == 1                                   # 旧作业被清掉，没有残留
    fired = datetime.fromtimestamp(rows[0]["fire_at"] / 1000)
    assert (fired.year, fired.month, fired.day) == (2026, 11, 10)
    assert (fired.hour, fired.minute) == (23, 0)             # 提前量/时刻都跟着走
    assert call("alert.status")["pending"] >= 1              # 提醒没有丢


def test_snooze_is_cleared_when_rolling():
    """贪睡只针对那一次；带进下一次会让新的一次一开始就是静音的。"""
    from qingdan.core.context import ctx
    tid = _task(due_at="2026-11-09T20:00:00", repeat="daily")
    old = ctx.need_store().get(tid)
    ctx.need_store().put(Item(kind=old.kind, id=old.id, rev=ctx.need_store().next_rev(),
                              data={**old.data, "snoozed_until": "2026-11-09T21:00:00"},
                              meta=old.meta), dirty=True)
    out = call("task.complete", item_id=tid)
    assert "snoozed_until" not in out["data"]


# --------------------------------------------------------------------------- 历史与撤销


def test_history_counts_done_and_skipped_separately():
    tid = _task(due_at="2026-11-09", repeat="daily")
    call("task.complete", item_id=tid)
    call("task.skip", item_id=tid)
    call("task.complete", item_id=tid)
    info = call("task.repeat_info", item_id=tid)
    assert info["history"] == {"done": 2, "skipped": 1, "total": 3}
    assert info["history_synced"] is False and info["synced"] is True


def test_undo_of_a_roll_removes_the_history_row():
    """撤销一次推进后，历史里不许留着一条没发生过的完成。"""
    tid = _task(due_at="2026-11-09", repeat="daily")
    call("task.complete", item_id=tid)
    assert call("task.occurrences", item_id=tid)["total"] == 1
    call("undo.last")
    assert _data(tid)["due_at"] == "2026-11-09"
    assert call("task.occurrences", item_id=tid)["total"] == 0


def test_undo_of_unrelated_edit_keeps_history():
    """精确性反证：改标题再撤销，不许误删完成历史。"""
    tid = _task(due_at="2026-11-09", repeat="daily")
    call("task.complete", item_id=tid)
    call("task.update", item_id=tid, title="改名")
    call("undo.last")
    assert _data(tid)["title"] == "喝水"
    assert call("task.occurrences", item_id=tid)["total"] == 1


# --------------------------------------------------------------------------- 锚


def test_anchor_is_set_when_repeat_starts_and_never_moves_on_roll():
    tid = _task(due_at="2026-11-09", repeat="FREQ=DAILY;COUNT=3")
    assert _data(tid)["repeat_start"] == "2026-11-09"
    call("task.complete", item_id=tid)
    assert _data(tid)["repeat_start"] == "2026-11-09"      # 推进不动锚


def test_explicit_due_change_reanchors():
    """用户改到期日 = 重新指定「从哪天开始循环」，锚必须跟着走。"""
    tid = _task(due_at="2026-11-09", repeat="FREQ=DAILY;COUNT=3")
    call("task.set_due", item_id=tid, due_at="2026-12-01")
    assert _data(tid)["repeat_start"] == "2026-12-01"
    # 重新锚定后 COUNT 重新数：12-01 是第 1 次，还剩 2 次
    assert call("task.repeat_info", item_id=tid)["upcoming"] == ["2026-12-02", "2026-12-03"]


def test_disabling_repeat_clears_anchor():
    tid = _task(due_at="2026-11-09", repeat="daily")
    call("task.set_repeat", item_id=tid, repeat=None)
    d = _data(tid)
    assert d["repeat"] is None and d["repeat_start"] is None


# --------------------------------------------------------------------------- 预览接口


def test_repeat_info_gives_machine_keys_not_parsed_text():
    tid = _task(due_at="2026-11-09", repeat="FREQ=WEEKLY;INTERVAL=2;BYDAY=MO,TH")
    info = call("task.repeat_info", item_id=tid, count=3)
    assert info["summary"]["key"] == "rp_every_n_weeks" and info["summary"]["n"] == 2
    assert info["summary"]["days"] == ["wd_mo", "wd_th"]
    assert len(info["upcoming"]) == 3
    # 后端不下发中文展示文案（英文界面残留中文是实测踩过的整族 bug）
    blob = json.dumps(info, ensure_ascii=False)
    assert not any("\u4e00" <= ch <= "\u9fff" for ch in blob)


def test_repeat_info_404_for_unknown_task():
    with pytest.raises(QdError):
        call("task.repeat_info", item_id="NOPE")


# --------------------------------------------------------------------------- 日历展开


def test_calendar_expands_future_occurrences_as_virtual_events():
    _task(title="每日", due_at="2026-11-09", repeat="daily")
    cells = {c["date"]: c for c in
             call("view.calendar", start="2026-11-09", days=5, today="2026-11-09")["cells"]}
    assert cells["2026-11-09"]["recurring"] == 0            # 当前那次由 Item 自己画
    assert cells["2026-11-09"]["count"] == 1
    for day in ("2026-11-10", "2026-11-11", "2026-11-12"):
        assert cells[day]["recurring"] == 1
        assert cells[day]["events"][0]["occurrence"] is True
        assert cells[day]["events"][0]["overdue"] is False   # 未来的次数不算逾期


def test_calendar_does_not_expand_done_or_past():
    tid = _task(title="每日", due_at="2026-11-05", repeat="daily")
    call("task.complete", item_id=tid)          # 滚到 11-06
    cells = {c["date"]: c for c in
             call("view.calendar", start="2026-11-01", days=12, today="2026-11-09")["cells"]}
    # 过去（含漏掉的那几次）不铺虚拟事件：逾期由 Item 自己表达
    for day in ("2026-11-01", "2026-11-05", "2026-11-07", "2026-11-08"):
        assert cells[day]["recurring"] == 0
    assert cells["2026-11-10"]["recurring"] == 1

    call("task.update", item_id=tid, status="cancelled")
    cells2 = {c["date"]: c for c in
              call("view.calendar", start="2026-11-09", days=4, today="2026-11-09")["cells"]}
    assert all(c["recurring"] == 0 for c in cells2.values())


def test_calendar_expansion_is_capped():
    """畸形/密集规则不许把响应撑爆。"""
    for i in range(30):
        _task(title=f"每日{i}", due_at="2026-11-09", repeat="daily")
    cells = call("view.calendar", start="2026-11-09", days=370, today="2026-11-09")["cells"]
    total = sum(c["recurring"] for c in cells)
    assert 0 < total <= 400


# --------------------------------------------------------------------------- HTTP 路由


def test_http_routes_exist_for_the_three_new_features():
    from fastapi.testclient import TestClient

    from qingdan.api.http import build_app

    client = TestClient(build_app())
    tid = _task(due_at="2026-11-09T20:00:00", repeat="daily")
    # HTTP 层统一包 {ok, data, trace_id}，断言要取 data 那一层
    r = client.get(f"/api/v1/tasks/{tid}/repeat")
    assert r.status_code == 200 and r.json()["data"]["summary"]["key"] == "rp_daily"
    r = client.post(f"/api/v1/tasks/{tid}/skip")
    assert r.status_code == 200
    assert r.json()["data"]["data"]["due_at"] == "2026-11-10T20:00:00"
    r = client.get(f"/api/v1/tasks/{tid}/occurrences")
    assert r.status_code == 200 and r.json()["data"]["total"] == 1


# --------------------------------------------------------------------------- 同步


def test_roll_propagates_but_history_never_does(root, remote):   # noqa: F811
    """推进后的 due_at 是数据、要同步；完成历史是设备本地流水、绝不进仓库。"""
    a = Machine(root, "a", remote)
    b = Machine(root, "b", remote)
    it = a.add(kind="task", title="喝水", due_at="2026-11-09", repeat="daily",
               status="todo", priority=4, remind_at=[], tags=[], order=0,
               repeat_start="2026-11-09", done_at=None, created_at="2026-11-01T00:00:00")
    assert a.sync().ok is True

    # A 机完成一次：直接用 store 模拟推进后的状态（service 层已在上面测过）
    a.store.occurrence_add(it.id, "2026-11-09", "2026-11-09T20:00:00", "done")
    a.edit(it.id, due_at="2026-11-10", status="todo", done_at=None)
    res = a.sync()
    assert res.ok is True and res.pushed >= 1

    assert b.sync().ok is True
    got = b.item(it.id)
    assert got.data["due_at"] == "2026-11-10"                 # 下一次同步过去了
    assert b.store.occurrence_count(it.id)["total"] == 0       # 历史没跟过去
    assert not any("occurrence" in p for p in a.remote_paths())  # 仓库里没有历史文件
    # 仓库里只有一条该任务的文件，没有按次生成的实例（不预生成是硬纪律）
    assert [p for p in a.remote_paths() if p.startswith(f"items/task/{it.id}")] == \
        [f"items/task/{it.id}.json"]


def test_two_machines_both_editing_keeps_the_roll_and_the_loser(root, remote):   # noqa: F811
    """A 推进、B 同时改标题：按协议是**整条 HLC 大者胜**（merge.pick 的第 4 行），
    不是字段级合并。所以要断言的是两件事：推进后的 due_at 不能丢，落败版本必须留档可找回。

    这条用例存在的原因：我一开始按"字段级合并"断言，测试红了 —— 红的是我的想象，
    不是代码。真实语义写进断言，下次有人改成字段级合并时这条会提醒他同步改文档。
    """
    a = Machine(root, "a", remote)
    b = Machine(root, "b", remote)
    it = a.add(kind="task", title="喝水", due_at="2026-11-09", repeat="daily", status="todo")
    a.sync()
    b.sync()

    b.edit(it.id, title="喝水（改名）")
    assert b.sync().ok is True
    a.edit(it.id, due_at="2026-11-10")           # A 机推进，版本戳更晚
    assert a.sync().ok is True

    b.sync()
    got = b.item(it.id)
    assert got.data["due_at"] == "2026-11-10"     # 推进没有被拉回旧值
    assert got.data["title"] == "喝水"            # 整条胜出 → B 的改名落败
    # 落败方必须留档（不静默丢数据），并且两台机器最终一致
    assert a.conflicts() or b.conflicts()
    assert a.item(it.id).data == got.data
