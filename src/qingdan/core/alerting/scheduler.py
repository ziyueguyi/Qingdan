"""告警调度器（M3 / A2）。

核心不变量：

1. **作业是派生数据**，由「提醒时间 × 启用渠道」重算。删了能重建，所以它不是业务真值，
   也**绝不能把 item 标成 dirty** —— 否则每次切免打扰时段都要往 git 提一次 commit。
2. **去重靠 `dedup_key` 唯一约束**，不靠内存状态。进程重启、补扫、并发都不会重发。
3. **补发防轰炸**：启动时把过期的 pending 全标 skipped。没有这条，关机一天后开机
   会瞬间涌出几十条提醒。
4. **一个渠道失败不影响其它渠道**，失败按指数退避重试，3 次后落 failed。
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta
from typing import Any, Callable

from qingdan.core import stamp, view
from qingdan.core.alerting import config as cfg
from qingdan.core.alerting.base import AlertMessage, get_channel
from qingdan.core.context import ctx

RETRY_BACKOFF_MS = [60_000, 300_000, 900_000]     # 1 / 5 / 15 分钟
MAX_ATTEMPTS = 3
STALE_GRACE_MS = 10 * 60 * 1000                    # 超过 10 分钟的未发提醒不再补发
SCAN_INTERVAL_S = 20


def _store():
    return ctx.need_store()


def reminder_times(item) -> list[int]:
    """一条记录的提醒时刻（绝对毫秒）。

    优先级：`snoozed_until` > `remind_at` 列表 > `due_at`（设了到期日就默认到期时提醒，
    这是滴答/Todoist 的通用行为）。倒数日用 `target_date - remind_days_before`。
    """
    d = item.data
    if item.kind == "countdown":
        target = view.parse_dt(d.get("target_date"))
        if not target:
            return []
        days = int(d.get("remind_days_before") or 0)
        fire = target.replace(hour=9, minute=0, second=0, microsecond=0) - timedelta(days=days)
        return [int(fire.timestamp() * 1000)]

    snooze = view.parse_dt(d.get("snoozed_until"))
    if snooze and snooze > stamp.now_dt():
        return [int(snooze.timestamp() * 1000)]

    times = [view.parse_dt(x) for x in (d.get("remind_at") or [])]
    times = [t for t in times if t]
    if times:
        return [int(t.timestamp() * 1000) for t in times]

    due = view.parse_dt(d.get("due_at"))
    return [int(due.timestamp() * 1000)] if due else []


def channels_for(item, available: list[str]) -> list[str]:
    """单任务的渠道覆盖；null 表示跟随全局。"""
    want = item.data.get("alert_channels")
    if want is None:
        return available
    return [c for c in available if c in set(want)]


def rebuild_for_item(item) -> int:
    """重算一条记录的全部 pending 作业。幂等：先清后建，重复调用结果一致。"""
    st = _store()
    st.alert_clear_pending(item.id)
    if item.deleted or item.data.get("status") in ("done", "cancelled"):
        return 0
    active = [c.name for c in _enabled_channels()]
    if not active:
        return 0
    created = 0
    for fire_at in reminder_times(item):
        for ch in channels_for(item, active):
            if st.alert_add(item.id, fire_at, ch):
                created += 1
    return created


def rebuild_all() -> dict:
    st = _store()
    total = 0
    for item in st.list_items(include_deleted=True):
        if item.kind in ("task", "countdown"):
            total += rebuild_for_item(item)
    return {"jobs": total, "pending": st.alert_pending_count()}


def _enabled_channels():
    """以**配置**为准，而不是问渠道自己。

    渠道的 `enabled()` 也会读配置，但调度器必须自己把关总开关与渠道开关 ——
    否则关掉的渠道仍会生成作业，只是发送时被拒，白白留下一堆 failed 记录。
    """
    c = cfg.get_config()
    if not c.get("master"):
        return []
    out = []
    for name, on in (c.get("channels") or {}).items():
        if not on:
            continue
        ch = get_channel(name)
        if ch is not None and ch.is_configured():
            out.append(ch)
    return out


def _to_message(job) -> AlertMessage | None:
    item = _store().get(job["item_id"])
    if item is None or item.deleted:
        return None
    d = item.data
    when = datetime.fromtimestamp(job["fire_at"] / 1000)
    today = view.as_date(stamp.now_day())
    overdue = (item.kind == "task"
               and (n := view.days_between(d.get("due_at"), today)) is not None and n < 0)
    return AlertMessage(
        item_id=item.id, kind=item.kind,
        title=d.get("title") or d.get("name") or "(无标题)",
        fire_at_ms=int(job["fire_at"]),
        when_text=when.strftime("%m-%d %H:%M") if when else "",
        group_name=_group_name(d.get("list_id")),
        priority=int(d.get("priority") or 4),
        overdue=overdue,
        days_left=view.days_between(d.get("target_date"), today) if item.kind == "countdown" else None,
    )


def _group_name(list_id) -> str:
    if not list_id:
        return ""
    g = _store().get(list_id)
    return g.data.get("name", "") if g else ""


def _minutes_of_day(now_ms: int) -> int:
    dt = datetime.fromtimestamp(now_ms / 1000)
    return dt.hour * 60 + dt.minute


def dispatch(now_ms: int | None = None) -> dict:
    """扫描到期作业并按渠道合并发送。返回本轮统计。"""
    st = _store()
    now_ms = now_ms or stamp.now_ms()
    due = st.alert_due(now_ms)
    if not due:
        return {"due": 0, "sent": 0, "failed": 0, "skipped": 0}

    c = cfg.get_config()
    quiet = (c["dnd"]["enabled"]
             and cfg.in_quiet_hours(_minutes_of_day(now_ms), c["dnd"]["start"], c["dnd"]["end"]))
    if cfg.is_globally_muted(now_ms) or quiet:
        for job in due:
            st.alert_mark(job["id"], "skipped", "quiet_hours" if quiet else "muted")
        return {"due": len(due), "sent": 0, "failed": 0,
                "skipped": len(due), "reason": "quiet_hours" if quiet else "muted"}

    by_channel: dict[str, list] = {}
    for job in due:
        by_channel.setdefault(job["channel"], []).append(job)

    sent = failed = 0
    for ch_name, jobs in by_channel.items():
        channel = get_channel(ch_name)
        if channel is None:
            for j in jobs:
                st.alert_mark(j["id"], "failed", "channel_not_registered")
                failed += 1
            continue
        msgs = [m for m in (_to_message(j) for j in jobs) if m]
        if not msgs:                       # 任务已删/已完成 → 作业作废
            for j in jobs:
                st.alert_mark(j["id"], "skipped", "item_gone")
            continue
        result = channel.send(msgs)
        if result.ok:
            for j in jobs:
                st.alert_mark(j["id"], "sent")
            sent += 1
            st.alert_log_add(ch_name, "info",
                             f"sent covered={result.covered} msgs={result.sent_count}")
        else:
            attempts = max(int(j["attempts"]) for j in jobs)
            if attempts + 1 >= MAX_ATTEMPTS:
                for j in jobs:
                    st.alert_mark(j["id"], "failed", f"{result.code}: {result.message}")
                failed += 1
            else:
                retry_at = now_ms + RETRY_BACKOFF_MS[min(attempts, len(RETRY_BACKOFF_MS) - 1)]
                for j in jobs:
                    st.alert_mark(j["id"], "pending", f"{result.code}: {result.message}", retry_at)
            st.alert_log_add(ch_name, "error", f"{result.code}: {result.message}")
    return {"due": len(due), "sent": sent, "failed": failed, "skipped": 0,
            "channels": list(by_channel)}


# --------------------------------------------------------------------------- 后台线程

class Scheduler:
    def __init__(self, interval_s: int = SCAN_INTERVAL_S, tick: Callable[[], float] = time.time):
        self.interval_s = interval_s
        self.tick = tick
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.last = {"due": 0, "sent": 0, "failed": 0, "skipped": 0}

    def start(self, skip_stale: bool = True) -> None:
        if skip_stale:
            # 必须在扫描之前：否则开机瞬间这批过期作业会被一起涌出
            n = _store().alert_skip_stale(int(self.tick() * 1000) - STALE_GRACE_MS)
            if n:
                _store().alert_log_add("scheduler", "info", f"启动时跳过 {n} 条过期提醒")
        if self._thread:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="qingdan-alert")
        self._thread.start()

    def _loop(self) -> None:
        while not self._stop.wait(self.interval_s):
            try:
                self.run_once()
            except Exception as e:                      # 调度线程绝不能因一次异常而死
                _store().alert_log_add("scheduler", "error", f"{type(e).__name__}: {e}")

    def run_once(self) -> dict:
        self.last = dispatch(int(self.tick() * 1000))
        return self.last

    def stop(self) -> None:
        self._stop.set()
        self._thread = None


scheduler = Scheduler()
