"""视图类功能：内置视图、日历范围、看板、视图偏好、今日统计、侧栏计数。

路由遮蔽由 `api/http.py` 在挂载时按具体度排序解决（静态路径优先于 `{参数}` 路径），
所以本模块的函数定义顺序不再敏感。曾经 `/views/counts` 被 `/views/{name}` 吃掉，
是契约测试抓出来的。
"""
from __future__ import annotations

from qingdan.core import stamp, view, task_query
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
               today: str | None = None, tag: str | None = None, name: str = "all",
               include_done: bool = False, filter_id: str | None = None,
               sort: str = "smart", reverse: bool = False, limit: int = 0, offset: int = 0,
               column: str | None = None, columns_limit: int = 0, columns_offset: int = 0) -> dict:
    from qingdan.core.board_query import query
    return query(ctx.need_store(), _today_or(today), group_by, list_id, tag, name, include_done,
                 filter_id, sort, reverse, limit, offset, column, columns_limit, columns_offset)


# --------------------------------------------------------------------------- 日历


@feature(name="view.calendar", group="view", http=("GET", "/api/v1/calendar"), idempotent=True)
def view_calendar(start: str | None = None, days: int = 42, today: str | None = None,
                  include_countdown: bool = True, list_id: str | None = None,
                  limit: int = 0, offset: int = 0, tag: str | None = None,
                  filter_id: str | None = None, name: str = 'all') -> dict:
    from qingdan.core.calendar_query import query
    d = _today_or(today)
    frm = _today_or(start) if start else d.replace(day=1)
    return query(ctx.need_store(),frm,int(days),d,include_countdown,list_id,
                 limit,offset,tag,filter_id,name)


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
               list_id: str | None = None, tag: str | None = None,
               limit: int = 0, offset: int = 0) -> dict:
    """五个内置视图 + all 的统一入口。视图是查询，不是数据副本。"""
    d = _today_or(today)
    if name not in view.VIEW_NAMES:
        raise validation_error("未知视图", allowed=list(view.VIEW_NAMES), got=name)
    if group not in view.GROUP_MODES:
        raise validation_error("未知分组方式", allowed=list(view.GROUP_MODES), got=group)
    if sort not in view.SORT_MODES:
        raise validation_error("未知排序方式", allowed=list(view.SORT_MODES), got=sort)

    with ctx.need_store().read_snapshot():
        where, args = task_query.view_where(name, d, include_done, list_id, tag)
        ordered, page = task_query.select(ctx.need_store(), where, args, sort, reverse, limit, offset)
        names = _group_names(ordered)
        buckets = view.group_items(ordered, group, d, names)
        return {
            "view": name,
            "today": d.isoformat(),
            "list_names": names,
            **page,
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
    with ctx.need_store().read_snapshot():
        db = task_query.connection(ctx.need_store())
        from datetime import timedelta
        # LIMIT -1 keeps this derived table as a streaming subquery on SQLite,
        # allowing date normalization once per row without a persistent custom index.
        row = db.execute(f"""SELECT
            coalesce(sum(alive AND opened AND due<=?),0),
            coalesce(sum(alive AND opened AND due<=?),0),
            coalesce(sum(alive AND opened AND coalesce(list_id,'')=''),0),
            coalesce(sum(alive AND status='done'),0),
            coalesce(sum(trashed),0),coalesce(sum(alive),0),
            coalesce(sum(alive AND opened AND due<?),0)
            FROM (SELECT i.deleted=0 alive,i.deleted=1 trashed,{task_query.OPEN} opened,
                         {task_query.STATUS} status,{task_query.DUE} due,
                         json_extract(i.data,'$.list_id') list_id
                  FROM item i WHERE i.kind='task' LIMIT -1)""",
            [d.isoformat(), (d + timedelta(days=6)).isoformat(), d.isoformat()]).fetchone()
        counts = dict(zip([*view.VIEW_NAMES, "overdue"], row))
        counts["by_group"] = dict(db.execute(
            "SELECT json_extract(data,'$.list_id'),COUNT(*) FROM item WHERE kind='task' AND deleted=0 "
            "AND coalesce(json_extract(data,'$.list_id'),'')<>'' GROUP BY json_extract(data,'$.list_id')"))
        counts["by_tag"] = dict(db.execute(
            "SELECT t.value,COUNT(*) FROM item i,json_each(i.data,'$.tags') t "
            "WHERE i.kind='task' AND i.deleted=0 GROUP BY t.value"))
        return counts


# --------------------------------------------------------------------------- 统计


@feature(name="stats.today", group="stats", http=("GET", "/api/v1/stats/today"), idempotent=True)
def stats_today(today: str | None = None) -> dict:
    d = _today_or(today)
    from datetime import timedelta
    with ctx.need_store().read_snapshot():
        db = task_query.connection(ctx.need_store())
        day = d.isoformat()
        status, due, open_ = task_query.STATUS, task_query.DUE, task_query.OPEN
        totals = db.execute(f"""SELECT
            coalesce(sum(CASE WHEN {open_} THEN 1 ELSE 0 END),0),
            coalesce(sum(CASE WHEN {open_} AND {due}<? THEN 1 ELSE 0 END),0),
            coalesce(sum(CASE WHEN {open_} AND {due}=? THEN 1 ELSE 0 END),0),
            coalesce(sum(CASE WHEN {status}='done' AND qd_day(json_extract(i.data,'$.done_at'))=? THEN 1 ELSE 0 END),0)
            FROM item i WHERE i.kind='task' AND i.deleted=0""", [day, day, day]).fetchone()
        priorities = dict(db.execute(f"SELECT {task_query.PRIORITY},COUNT(*) FROM item i "
                                    f"WHERE i.kind='task' AND i.deleted=0 AND {open_} GROUP BY {task_query.PRIORITY}"))
        rows = db.execute("SELECT id,json_extract(data,'$.title') title,qd_day(json_extract(data,'$.target_date')) target "
                          "FROM item WHERE kind='countdown' AND deleted=0 AND target BETWEEN ? AND ? ORDER BY target,id",
                          [day, (d + timedelta(days=30)).isoformat()])
        cds = [{"id": row["id"], "title": row["title"], "days_left": (view.as_date(row["target"]) - d).days} for row in rows]
        return {"today": day, "open_total": totals[0], "overdue": totals[1],
                "due_today": totals[2], "done_today": totals[3],
                "by_priority": {str(p): priorities.get(p, 0) for p in (1, 2, 3, 4)}, "countdown_soon": cds,
                "groups": db.execute("SELECT COUNT(*) FROM item WHERE kind='list' AND deleted=0").fetchone()[0],
                "tags": db.execute("SELECT COUNT(*) FROM item WHERE kind='tag' AND deleted=0").fetchone()[0]}
