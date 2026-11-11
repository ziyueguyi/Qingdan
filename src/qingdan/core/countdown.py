"""倒数日 / 纪念日（R8）。

独立 kind，参与同步。天数是**派生值**，每次查询按 today 现算，绝不落库 ——
存下来的「还剩几天」第二天就是错的。
"""
from __future__ import annotations


from qingdan.core import stamp, view
from qingdan.core.context import ctx
from qingdan.core.org import _check_color, _need_kind
from qingdan.core.registry import feature, validation_error
from qingdan.core.service import _bump, _create

COUNT_MODES = ("days", "weeks", "years")


def _shape(it, today: view.date) -> dict:
    d = dict(it.to_dict())
    data = it.data
    n = view.days_between(data.get("target_date"), today)
    mode = data.get("count_mode", "days")
    out = {"id": it.id, "title": data.get("title"), "target_date": data.get("target_date"),
            "color": data.get("color"), "repeat_yearly": bool(data.get("repeat_yearly")),
            "count_mode": mode, "days": n}
    # 只给 direction + days，不给 label：「还剩 3 天」是界面文案，归前端 i18n。
    # 这里原先写死中文，英文界面下倒计时会退回中文 —— 而前端本来就是自己拼的，
    # 那份 label 同时还是个没人用的字段。
    out["direction"] = None if n is None else ("left" if n > 0 else "today" if n == 0 else "past")
    if mode == "weeks" and n is not None:
        out["weeks"] = round(abs(n) / 7, 1)
    if mode == "years" and n is not None and n < 0:
        out["years"] = round(abs(n) / 365.25, 1)
    if data.get("show_age") and n is not None and n < 0:
        out["age"] = int(abs(n) // 365.25)
    out["data"] = data
    return out


@feature(name="countdown.create", group="countdown", http=("POST", "/api/v1/countdowns"),
         undoable=True)
def countdown_create(title: str, target_date: str, repeat_yearly: bool = False,
                     color: str | None = None, count_mode: str = "days",
                     include_target_day: bool = False, show_age: bool = False,
                     remind_days_before: int = 0, note: str = "") -> dict:
    if not title or not title.strip():
        raise validation_error("标题不能为空")
    if view.to_date(target_date) is None:
        raise validation_error("target_date 必须是 YYYY-MM-DD 或 ISO8601", got=target_date)
    if count_mode not in COUNT_MODES:
        raise validation_error("count_mode 非法", allowed=list(COUNT_MODES), got=count_mode)
    if int(remind_days_before) < 0 or int(remind_days_before) > 365:
        raise validation_error("remind_days_before 需在 0..365", got=remind_days_before)
    return _create("countdown", {
        "title": title.strip(), "target_date": str(target_date),
        "repeat_yearly": bool(repeat_yearly), "color": _check_color(color),
        "count_mode": count_mode, "include_target_day": bool(include_target_day),
        "show_age": bool(show_age), "remind_days_before": int(remind_days_before),
        "note": note, "created_at": stamp.now_text(),
    }).to_dict()


@feature(name="countdown.list", group="countdown", http=("GET", "/api/v1/countdowns"),
         idempotent=True)
def countdown_list(today: str | None = None, include_deleted: bool = False,
                   sort: str = "days") -> dict:
    d = view.as_date(today) if today else view.as_date(stamp.now_day())
    items = ctx.need_store().list_items(kind="countdown", include_deleted=include_deleted)
    shapes = [_shape(it, d) for it in items]
    if sort == "days":
        shapes.sort(key=lambda x: (x["days"] is None, abs(x["days"] or 0)))
    elif sort == "title":
        shapes.sort(key=lambda x: x["title"] or "")
    elif sort == "created":
        shapes.sort(key=lambda x: x["data"].get("created_at") or "")
    else:
        raise validation_error("sort 非法", allowed=["days", "title", "created"], got=sort)
    return {"today": d.isoformat(), "total": len(shapes), "items": shapes}


@feature(name="countdown.update", group="countdown", http=("PATCH", "/api/v1/countdowns/{item_id}"),
         undoable=True)
def countdown_update(item_id: str, title: str | None = None, target_date: str | None = None,
                     repeat_yearly: bool | None = None, color: str | None = None,
                     count_mode: str | None = None, show_age: bool | None = None,
                     remind_days_before: int | None = None, note: str | None = None) -> dict:
    it = _need_kind(item_id, "countdown", "倒数日")
    data = dict(it.data)
    if title is not None:
        if not title.strip():
            raise validation_error("标题不能为空")
        data["title"] = title.strip()
    if target_date is not None:
        if view.to_date(target_date) is None:
            raise validation_error("target_date 无法解析", got=target_date)
        data["target_date"] = str(target_date)
    if repeat_yearly is not None:
        data["repeat_yearly"] = bool(repeat_yearly)
    if color is not None:
        data["color"] = _check_color(color)
    if count_mode is not None:
        if count_mode not in COUNT_MODES:
            raise validation_error("count_mode 非法", allowed=list(COUNT_MODES), got=count_mode)
        data["count_mode"] = count_mode
    if show_age is not None:
        data["show_age"] = bool(show_age)
    if remind_days_before is not None:
        data["remind_days_before"] = int(remind_days_before)
    if note is not None:
        data["note"] = note
    return _bump(it, data).to_dict()


@feature(name="countdown.delete", group="countdown", http=("DELETE", "/api/v1/countdowns/{item_id}"),
         undoable=True)
def countdown_delete(item_id: str) -> dict:
    it = _need_kind(item_id, "countdown", "倒数日")
    if it.deleted:
        return it.to_dict()
    return _bump(it, deleted=True).to_dict()


@feature(name="countdown.restore", group="countdown",
         http=("POST", "/api/v1/countdowns/{item_id}/restore"), undoable=True)
def countdown_restore(item_id: str) -> dict:
    it = _need_kind(item_id, "countdown", "倒数日")
    if not it.deleted:
        return it.to_dict()
    return _bump(it, deleted=False).to_dict()
