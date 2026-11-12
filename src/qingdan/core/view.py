"""视图与查询逻辑（纯函数，不碰数据库、不注册功能）。

视图是**查询定义，不是数据副本**：切换视图不复制任务，所以分组/排序不可能丢任务。
`today` 一律由调用方注入（YYYY-MM-DD 字符串），跨午夜与跨时区的边界才能被测试覆盖。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from qingdan.core import rrule
from qingdan.core.models import Item

SMART_DUE_MAX = "9999-12-31T23:59:59"


# --------------------------------------------------------------------------- 时间


def parse_dt(value: Any) -> datetime | None:
    """解析 ISO8601。带时区的统一折算到本地墙钟（与显示一致），naive 原样用。"""
    if not value:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        s = str(value).strip().replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(s)
        except ValueError:
            return None
    if dt.tzinfo is not None:
        dt = dt.astimezone().replace(tzinfo=None)
    return dt


def to_date(value: Any) -> date | None:
    dt = parse_dt(value)
    return dt.date() if dt else None


def as_date(value: Any) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    d = to_date(value)
    if d is None:
        raise ValueError(f"无法解析日期：{value!r}")
    return d


def days_between(target: Any, today: date) -> int | None:
    """日历天差。不能用 round(毫秒差/86400000) —— 那个算法会把「今天 20:00」
    算进明天、把「逾期 6 小时」算进今天（原型阶段实测踩过）。"""
    d = to_date(target)
    return None if d is None else (d - today).days


# --------------------------------------------------------------------------- 谓词


def is_alive(it: Item) -> bool:
    return not it.deleted


def is_open(it: Item) -> bool:
    return is_alive(it) and it.data.get("status") not in ("done", "cancelled")


def is_done(it: Item) -> bool:
    return is_alive(it) and it.data.get("status") == "done"


# --------------------------------------------------------------------------- 内置视图

VIEW_NAMES = ("today", "next7", "inbox", "done", "trash", "all")


def apply_view(name: str, items: list[Item], today: date | str,
               include_done: bool = False) -> list[Item]:
    """五个内置视图的精确语义（开发文档 §5.2）。"""
    d = as_date(today)
    end = d + timedelta(days=6)

    if name == "today":
        out: list[Item] = []
        for it in items:
            if not is_alive(it):
                continue
            if not is_open(it):
                if include_done and it.data.get("status") == "done" and _due_le(it, d):
                    out.append(it)          # 已完成任务当日仍显示在底部折叠区
                continue
            if _due_le(it, d):              # 含逾期
                out.append(it)
        return out

    if name == "next7":
        out = []
        for it in items:
            if not is_alive(it):
                continue
            if not is_open(it):
                if include_done and it.data.get("status") == "done" and _due_le(it, end):
                    out.append(it)
                continue
            if _due_le(it, end):
                out.append(it)
        return out

    if name == "inbox":
        return [it for it in items
                if is_alive(it) and not it.data.get("list_id")
                and (include_done or is_open(it))]

    if name == "done":
        out = [it for it in items if is_done(it)]
        out.sort(key=lambda it: it.data.get("done_at") or "", reverse=True)
        return out

    if name == "trash":
        out = [it for it in items if it.deleted]
        out.sort(key=lambda it: it.deleted_at_ms or 0, reverse=True)
        return out

    if name == "all":
        return [it for it in items if is_alive(it)]

    raise ValueError(f"未知视图：{name}（允许 {list(VIEW_NAMES)}）")


def _due_le(it: Item, d: date) -> bool:
    """到期日 <= d，含逾期。无到期日的任务**不出现**在时间视图里 —— 这条规则
    决定「今天」视图是否可信，滴答与 Todoist 都这么做。"""
    dd = to_date(it.data.get("due_at"))
    return dd is not None and dd <= d


# --------------------------------------------------------------------------- 分组

GROUP_MODES = ("none", "date", "group", "priority", "tag", "status", "created")

#: 分组标题一律返回**机器键**，由前端 i18n 翻译。
#: 后端返回中文标签会让「切换语言」在英文界面上留下「已逾期 / 待办」这种残渣（实测踩过）。
#: 用户数据（分组名、标签名）不是文案，保持原样返回，永不翻译。
_DATE_KEYS = ["sec_overdue", "sec_today", "sec_tomorrow", "sec_week",
              "sec_later", "sec_none", "sec_done"]
_STATUS_KEYS = {"todo": "st_todo", "doing": "st_doing", "done": "st_done",
                "cancelled": "st_cancelled"}
_PRIORITY_KEYS = {1: "prio_1", 2: "prio_2", 3: "prio_3", 4: "prio_4"}


def bucket(key: str, items: list[Item], **extra) -> dict[str, Any]:
    """一个分组桶。label_key 非空表示标题是 UI 文案，需要前端翻译。"""
    return {"key": key, "label_key": key, "label": None, "items": items, **extra}


def data_bucket(name: str, items: list[Item], key: str | None = None) -> dict[str, Any]:
    """标题来自用户数据（分组名/标签名），不参与翻译。"""
    return {"key": key or name, "label_key": None, "label": name, "items": items}


def group_items(items: list[Item], mode: str, today: date | str,
                name_of: dict[str, str] | None = None) -> list[dict[str, Any]]:
    """分组只影响呈现，不改变集合 —— 测试断言「任意分组下总数 == 不分组总数」。"""
    d = as_date(today)
    name_of = name_of or {}
    if mode == "none":
        return [data_bucket("", items, key="")]

    if mode == "date":
        buckets: list[list[Item]] = [[] for _ in _DATE_KEYS]
        for it in items:
            if it.data.get("status") in ("done", "cancelled"):
                buckets[6].append(it)
                continue
            n = days_between(it.data.get("due_at"), d)
            if n is None:
                buckets[5].append(it)
            elif n < 0:
                buckets[0].append(it)
            elif n == 0:
                buckets[1].append(it)
            elif n == 1:
                buckets[2].append(it)
            elif n <= 6:
                buckets[3].append(it)
            else:
                buckets[4].append(it)
        return [bucket(k, b) for k, b in zip(_DATE_KEYS, buckets) if b]

    if mode == "group":
        keys: list[str] = []
        out: dict[str, list[Item]] = {}
        for it in items:
            lid = it.data.get("list_id")
            name = name_of.get(lid) if lid else None
            ck = lid or "inbox"
            if ck not in out:
                out[ck] = []
                keys.append(ck)
            out[ck].append(it)
        return [bucket("inbox", out["inbox"]) if k == "inbox"
                else data_bucket(name_of.get(k, ""), out[k], key=k)
                for k in keys]

    if mode == "priority":
        return [bucket(_PRIORITY_KEYS[p],
                       [it for it in items if int(it.data.get("priority", 4)) == p])
                for p in (1, 2, 3, 4)]

    if mode == "tag":
        labels: list[str] = []
        for it in items:
            for tg in (it.data.get("tags") or []):
                if tg not in labels:
                    labels.append(tg)
        out = [data_bucket(tg, [it for it in items if tg in (it.data.get("tags") or [])])
               for tg in labels]
        untagged = [it for it in items if not (it.data.get("tags") or [])]
        if untagged:
            out.append(bucket("untagged", untagged))
        return out

    if mode == "status":
        return [bucket(_STATUS_KEYS[st], [it for it in items if it.data.get("status") == st])
                for st in _STATUS_KEYS]

    if mode == "created":
        days: dict[str, list[Item]] = {}
        for it in items:
            dd = to_date(it.data.get("created_at"))
            days.setdefault(dd.isoformat() if dd else "unknown", []).append(it)
        return [data_bucket(k, v) for k, v in sorted(days.items(), reverse=True)]

    raise ValueError(f"未知分组方式：{mode}（允许 {list(GROUP_MODES)}）")


# --------------------------------------------------------------------------- 排序

SORT_MODES = ("smart", "due", "priority", "title", "created", "updated", "manual")


def sort_items(items: list[Item], mode: str = "smart", reverse: bool = False) -> list[Item]:
    if mode == "smart":
        # Todoist 智能序：日期时间 → 优先级 → 截止期限 → 手动序 → 创建时间
        key = lambda it: (it.data.get("due_at") or SMART_DUE_MAX,
                          int(it.data.get("priority", 4)),
                          it.data.get("due_at") is None,
                          int(it.data.get("order", 0)),
                          it.data.get("created_at") or "", it.id)
    elif mode == "due":
        key = lambda it: (it.data.get("due_at") or SMART_DUE_MAX, it.id)
    elif mode == "priority":
        key = lambda it: (int(it.data.get("priority", 4)), it.id)
    elif mode == "title":
        key = lambda it: (str(it.data.get("title", "")), it.id)
    elif mode == "created":
        key = lambda it: (it.data.get("created_at") or "", it.id)
    elif mode == "updated":
        key = lambda it: (it.rev, it.id)
    elif mode == "manual":
        key = lambda it: (int(it.data.get("order", 0)), it.id)
    else:
        raise ValueError(f"未知排序方式：{mode}（允许 {list(SORT_MODES)}）")
    return sorted(items, key=key, reverse=reverse)


# --------------------------------------------------------------------------- 日历


def due_key(it: Item) -> Any:
    """日历/统计取「哪天出现」：任务看 due_at，倒数日看 target_date。

    两者字段名不同，如果日历只认 due_at，倒数日就永远画不出来。
    """
    return it.data.get("target_date") if it.kind == "countdown" else it.data.get("due_at")


def expand_recurring(items: list[Item], start: date | str, days: int, today: date | str,
                     cap: int = 400) -> dict[date, list[Item]]:
    """把重复任务在窗口内的**未来次数**展开成虚拟事件（只读：不落库、不预生成 Item）。

    三条边界，每条都有具体理由：

    1. **只展开未完成的任务** —— done/cancelled 不再产生新次数；
    2. **只展开「严格晚于当前 due_at 且不早于今天」的日期** —— 当前那一次由 Item 自己画；
       漏掉的过去几次由「逾期」表达，重复画只会把计数搞乱（用户会以为今天有三件事）；
    3. **总量封顶 + 畸形规则静默跳过** —— 另一台机器同步来的垃圾规则（`try_parse` 返回 None）
       不许把整个日历带崩，`FREQ=DAILY` × 370 天 × 一堆任务也不许把响应撑爆。
    """
    d0 = as_date(start)
    today_d = as_date(today)
    end = d0 + timedelta(days=max(0, int(days) - 1))
    out: dict[date, list[Item]] = {}
    used = 0
    for it in items:
        if it.kind != "task" or not is_open(it):
            continue
        rule = rrule.try_parse(it.data.get("repeat"))
        due = parse_dt(it.data.get("due_at"))
        if rule is None or due is None:
            continue
        due_d = due.date()
        for nxt in rrule.upcoming(due, rule, n=int(days) + 2,
                                  anchor=it.data.get("repeat_start"), end=end, warnings=[]):
            dd = nxt.date()
            if dd < d0 or dd < today_d or dd == due_d:
                continue
            if used >= int(cap):
                return out
            out.setdefault(dd, []).append(it)
            used += 1
    return out


def calendar_days(items: list[Item], start: date | str, days: int,
                  today: date | str, expand: bool = True) -> list[dict[str, Any]]:
    """把任务铺到日历格子里。跨天任务只出现在到期日那天（v1 不做跨天条带）。

    `expand=True` 时额外铺上重复任务的未来次数（虚拟事件，带 `occurrence: true`）。
    """
    d0 = as_date(start)
    d = as_date(today)
    virt = expand_recurring(items, d0, days, d) if expand else {}
    out: list[dict[str, Any]] = []
    for i in range(int(days)):
        cur = d0 + timedelta(days=i)
        evs = [it for it in items if is_alive(it) and to_date(due_key(it)) == cur]
        extra = virt.get(cur, [])
        events = [event_shape(it, cur, d) for it in sort_items(evs, "smart")]
        events += [event_shape(it, cur, d, occurrence=True) for it in extra]
        out.append({
            "date": cur.isoformat(),
            "in_month": cur.month == d0.month,
            "is_today": cur == d,
            "count": len(evs) + len(extra),
            "recurring": len(extra),
            "events": events,
        })
    return out


def event_shape(it: Item, cur: date, today: date, occurrence: bool = False) -> dict[str, Any]:
    return {
        "id": it.id,
        "kind": it.kind,
        "title": it.data.get("title") or it.data.get("name"),
        "priority": int(it.data.get("priority", 4)),
        "color": it.data.get("color"),
        "status": it.data.get("status"),
        "repeat": it.data.get("repeat"),
        # 虚拟次数不是数据副本：点开仍指向同一个 Item，只是标出来「这是重复展开的一次」
        "occurrence": bool(occurrence),
        "overdue": (not occurrence and it.data.get("status") not in ("done", "cancelled")
                    and cur < today),
    }


# --------------------------------------------------------------------------- 看板


def board_columns(items: list[Item], by: str, name_of: dict[str, str] | None = None,
                  today: date | str | None = None) -> list[dict[str, Any]]:
    name_of = name_of or {}
    if by == "status":
        cols = [(st, _STATUS_KEYS[st], None) for st in _STATUS_KEYS]
    elif by == "group":
        cols = [("inbox", "inbox", None) if lid == "inbox" else (lid, None, name_of.get(lid, ""))
                for lid in dict.fromkeys((it.data.get("list_id") or "inbox") for it in items)]
    elif by == "priority":
        cols = [(str(p), _PRIORITY_KEYS[p], None) for p in (1, 2, 3, 4)]
    else:
        raise ValueError("board 的 group_by 只能是 status/group/priority")
    out = []
    for value, label_key, label in cols:
        if by == "status":
            members = [it for it in items if it.data.get("status") == value]
        elif by == "group":
            members = [it for it in items if (it.data.get("list_id") or "inbox") == value]
        else:
            members = [it for it in items if str(int(it.data.get("priority", 4))) == value]
        out.append({"key": value, "label_key": label_key, "label": label,
                    "total": len(members),
                    "items": [it.to_dict() for it in sort_items(members, "smart")]})
    return out


# --------------------------------------------------------------------------- 自定义过滤器


def match_criteria(it: Item, crit: dict[str, Any], today: date | str) -> bool:
    """过滤器条件求值。空条件视为不限制。"""
    if not is_alive(it) or it.kind != "task":
        return False
    d = as_date(today)
    if "status" in crit and crit["status"] and it.data.get("status") != crit["status"]:
        return False
    if crit.get("open_only") and not is_open(it):
        return False
    if "list_id" in crit and crit["list_id"] and it.data.get("list_id") != crit["list_id"]:
        return False
    if "group_id" in crit and crit["group_id"] and it.data.get("list_id") != crit["group_id"]:
        return False
    if crit.get("no_group") and it.data.get("list_id"):
        return False
    if "tag" in crit and crit["tag"] and crit["tag"] not in (it.data.get("tags") or []):
        return False
    if "tags_any" in crit and crit["tags_any"]:
        if not set(crit["tags_any"]) & set(it.data.get("tags") or []):
            return False
    if crit.get("priority") and int(it.data.get("priority", 4)) != int(crit["priority"]):
        return False
    if crit.get("priority_max") and int(it.data.get("priority", 4)) > int(crit["priority_max"]):
        return False
    if "due_within_days" in crit and crit["due_within_days"] is not None:
        n = days_between(it.data.get("due_at"), d)
        if n is None or n > int(crit["due_within_days"]) or n < int(crit.get("due_from_days", -10 ** 6)):
            return False
    if crit.get("overdue"):
        n = days_between(it.data.get("due_at"), d)
        if n is None or n >= 0:
            return False
    if crit.get("has_due") and not it.data.get("due_at"):
        return False
    if crit.get("q"):
        q = str(crit["q"]).lower()
        hay = " ".join([str(it.data.get("title", "")), str(it.data.get("note", "")),
                        " ".join(map(str, it.data.get("tags") or []))]).lower()
        if q not in hay:
            return False
    return True
