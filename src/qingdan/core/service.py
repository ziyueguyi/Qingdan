"""功能实现层。每个函数都用 @feature 登记，自动获得 HTTP 与 CLI 入口。

这里只放 M0/M1 已实现的功能；未实现的不注册空壳接口（宁缺毋假）。
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any

from qingdan import __version__
from qingdan.core.context import app_dir as context_app_dir, ctx
from qingdan.core.merge import same  # noqa: F401  （保持模块导出面，供同步层复用）
from qingdan.core.models import Item, new_id
from qingdan.core.nlp import parse_quick
from qingdan.core import rrule, stamp, workdays
from qingdan.core.icons import check_icon
from qingdan.core.registry import REGISTRY, feature, not_found, validation_error
from qingdan.core.view import as_date, parse_dt

VALID_STATUS = {"todo", "doing", "done", "cancelled"}
VALID_PRIORITY = {1, 2, 3, 4}

#: 重复规则存**规范值**，不存中文字面量 —— 否则界面语言一改，历史数据里的 "每周"
#: 就成了英文界面上的乱码，而且没法国际化。中文写法仍接受，落库前归一化。
REPEAT_CANON = {"daily": "daily", "weekly": "weekly", "monthly": "monthly", "yearly": "yearly"}
REPEAT_ALIASES = {"每天": "daily", "每日": "daily", "daily": "daily",
                  "每周": "weekly", "weekly": "weekly",
                  "每月": "monthly", "monthly": "monthly",
                  "每年": "yearly", "每年重复": "yearly", "yearly": "yearly"}


def _validate_repeat(value):
    """规范化重复规则，并**当场拒绝本子集不支持的写法**。

    以前任何 `FREQ=` 开头的串都原样存下（`FREQ=HOURLY`、`BYSETPOS=2` 全收），
    界面照样画出 ↻ 却永远不会推进 —— 那就是「宁缺毋假」要挡的空壳。
    """
    if not value:
        return None
    v = str(value).strip()
    canon = v.upper() if v.upper().startswith("FREQ=") else REPEAT_ALIASES.get(v)
    if canon is None:
        raise validation_error("repeat 支持 daily/weekly/monthly/yearly、每天/每周/每月/每年，"
                               "或 RFC5545 的 FREQ=…", got=value)
    try:
        rrule.parse(canon)
    except rrule.RuleError as e:
        raise validation_error(f"repeat 规则不支持：{e}", got=value) from None
    return canon


def _anchor_for(repeat: str | None, due_at: Any) -> str | None:
    """维护 `repeat_start` —— COUNT 计数与 INTERVAL 对齐的锚。

    **显式改到期日/改重复规则时重新下锚，自动推进时绝不动它**：否则 COUNT=10 的任务
    每推进一次锚就跟着走，永远数不到头。没有到期日就没法下锚（返回 None，
    推进时会明确告知原因而不是静默不滚）。
    """
    if not repeat:
        return None
    return due_at or None

_TASK_FIELDS = {
    "title", "note", "list_id", "folder_id", "section_id", "parent_id",
    "status", "priority", "color", "due_at", "due_all_day", "remind_at",
    "remind_mode", "alert_channels", "tags", "order", "repeat", "done_at",
}


# --------------------------------------------------------------------------- 内部


def _now_ms() -> int:
    return stamp.now_ms()


def _validate_task_data(data: dict, item_id: str | None = None) -> None:
    title = data.get("title")
    if not isinstance(title, str) or not title.strip():
        raise validation_error("title 不能为空")
    data["title"] = title.strip()
    tags = data.get("tags") or []
    if not isinstance(tags, list) or any(not isinstance(tag, str) for tag in tags):
        raise validation_error("tags 必须是标签名称数组")
    data["tags"] = list(dict.fromkeys(tag.strip().lstrip("#").strip() for tag in tags
                                     if tag.strip().lstrip("#").strip()))
    due = data.get("due_at")
    if due and (not isinstance(due, str) or parse_dt(due) is None):
        raise validation_error("due_at 必须是 ISO8601 日期或时间", got=due)
    st = ctx.need_store()
    for key, kind in (("list_id", "list"), ("section_id", "section"), ("parent_id", "task")):
        value = data.get(key)
        if value:
            target = st.get(value)
            if target is None or target.deleted or target.kind != kind:
                raise validation_error(f"{key} 指向的对象不存在或类型不符")
    if data.get("section_id"):
        section = st.get(data["section_id"])
        if section.data.get("list_id") != data.get("list_id"):
            raise validation_error("小节不属于目标清单")
    seen = {item_id} if item_id else set()
    ancestor = data.get("parent_id")
    while ancestor:
        if ancestor in seen:
            raise validation_error("父任务关系不能形成循环")
        seen.add(ancestor)
        parent = st.get(ancestor)
        ancestor = parent.data.get("parent_id") if parent else None


def _stamp(meta: dict, dev: str) -> dict:
    meta = dict(meta)
    meta.setdefault("creator_dev", dev)
    meta["editor_dev"] = dev
    return meta


def _sync_alerts(item: Item) -> None:
    """提醒作业是派生数据，写完记录就重算。

    绝不能在里面对 item 再发一次版本 —— 否则每次改提醒都会多一条待推送，
    切个免打扰时段就要往 git 提一次 commit。
    """
    if item.kind not in ("task", "countdown"):
        return
    try:
        from qingdan.core.alerting import scheduler
        scheduler.rebuild_for_item(item)
    except Exception:
        pass          # 告警未注册/未配置不该影响任务保存


def _create(kind: str, data: dict, deleted: bool = False) -> Item:
    st = ctx.need_store()
    item = Item(
        kind=kind,
        id=new_id(),
        rev=st.next_rev(),
        data=data,
        deleted=deleted,
        deleted_at_ms=_now_ms() if deleted else None,
        meta=_stamp({}, st.device_id),
    )
    st.put(item, dirty=True)
    st.op_touch(None, item.id, "created")        # 前像为空 = 新建，撤销时应写墓碑
    _sync_alerts(item)
    return item


def _load(item_id: str, allow_deleted: bool = True) -> Item:
    it = ctx.need_store().get(item_id)
    if it is None:
        raise not_found(f"任务 {item_id}")
    if it.deleted and not allow_deleted:
        raise not_found(f"任务 {item_id}（在垃圾桶里）")
    return it


def _bump(old: Item, data: dict | None = None, deleted: bool | None = None) -> Item:
    st = ctx.need_store()
    item = Item(
        kind=old.kind,
        id=old.id,
        rev=st.next_rev(),
        data=dict(old.data) if data is None else data,
        deleted=old.deleted if deleted is None else deleted,
        deleted_at_ms=old.deleted_at_ms if deleted is None else (_now_ms() if deleted else None),
        meta=_stamp(old.meta, st.device_id),
    )
    st.put(item, dirty=True)
    st.op_touch(old, item.id, "deleted" if (deleted or old.deleted) else "updated")
    _sync_alerts(item)
    return item


def _validate_priority(value: Any) -> int:
    try:
        p = int(value)
    except (TypeError, ValueError):
        raise validation_error("priority 必须是 1|2|3|4", got=repr(value)) from None
    if p not in VALID_PRIORITY:
        raise validation_error("priority 必须是 1|2|3|4（1=P1 紧急，4=无）", got=p)
    return p


def _smart_key(it: Item) -> tuple:
    """Todoist 的智能序：日期时间 → 优先级 → 截止期限 → 手动序 → 创建时间。"""
    d = it.data
    due = d.get("due_at") or "9999-12-31T23:59:59"
    return (
        due,
        int(d.get("priority", 4)),
        d.get("due_at") is None,
        int(d.get("order", 0)),
        d.get("created_at") or "",
        it.id,
    )


# --------------------------------------------------------------------------- 应用


@feature(name="app.health", group="app", http=("GET", "/api/v1/health"), idempotent=True)
def health() -> dict:
    """存活探针 + 侧栏徽标计数。不需要登录，供外部脚本与冒烟测试使用。"""
    st = ctx.need_store()
    from qingdan.core.notification_inbox import token as notification_token
    with st.read_snapshot():
        return {
            "ok": True,
            "version": __version__,
            "device_id": st.device_id,
            **st.health_counts(),
            "change_token": st.change_token(),
            "task_token": st.task_change_token(),
            "notification_token": notification_token(st),
            "today": stamp.now_day(),
            "authed": ctx.authed,
            "data_dir": str(ctx.data_dir),
            # 数据到底读自哪里、为什么在那里：出问题时第一句要能回答这个
            "data_dir_source": ctx.data_dir_source,
            "app_dir": str(context_app_dir()),
        }


@feature(name="app.info", group="app", http=("GET", "/api/v1/app/info"), idempotent=True)
def app_info() -> dict:
    st = ctx.need_store()
    return {
        "version": __version__,
        "data_dir": str(ctx.data_dir),
        "db_path": str(st.path),
        "device_id": st.device_id,
        "clock": {"offset_ms": st.clock.offset_ms, "last_ms": st.clock.last_ms,
                  "last_seq": st.clock.last_seq},
        "features": sorted(REGISTRY),
        "workday_calendar": workdays.metadata(),
    }


# --------------------------------------------------------------------------- 应用偏好（设备本地）

APP_PREF_KEYS = {"theme", "accent", "size", "lang", "sidebar_collapsed",
                 "quick_add_default_group"}


@feature(name="app.pref_get", group="app", http=("GET", "/api/v1/app/prefs"), idempotent=True)
def app_pref_get() -> dict:
    """主题/语言/字号存后端而**不是 localStorage**：刷新、换浏览器、换机器都不丢，
    同时它们属于设备本地数据，永不进同步（§11.5）。"""
    st = ctx.need_store()
    all_prefs = st.pref_get() or {}
    return {"prefs": {k: all_prefs[k] for k in APP_PREF_KEYS if k in all_prefs},
            "synced": False}


@feature(name="app.pref_set", group="app", http=("PATCH", "/api/v1/app/prefs"), undoable=False)
def app_pref_set(prefs: dict) -> dict:
    if not isinstance(prefs, dict) or not prefs:
        raise validation_error("prefs 必须是非空对象")
    unknown = set(prefs) - APP_PREF_KEYS
    if unknown:
        raise validation_error("不支持的偏好项", allowed=sorted(APP_PREF_KEYS), got=sorted(unknown))
    if "theme" in prefs and prefs["theme"] not in {"light", "dark"}:
        raise validation_error("theme 只能是 light|dark", got=prefs["theme"])
    if "size" in prefs and prefs["size"] not in {"s", "m", "l"}:
        raise validation_error("size 只能是 s|m|l", got=prefs["size"])
    if "lang" in prefs and prefs["lang"] not in {"zh", "en"}:
        raise validation_error("lang 只能是 zh|en", got=prefs["lang"])
    st = ctx.need_store()
    for k, v in prefs.items():
        st.pref_set(k, v)
    return app_pref_get()


# --------------------------------------------------------------------------- 快速添加


@feature(name="task.quick_add", group="task", http=("POST", "/api/v1/tasks/quick-add"),
         undoable=True)
def task_quick_add(text: str, today: str | None = None, create: bool = False,
                   list_id: str | None = None) -> dict:
    """一句「明天下午3点 交周报 #工作任务 @重要 !P1」→ 结构化任务。

    默认只解析不落库（create=False），让 UI 先回显识别结果再确认。
    解析失败一律降级为纯标题，不报错 —— 快速添加弹校验框等于劝退。
    """
    st = ctx.need_store()
    parsed = parse_quick(
        text,
        today=as_date(today) if today else None,
        groups=[{"id": g.id, "name": g.data.get("name")} for g in st.list_items(kind="list")],
        tags=[t.data.get("name") for t in st.list_items(kind="tag")])
    if not create:
        return {"created": None, "parsed": parsed}
    if not parsed["title"]:
        raise validation_error("无法从输入中解析出标题")
    item = task_create(title=parsed["title"], due_at=parsed["due_at"],
                       list_id=parsed["list_id"] or list_id, tags=parsed["tags"],
                       priority=parsed["priority"] or 4)
    return {"created": item, "parsed": parsed}


# --------------------------------------------------------------------------- 任务


@feature(name="task.create", group="task", http=("POST", "/api/v1/tasks"), undoable=True)
def task_create(title: str, note: str = "", priority: int = 4, due_at: str | None = None,
                tags: list | None = None, color: str | None = None,
                list_id: str | None = None, section_id: str | None = None,
                parent_id: str | None = None, order: int = 0,
                repeat: str | None = None, due_all_day: bool | None = None) -> dict:
    """新建任务。未指定 list_id 的任务落收集箱（GTD Inbox）。

    `due_all_day` 默认 True；传了带时间的 due_at 而未显式指定时按 False 推断 ——
    否则弹窗里选"20:00"建出来的任务会被标成全天，界面上时间凭空消失（2026-11-10 补）。
    """
    if not title or not title.strip():
        raise validation_error("title 不能为空")
    st = ctx.need_store()
    for key, value, expect in (("list_id", list_id, "list"), ("section_id", section_id, "section"),
                               ("parent_id", parent_id, "task")):
        if value:
            target = st.get(value)
            if target is None or target.kind != expect:
                raise validation_error(f"{key} 指向的对象不存在或类型不符",
                                       key=key, got=None if not target else target.kind)
    canon_repeat = _validate_repeat(repeat)
    data = {
        "title": title.strip(),
        "note": note,
        "list_id": list_id,
        "section_id": section_id,
        "parent_id": parent_id,
        "status": "todo",
        "priority": _validate_priority(priority),
        "color": color,
        "due_at": due_at,
        "due_all_day": bool(due_all_day) if due_all_day is not None
                       else (True if not due_at or "T" not in str(due_at) else False),
        "remind_at": [],
        "remind_mode": "once",
        "alert_channels": None,
        "tags": list(tags or []),
        "order": int(order),
        "repeat": canon_repeat,
        "repeat_start": _anchor_for(canon_repeat, due_at),
        "done_at": None,
        "created_at": stamp.now_text(),
    }
    _validate_task_data(data)
    _align_workday_start(data)
    out = _create("task", data).to_dict()
    if canon_repeat and not due_at:
        # 不静默收下：设了重复却没有到期日 = 永远不会推进，必须让用户看见原因
        out["needs_due_key"] = "repeat_needs_due"
    return out


@feature(name="task.get", group="task", http=("GET", "/api/v1/tasks/{item_id}"), idempotent=True)
def task_get(item_id: str, include_deleted: bool = True) -> dict:
    return _load(item_id, allow_deleted=include_deleted).to_dict()


@feature(name="task.list", group="task", http=("GET", "/api/v1/tasks"), idempotent=True)
def task_list(status: str | None = None, list_id: str | None = None, tag: str | None = None,
              priority: int | None = None, include_deleted: bool = False,
              parent_id: str | None = None,
              sort: str = "smart", limit: int = 0) -> dict:
    """按条件取任务。视图（今天/近一周等）是它的上层封装，不另存数据副本。"""
    if status and status not in VALID_STATUS:
        raise validation_error("status 非法", allowed=sorted(VALID_STATUS), got=status)
    items = ctx.need_store().list_items(kind="task", include_deleted=include_deleted)
    out = [it for it in items
           if (not status or it.data.get("status") == status)
           and (not list_id or it.data.get("list_id") == list_id)
           and (not parent_id or it.data.get("parent_id") == parent_id)
           and (not tag or tag in (it.data.get("tags") or []))
           and (priority is None or int(it.data.get("priority", 4)) == int(priority))]
    if sort == "smart":
        out.sort(key=_smart_key)
    elif sort == "updated":
        out.sort(key=lambda it: it.rev, reverse=True)
    elif sort == "title":
        out.sort(key=lambda it: it.data.get("title", ""))
    if limit:
        out = out[: int(limit)]
    return {"total": len(out), "items": [it.to_dict() for it in out]}


@feature(name="task.update", group="task", http=("PATCH", "/api/v1/tasks/{item_id}"), undoable=True)
def task_update(item_id: str, **changes: Any) -> dict:
    """局部修改。每次调用都发一个新 rev —— 这是同步能识别「谁更新」的基础。"""
    old = _load(item_id, allow_deleted=False)
    unknown = set(changes) - _TASK_FIELDS
    if unknown:
        raise validation_error("不支持的字段", fields=sorted(unknown))
    if not changes:
        raise validation_error("没有要修改的字段")
    data = dict(old.data)
    if "priority" in changes:
        changes["priority"] = _validate_priority(changes["priority"])
    if "status" in changes and changes["status"] not in VALID_STATUS:
        raise validation_error("status 非法", allowed=sorted(VALID_STATUS), got=changes["status"])
    if "repeat" in changes:
        changes["repeat"] = _validate_repeat(changes["repeat"])   # 曾经原样收下任意串
    data.update(changes)
    if any(key in changes and changes[key] != old.data.get(key) for key in ('due_at','repeat','remind_at')):
        data.pop('snoozed_until',None)
    if "list_id" in changes and changes["list_id"] != old.data.get("list_id") and "section_id" not in changes:
        data["section_id"] = None
    _validate_task_data(data, item_id)
    if (("repeat" in changes and changes["repeat"] != old.data.get("repeat")) or
            ("due_at" in changes and changes["due_at"] != old.data.get("due_at"))):
        # 显式改重复/改到期日 = 用户重新指定「从哪天开始」，所以重新下锚
        data["repeat_start"] = _anchor_for(data.get("repeat"), data.get("due_at"))
        _align_workday_start(data)
    if "done_at" in changes and changes["done_at"] is None and changes.get("status") == "done":
        data["done_at"] = stamp.now_text()
    return _bump(old, data).to_dict()


def _fmt_due_like(original: Any, dt: datetime) -> str:
    """保持到期值原来的**形状**：日期型仍是日期型。

    全天任务被推进成 `2026-11-07T00:00:00` 会在界面上多出个「00:00」，
    并且让 `due_all_day` 与 `due_at` 自相矛盾。
    """
    if original and "T" not in str(original):
        return dt.date().isoformat()
    return dt.replace(microsecond=0).isoformat()


def _align_workday_start(data: dict) -> None:
    """Apply filtering to an explicitly chosen start, retaining the nominal anchor."""
    rule = rrule.try_parse(data.get("repeat"))
    if not rule or not rule.workday:
        return
    due = parse_dt(data.get("due_at"))
    if due is None:
        raise validation_error("工作日规则需要到期日期")
    try:
        first = next(rrule.occurrences(data.get("repeat_start") or due, rule), None)
    except rrule.CalendarError as error:
        raise validation_error(str(error), key="calendar_missing_year", year=error.year,
                               calendar_id=error.calendar_id) from None
    if first is None:
        raise validation_error("结束日期之前没有符合规则的工作日")
    effective = due.replace(year=first.year, month=first.month, day=first.day)
    delta = effective - due
    data["due_at"] = _fmt_due_like(data.get("due_at"), effective)
    if delta:
        data["remind_at"] = [_fmt_due_like(value, parsed + delta)
                             for value in data.get("remind_at", [])
                             if (parsed := parse_dt(value)) is not None]


def _repeat_roll(old: Item, kind: str) -> tuple[dict, dict]:
    """把重复任务推进一次。返回 (新的 data, 附带的机器键信息)。

    `kind`: `done`（完成这一次）| `skip`（跳过这一次，不算完成）。

    语义与滴答/Todoist 一致：完成重复任务**不是把任务关掉**，而是把「当前这一次」
    记进本地历史、并把 Item 的 `due_at` 推到下一次、状态回到待办。所以重复任务
    不会堆在「已完成」里 —— 历史由 `task.occurrences` 提供。
    """
    data = dict(old.data)
    now_text = stamp.now_text()
    rule = rrule.try_parse(data.get("repeat"))
    due = parse_dt(data.get("due_at"))
    extra: dict[str, Any] = {"rolled": False, "kind": kind}

    if rule is None or due is None:
        if rule is None and "X-QD-" in str(data.get("repeat", "")).upper():
            raise validation_error("当前客户端不支持该工作日规则或日历版本，请更新后再推进")
        # 推不动就把原因说出来，绝不静默当普通完成
        if data.get("repeat") and rule is None:
            extra["repeat_blocked_key"] = "repeat_rule_invalid"   # 另一台机器同步来的畸形规则
        elif rule is not None and due is None:
            extra["repeat_blocked_key"] = "repeat_needs_due"      # 设了重复但没有到期日
        if kind == "done":
            data.update(status="done", done_at=now_text)
        return data, extra

    anchor = parse_dt(data.get("repeat_start")) or due
    try:
        nxt = rrule.next_after(due, rule, anchor=anchor)
    except rrule.CalendarError as error:
        raise validation_error(str(error), key="calendar_missing_year", year=error.year,
                               calendar_id=error.calendar_id) from None
    prev_due = str(data.get("due_at"))
    ctx.need_store().occurrence_add(old.id, prev_due, now_text,
                                    "skipped" if kind == "skip" else "done")
    if nxt is None:
        # UNTIL / COUNT 到头：这一次就是最后一次，正常收尾
        if kind == "done":
            data.update(status="done", done_at=now_text)
        extra.update(repeat_ended=True, repeat_ended_key="repeat_ended")
        return data, extra

    delta = nxt - due
    data["due_at"] = _fmt_due_like(old.data.get("due_at"), nxt)
    if kind == "done":
        data["status"] = "todo"        # 完成的是上一次，任务本身继续
        data["done_at"] = None
    data.pop("snoozed_until", None)    # 贪睡只针对那一次，推进后必须失效
    # 提醒按同一位移平移，保住用户设的提前量 —— 否则任务滚到明天、提醒留在昨天。
    rem = [t for t in (parse_dt(x) for x in (data.get("remind_at") or [])) if t]
    data["remind_at"] = [_fmt_due_like(old.data.get("due_at"), t + delta) for t in rem]
    extra.update(rolled=True, next_due_at=data["due_at"], occurrence=prev_due)
    return data, extra


@feature(name="task.complete", group="task", http=("POST", "/api/v1/tasks/{item_id}/complete"),
         undoable=True)
def task_complete(item_id: str) -> dict:
    old = _load(item_id, allow_deleted=False)
    data, extra = _repeat_roll(old, "done")
    out = _bump(old, data).to_dict()
    out["repeat_roll"] = extra
    return out


@feature(name="task.skip", group="task", http=("POST", "/api/v1/tasks/{item_id}/skip"),
         undoable=True)
def task_skip(item_id: str) -> dict:
    """跳过这一次：不算完成，直接把到期日推到下一次（记进本地历史，kind=skipped）。

    没有重复规则就明确报错，而不是当什么都没发生 —— 静默无反应比报错更糟。
    """
    old = _load(item_id, allow_deleted=False)
    if not old.data.get("repeat"):
        raise validation_error("该任务没有重复规则，没有「下一次」可以跳过", item_id=item_id)
    data, extra = _repeat_roll(old, "skip")
    out = _bump(old, data).to_dict()
    out["repeat_roll"] = extra
    return out


@feature(name="task.repeat_info", group="task",
         http=("GET", "/api/v1/tasks/{item_id}/repeat"), idempotent=True)
def task_repeat_info(item_id: str, count: int = 5) -> dict:
    """重复规则的**后端拆解** + 未来几次 + 本地历史统计。

    前端不自己解析 RRULE、也不自己算下一次（那是把业务判断复制到第二处，
    和「已逾期硬编码进响应体」是同一类泄漏），只翻译这里给的机器键。
    """
    it = _load(item_id)
    rule = rrule.try_parse(it.data.get("repeat"))
    due = parse_dt(it.data.get("due_at"))
    n = max(0, min(int(count), 50))
    warnings: list[dict] = []
    upcoming = rrule.upcoming(due, rule, n, anchor=it.data.get("repeat_start"), warnings=warnings) if (rule and due) else []
    out: dict[str, Any] = {
        "item_id": it.id,
        "repeat": it.data.get("repeat"),
        "summary": rrule.summary(rule) if rule else {},
        "due_at": it.data.get("due_at"),
        "repeat_start": it.data.get("repeat_start"),
        "upcoming": [_fmt_due_like(it.data.get("due_at"), d) for d in upcoming],
        "warnings": warnings,
        "history": ctx.need_store().occurrence_count(it.id),
        "synced": bool(it.data.get("repeat")),   # 规则本身是 Item 字段，参与同步
        "history_synced": False,                 # 完成历史是设备本地的，永不进同步
    }
    if it.data.get("repeat") and rule is None:
        out["invalid_rule_key"] = "repeat_rule_invalid"
    elif rule is not None and due is None:
        out["needs_due_key"] = "repeat_needs_due"
    return out


@feature(name="task.occurrences", group="task",
         http=("GET", "/api/v1/tasks/{item_id}/occurrences"), idempotent=True)
def task_occurrences(item_id: str, limit: int = 100) -> dict:
    """某任务已完成/已跳过的那些次。设备本地数据，**不同步**。"""
    _load(item_id)          # 不存在就 404，别对着空气返回空列表
    rows = ctx.need_store().occurrence_list(item_id, limit=max(1, min(int(limit), 500)))
    return {"item_id": item_id, "total": len(rows), "occurrences": rows, "synced": False}



@feature(name="task.delete", group="task", http=("DELETE", "/api/v1/tasks/{item_id}"), undoable=True)
def task_delete(item_id: str) -> dict:
    """软删 = 写墓碑版本，**不是删文件**。

    删除因此变成一个可推送、可拉取、可参与 LWW 比较的普通版本变更，
    「清单里删了但 git 里没删」在协议层不可能长期存在。
    """
    old = _load(item_id)
    if old.deleted:
        return old.to_dict()
    return _bump(old, deleted=True).to_dict()


@feature(name="task.restore", group="task", http=("POST", "/api/v1/tasks/{item_id}/restore"),
         undoable=True)
def task_restore(item_id: str) -> dict:
    """恢复 = 再写一个 deleted=false 的新版本（同样参与同步）。"""
    old = _load(item_id)
    if not old.deleted:
        return old.to_dict()
    return _bump(old, deleted=False).to_dict()


# --------------------------------------------------------------------------- 分组


@feature(name="group.create", group="group", http=("POST", "/api/v1/groups"), undoable=True)
def group_create(name: str, color: str | None = None, folder_id: str | None = None,
                 order: int = 0, icon: str = "") -> dict:
    """新建分组。UI 叫「分组」，内部 kind 仍是 list，仓库路径 items/list/ 不变。"""
    if not name or not name.strip():
        raise validation_error("分组名不能为空")
    data = {"name": name.strip(), "color": color, "folder_id": folder_id,
            "order": int(order), "archived": False, "icon": check_icon(icon)}
    return _create("list", data).to_dict()


@feature(name="group.list", group="group", http=("GET", "/api/v1/groups"), idempotent=True)
def group_list(include_deleted: bool = False, limit: int | None = 5,
               offset: int = 0, q: str = "") -> dict:
    return ctx.need_store().catalog_page("list", limit, offset, q, include_deleted)


@feature(name="group.get", group="group", http=("GET", "/api/v1/groups/{item_id}"), idempotent=True)
def group_get(item_id: str) -> dict:
    item = ctx.need_store().get(item_id)
    if item is None or item.deleted or item.kind != "list":
        raise not_found(f"清单 {item_id}")
    return item.to_dict()


@feature(name="group.delete", group="group", http=("DELETE", "/api/v1/groups/{item_id}"),
         undoable=True)
def group_delete(item_id: str) -> dict:
    """删分组：写分组墓碑，并把其下任务的 list_id/section_id 显式清掉转收集箱。

    只删分组不清引用会留下悬空 list_id —— 任务既不在原分组（已删）也不进收集箱
    （收集箱判定的是 list_id 为空），等于凭空消失。
    """
    st = ctx.need_store()
    it = st.get(item_id)
    if it is None or it.kind != "list":
        raise not_found(f"分组 {item_id}")
    moved = 0
    for task in st.list_items(kind="task"):
        if task.data.get("list_id") == item_id:
            _bump(task, {**task.data, "list_id": None, "section_id": None})
            moved += 1
    out = _bump(it, deleted=True).to_dict()
    out["moved_to_inbox"] = moved
    return out


@feature(name="group.update", group="group", http=("PATCH", "/api/v1/groups/{item_id}"),
         undoable=True)
def group_update(item_id: str, name: str | None = None, color: str | None = None,
                 folder_id: str | None = None, order: int | None = None,
                 archived: bool | None = None, emoji: str | None = None, icon: str | None = None) -> dict:
    st = ctx.need_store()
    it = st.get(item_id)
    if it is None or it.kind != "list":
        raise not_found(f"分组 {item_id}")
    data = dict(it.data)
    if icon is not None: data['icon'] = check_icon(icon)
    for k, v in (("name", name), ("color", color), ("folder_id", folder_id),
                 ("order", order), ("archived", archived), ("emoji", emoji)):
        if v is not None:
            data[k] = v
    if "name" in data and not str(data["name"]).strip():
        raise validation_error("分组名不能为空")
    return _bump(it, data).to_dict()


@feature(name="group.archive", group="group", http=("POST", "/api/v1/groups/{item_id}/archive"),
         undoable=True)
def group_archive(item_id: str, archived: bool = True) -> dict:
    st = ctx.need_store()
    it = st.get(item_id)
    if it is None or it.kind != "list":
        raise not_found(f"分组 {item_id}")
    return _bump(it, {**it.data, "archived": bool(archived)}).to_dict()


# --------------------------------------------------------------------------- 任务细粒度动作


@feature(name="task.reopen", group="task", http=("POST", "/api/v1/tasks/{item_id}/reopen"),
         undoable=True)
def task_reopen(item_id: str) -> dict:
    old = _load(item_id, allow_deleted=False)
    data = dict(old.data)
    data.update(status="todo", done_at=None)
    return _bump(old, data).to_dict()


@feature(name="task.set_priority", group="task", http=("POST", "/api/v1/tasks/{item_id}/priority"),
         undoable=True)
def task_set_priority(item_id: str, priority: int) -> dict:
    old = _load(item_id, allow_deleted=False)
    return _bump(old, {**old.data, "priority": _validate_priority(priority)}).to_dict()


@feature(name="task.set_color", group="task", http=("POST", "/api/v1/tasks/{item_id}/color"),
         undoable=True)
def task_set_color(item_id: str, color: str | None = None) -> dict:
    """任务自定义色，三级覆盖里最高优先级（任务色 > 分组色 > 优先级色）。传 null 清除。"""
    old = _load(item_id, allow_deleted=False)
    if color is not None:
        c = str(color).strip()
        if c and not re.fullmatch(r"#?[0-9a-fA-F]{6}", c):
            raise validation_error("color 必须是 #RRGGBB", got=color)
        color = c if not c or c.startswith("#") else f"#{c}"
    return _bump(old, {**old.data, "color": color or None}).to_dict()


@feature(name="task.set_due", group="task", http=("POST", "/api/v1/tasks/{item_id}/due"),
         undoable=True)
def task_set_due(item_id: str, due_at: str | None = None, all_day: bool = False) -> dict:
    old = _load(item_id, allow_deleted=False)
    if due_at:
        if parse_dt(due_at) is None:
            raise validation_error("due_at 必须是 ISO8601，如 2026-11-06 或 2026-11-06T20:00:00",
                                   got=due_at)
    data = {**old.data, "due_at": due_at or None,
            "due_all_day": bool(all_day) if due_at else True}
    if data['due_at'] != old.data.get('due_at'):
        data.pop('snoozed_until',None)
    # 这是独立接口、不经过 task.update，所以锚点必须在这里也维护一遍 ——
    # 漏掉的话「改到期日」后 COUNT 仍从旧锚数，用户看到的剩余次数是错的。
    data["repeat_start"] = _anchor_for(data.get("repeat"), data.get("due_at"))
    _align_workday_start(data)
    return _bump(old, data).to_dict()


@feature(name="task.set_repeat", group="task", http=("POST", "/api/v1/tasks/{item_id}/repeat"),
         undoable=True)
def task_set_repeat(item_id: str, repeat: str | None = None) -> dict:
    """重复规则只存一个 Item + RRULE/规范值，视图层按需展开，不预生成实例。

    开启重复 = 用户指定「从当前到期日开始循环」，所以在这里重新下锚（`repeat_start`）；
    自动推进时不动锚，否则 COUNT 永远数不到头。
    """
    old = _load(item_id, allow_deleted=False)
    canon = _validate_repeat(repeat)
    data = {**old.data, "repeat": canon, "repeat_start": _anchor_for(canon, old.data.get("due_at"))}
    if canon != old.data.get('repeat'):
        data.pop('snoozed_until',None)
    _align_workday_start(data)
    out = _bump(old, data).to_dict()
    if canon:
        rule = rrule.try_parse(canon)
        due = parse_dt(data.get("due_at"))
        out["summary"] = rrule.summary(rule)
        warnings: list[dict] = []
        dates = rrule.upcoming(due, rule, 1, anchor=data["repeat_start"], warnings=warnings) if (rule and due) else []
        nxt = dates[0] if dates else None
        out["warnings"] = warnings
        out["next_due_at"] = _fmt_due_like(data.get("due_at"), nxt) if nxt else None
        if not due:
            out["needs_due_key"] = "repeat_needs_due"
    return out


@feature(name="task.repeat_preview", group="task", http=("POST", "/api/v1/tasks/repeat-preview"),
         idempotent=True)
def task_repeat_preview(repeat: str, due_at: str, count: int = 5) -> dict:
    """校验并预览周期规则，不创建任务、不改变版本。"""
    canon = _validate_repeat(repeat)
    due = parse_dt(due_at)
    if not canon or due is None or not 1 <= count <= 20:
        raise validation_error("需提供有效周期、到期日期和 1..20 的预览条数")
    rule = rrule.parse(canon)
    data = {"due_at": due_at, "repeat": canon, "repeat_start": due_at}
    _align_workday_start(data)
    effective = parse_dt(data["due_at"])
    warnings: list[dict] = []
    dates = rrule.upcoming(effective, rule, count, anchor=due_at, warnings=warnings)
    return {"repeat": canon, "summary": rrule.summary(rule),
            "first_due_at": data["due_at"], "warnings": warnings,
            "calendar": workdays.metadata(rule.calendar_id) if rule.workday else None,
            "upcoming": [_fmt_due_like(due_at, dt) for dt in dates]}


@feature(name="task.set_reminder", group="task", http=("POST", "/api/v1/tasks/{item_id}/reminder"),
         undoable=True)
def task_set_reminder(item_id: str, remind_at: list | None = None, mode: str = "once",
                      channels: list | None = None) -> dict:
    """设置提醒时间。v1 上限 3 个；mode=sticky 为持续提醒；channels=null 跟随全局默认。"""
    old = _load(item_id, allow_deleted=False)
    if mode not in {"once", "sticky"}:
        raise validation_error("mode 只能是 once|sticky", got=mode)
    times = list(remind_at or [])
    if len(times) > 3:
        raise validation_error("提醒最多 3 个", got=len(times))
    for t in times:
        if parse_dt(t) is None:
            raise validation_error("remind_at 必须是 ISO8601", got=t)
    if channels is not None:
        bad = set(channels) - {"toast", "dingtalk", "email"}
        if bad:
            raise validation_error("未知告警渠道", allowed=["toast", "dingtalk", "email"],
                                   got=sorted(bad))
    data = {**old.data, "remind_at": times, "remind_mode": mode, "alert_channels": channels}
    data.pop('snoozed_until',None)
    return _bump(old,data).to_dict()


@feature(name="task.move", group="task", http=("POST", "/api/v1/tasks/{item_id}/move"),
         undoable=True)
def task_move(item_id: str, list_id: str | None = None, section_id: str | None = None,
              parent_id: str | None = None, to_inbox: bool = False) -> dict:
    """跨分组移动 / 改父级。to_inbox=true 清空 list_id。"""
    old = _load(item_id, allow_deleted=False)
    st = ctx.need_store()
    for key, value in (("list_id", list_id), ("section_id", section_id), ("parent_id", parent_id)):
        if value:
            target = st.get(value)
            if target is None or target.deleted:
                raise not_found(f"{key} 指向的 {value}")
            expect = {"list_id": "list", "section_id": "section", "parent_id": "task"}[key]
            if target.kind != expect:
                raise validation_error(f"{key} 必须指向 {expect}", got=target.kind)
    if parent_id == item_id:
        raise validation_error("不能把任务设为自己的子任务")
    ancestor = parent_id
    seen = {item_id}
    while ancestor:
        if ancestor in seen:
            raise validation_error("父任务关系不能形成循环")
        seen.add(ancestor)
        parent = st.get(ancestor)
        ancestor = parent.data.get("parent_id") if parent else None
    data = dict(old.data)
    destination = list_id if list_id is not None else data.get("list_id")
    if section_id and not to_inbox:
        section = st.get(section_id)
        if section.data.get("list_id") != destination:
            raise validation_error("小节不属于目标清单")
    if list_id is not None and list_id != data.get("list_id"):
        data["section_id"] = None
    if to_inbox:
        data["list_id"] = None
        data["section_id"] = None
    for key, value in (("list_id", list_id), ("section_id", section_id), ("parent_id", parent_id)):
        if value is not None and not to_inbox:
            data[key] = value
    return _bump(old, data).to_dict()


@feature(name="task.reorder", group="task", http=("POST", "/api/v1/tasks/{item_id}/reorder"),
         undoable=True)
def task_reorder(item_id: str, order: int, after: str | None = None) -> dict:
    old = _load(item_id, allow_deleted=False)
    return _bump(old, {**old.data, "order": int(order)}).to_dict()


@feature(name="task.snooze", group="task", http=("POST", "/api/v1/tasks/{item_id}/snooze"),
         undoable=True)
def task_snooze(item_id: str, minutes: int = 10) -> dict:
    """推迟提醒，**不改到期日** —— 到期日是事实，推迟只是本地的一次让步。"""
    old = _load(item_id, allow_deleted=False)
    if int(minutes) <= 0 or int(minutes) > 24 * 60:
        raise validation_error("minutes 需在 1..1440", got=minutes)
    when = stamp.now_dt() + timedelta(minutes=int(minutes))
    data = dict(old.data)
    data["snoozed_until"] = when.isoformat(timespec="seconds")
    return _bump(old, data).to_dict()


# --------------------------------------------------------------------------- 备注


@feature(name="note.append", group="note", http=("POST", "/api/v1/tasks/{item_id}/notes"),
         undoable=True)
def note_append(item_id: str, text: str) -> dict:
    """备注是独立 Item（parent_id 指向任务），不用内嵌数组 —— 数组无法三方合并。"""
    _load(item_id, allow_deleted=False)
    if not text or not text.strip():
        raise validation_error("备注不能为空")
    return _create("note", {"parent_id": item_id, "text": text.strip(),
                            "created_at": stamp.now_text()}).to_dict()


@feature(name="note.list", group="note", http=("GET", "/api/v1/tasks/{item_id}/notes"),
         idempotent=True)
def note_list(item_id: str) -> dict:
    items = [it for it in ctx.need_store().list_items(kind="note")
             if it.data.get("parent_id") == item_id]
    items.sort(key=lambda it: it.data.get("created_at") or "")
    return {"total": len(items), "items": [it.to_dict() for it in items]}


@feature(name="note.delete", group="note", http=("DELETE", "/api/v1/notes/{item_id}"),
         undoable=True)
def note_delete(item_id: str) -> dict:
    st = ctx.need_store()
    it = st.get(item_id)
    if it is None or it.kind != "note":
        raise not_found(f"备注 {item_id}")
    return _bump(it, deleted=True).to_dict()


# --------------------------------------------------------------------------- 搜索与撤销


@feature(name="search.query", group="search", http=("GET", "/api/v1/search"), idempotent=True)
def search_query(q: str, kinds: list | None = None, include_deleted: bool = False,
                 limit: int = 200, offset: int = 0) -> dict:
    """中文子串搜索用 LIKE。FTS5 的 trigram 分词按 3 字符建索引，
    「立案」「周报」这类两字词命中恒为 0（spike/fts_probe.py 实测），故不采用。"""
    if not q or not q.strip():
        raise validation_error("q 不能为空")
    from qingdan.core import task_query
    where, args = ['(i.data LIKE ? OR i.meta LIKE ?)'], [f'%{q.strip()}%'] * 2
    if kinds:
        where.append('i.kind IN (' + ','.join('?' for _ in kinds) + ')')
        args.extend(kinds)
    if not include_deleted:
        where.append('i.deleted=0')
    items, page = task_query.select(ctx.need_store(), where, args, limit=limit, offset=offset,
                                   order='i.kind,i.id')
    return {**page, "items": [it.to_dict() for it in items]}


@feature(name="undo.last", group="undo", http=("POST", "/api/v1/undo"), undoable=False)
def undo_last() -> dict:
    """撤销 = 用前像**再写一个新版本**，不是回滚历史。

    直接改回旧 rev 会让两台机器看到相同版本戳，LWW 立刻失效。
    """
    st = ctx.need_store()
    op = st.last_open_op()
    if op is None:
        raise not_found("没有可撤销的操作")
    restored = []
    for entry in op["items"]:
        before = entry["before"]
        current = st.get(entry["item_id"])
        if current is None:
            continue
        if before is None:                       # 当初是新建 → 撤销即写墓碑
            _bump(current, deleted=True)
        else:
            old = Item.from_payload(before)
            # 撤销一次「完成并推进」时，把那条完成历史一并撤掉：前像里的 due_at 正是
            # 被记进 occurrence 的那一次。其它情形（改标题等）前像的 due_at 是当前这一次、
            # 尚未完成，删不到任何行，所以这个钩子不会误伤。
            if old.data.get("repeat") and old.data.get("due_at"):
                st.occurrence_delete(entry["item_id"], str(old.data["due_at"]))
            _bump(current, data=old.data, deleted=old.deleted)
        restored.append(entry["item_id"])
    st.mark_op_undone(op["id"])
    return {"undone": op["feature"], "items": restored, "remaining": _open_op_count()}


def _open_op_count() -> int:
    row = ctx.need_store().db.execute("SELECT COUNT(*) c FROM ops WHERE undone = 0").fetchone()
    return int(row["c"])
