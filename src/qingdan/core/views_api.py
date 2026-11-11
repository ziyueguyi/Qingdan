"""视图类功能：内置视图、日历范围、看板、视图偏好、今日统计、侧栏计数。

路由遮蔽由 `api/http.py` 在挂载时按具体度排序解决（静态路径优先于 `{参数}` 路径），
所以本模块的函数定义顺序不再敏感。曾经 `/views/counts` 被 `/views/{name}` 吃掉，
是契约测试抓出来的。
"""
from __future__ import annotations

from qingdan.core import stamp, view
from qingdan.core.context import ctx
from qingdan.core.registry import feature, not_found, validation_error

PREF_KEYS = frozenset({"view", "layout", "group", "sort", "reverse", "color_by",
                       "collapsed", "column_widths", "sidebar_hidden",
                       "folded_folders",   # 侧栏里被收起的分组（folder）id 列表
                       "sb_min", "dt_min"})  # 侧栏 / 详情面板折叠态（跨会话保留）


def _today_or(value: str | None):
    """视图里的"今天"。不传就用**同一个时间缝**，不能各读各的墙钟：
    `stats.today` 要数的 `done_at` 是 service 用这把缝写进去的。"""
    return view.as_date(value) if value else view.as_date(stamp.now_day())


def _group_names(items) -> dict[str, str]:
    return ctx.need_store().list_names(it.data.get("list_id") for it in items)


def _pool(tasks_only: bool = True):
    st = ctx.need_store()
    return st.list_items(kind="task") if tasks_only else st.list_items()


# --------------------------------------------------------------------------- 看板（先注册）


@feature(name="view.board", group="view", http=("GET", "/api/v1/views/board"), idempotent=True)
def view_board(group_by: str = "status", list_id: str | None = None,
               today: str | None = None) -> dict:
    d = _today_or(today)
    pool = [it for it in _pool() if view.is_alive(it)]
    if list_id:
        pool = [it for it in pool if it.data.get("list_id") == list_id]
    names = _group_names(pool)
    cols = view.board_columns(pool, group_by, names, d)
    return {"group_by": group_by, "total": len(pool), "columns": cols, "list_names": names}


# --------------------------------------------------------------------------- 日历


@feature(name="view.calendar", group="view", http=("GET", "/api/v1/calendar"), idempotent=True)
def view_calendar(start: str | None = None, days: int = 42, today: str | None = None,
                  include_countdown: bool = True, list_id: str | None = None) -> dict:
    d = _today_or(today)
    frm = _today_or(start) if start else d.replace(day=1)
    if not 1 <= int(days) <= 370:
        raise validation_error("days 需在 1..370", got=days)
    items = [it for it in ctx.need_store().list_items() if view.is_alive(it)]
    if list_id:
        items = [it for it in items if it.data.get("list_id") == list_id]
    if not include_countdown:
        items = [it for it in items if it.kind != "countdown"]
    return {"start": frm.isoformat(), "days": int(days), "today": d.isoformat(),
            "cells": view.calendar_days(items, frm, int(days), d)}


# --------------------------------------------------------------------------- 视图偏好（本地，不同步）


@feature(name="view.pref_get", group="view", http=("GET", "/api/v1/views/prefs"), idempotent=True)
def view_pref_get() -> dict:
    """视图偏好属设备本地数据，**永不进同步**（§11.5）—— 手机上改的分组不该动电脑。"""
    return {"prefs": ctx.need_store().pref_get(), "synced": False}


@feature(name="view.pref_set", group="view", http=("PATCH", "/api/v1/views/prefs"),
         undoable=False)
def view_pref_set(prefs: dict) -> dict:
    if not isinstance(prefs, dict) or not prefs:
        raise validation_error("prefs 必须是非空对象")
    unknown = set(prefs) - PREF_KEYS
    if unknown:
        raise validation_error("不支持的偏好项", allowed=sorted(PREF_KEYS), got=sorted(unknown))
    st = ctx.need_store()
    for k, v in prefs.items():
        st.pref_set(k, v)
    return {"prefs": st.pref_get(), "synced": False}


# --------------------------------------------------------------------------- 内置视图（最后注册）


@feature(name="view.query", group="view", http=("GET", "/api/v1/views/{name}"), idempotent=True)
def view_query(name: str, today: str | None = None, group: str = "date", sort: str = "smart",
               reverse: bool = False, include_done: bool = False,
               list_id: str | None = None, tag: str | None = None) -> dict:
    """五个内置视图 + all 的统一入口。视图是查询，不是数据副本。"""
    d = _today_or(today)
    if name not in view.VIEW_NAMES:
        raise validation_error("未知视图", allowed=list(view.VIEW_NAMES), got=name)
    if group not in view.GROUP_MODES:
        raise validation_error("未知分组方式", allowed=list(view.GROUP_MODES), got=group)
    if sort not in view.SORT_MODES:
        raise validation_error("未知排序方式", allowed=list(view.SORT_MODES), got=sort)

    # 取全量（含墓碑）再交给 apply_view —— 垃圾桶视图要看的就是墓碑，
    # 这里若过滤掉已删除项，trash 会永远是空的。
    pool = ctx.need_store().list_items(kind="task", include_deleted=True)
    if list_id:
        pool = [it for it in pool if it.data.get("list_id") == list_id]
    if tag:
        pool = [it for it in pool if tag in (it.data.get("tags") or [])]

    items = view.apply_view(name, pool, d, include_done=include_done)
    ordered = view.sort_items(items, sort, reverse)
    names = _group_names(ordered)
    buckets = view.group_items(ordered, group, d, names)
    return {
        "view": name,
        "today": d.isoformat(),
        "list_names": names,
        "total": len(ordered),
        # label_key 给 UI 文案（需翻译），label 给用户数据（永不翻译）
        "groups": [{"key": b["key"], "label_key": b["label_key"], "label": b["label"],
                    "total": len(b["items"]),
                    "items": [it.to_dict() for it in b["items"]]} for b in buckets],
        "items": [it.to_dict() for it in ordered],
    }


@feature(name="view.counts", group="view", http=("GET", "/api/v1/views/counts"), idempotent=True)
def view_counts(today: str | None = None) -> dict:
    """侧栏徽标计数。

    必须单独提供：前端如果拿「当前视图的任务」去数其它视图的角标，切到「今天」时
    全部/收集箱/已完成都会显示成今天的条数（实测踩过）。
    """
    d = _today_or(today)
    pool = ctx.need_store().list_items(kind="task", include_deleted=True)
    counts = {name: len(view.apply_view(name, pool, d, include_done=(name == "done")))
              for name in view.VIEW_NAMES}
    counts["overdue"] = len([it for it in view.apply_view("today", pool, d)
                             if (n := view.days_between(view.due_key(it), d)) is not None and n < 0])
    # 分组与标签的角标也一并给出：前端逐组请求会打 N 次接口，用当前视图自己数又是错的
    alive = [it for it in pool if not it.deleted]
    by_group: dict[str, int] = {}
    by_tag: dict[str, int] = {}
    for it in alive:
        lid = it.data.get("list_id")
        if lid:
            by_group[lid] = by_group.get(lid, 0) + 1
        for name in (it.data.get("tags") or []):
            by_tag[name] = by_tag.get(name, 0) + 1
    counts["by_group"] = by_group
    counts["by_tag"] = by_tag
    return counts


# --------------------------------------------------------------------------- 统计


@feature(name="stats.today", group="stats", http=("GET", "/api/v1/stats/today"), idempotent=True)
def stats_today(today: str | None = None) -> dict:
    d = _today_or(today)
    tasks = [it for it in ctx.need_store().list_items(kind="task") if view.is_alive(it)]
    open_t = [it for it in tasks if view.is_open(it)]
    overdue = [it for it in open_t
               if (n := view.days_between(it.data.get("due_at"), d)) is not None and n < 0]
    due_today = [it for it in open_t if view.days_between(it.data.get("due_at"), d) == 0]
    done_today = [it for it in tasks
                  if it.data.get("status") == "done"
                  and view.to_date(it.data.get("done_at")) == d]
    by_prio = {str(p): sum(1 for it in open_t if int(it.data.get("priority", 4)) == p)
               for p in (1, 2, 3, 4)}
    cds = []
    for it in ctx.need_store().list_items(kind="countdown"):
        n = view.days_between(it.data.get("target_date"), d)
        if n is not None and 0 <= n <= 30:
            cds.append({"id": it.id, "title": it.data.get("title"), "days_left": n})
    cds.sort(key=lambda x: x["days_left"])
    return {"today": d.isoformat(), "open_total": len(open_t), "overdue": len(overdue),
            "due_today": len(due_today), "done_today": len(done_today),
            "by_priority": by_prio, "countdown_soon": cds,
            "groups": len(ctx.need_store().list_items(kind="list")),
            "tags": len(ctx.need_store().list_items(kind="tag"))}
