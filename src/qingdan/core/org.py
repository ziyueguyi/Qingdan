"""组织结构：文件夹 / 小节 / 标签 / 自定义过滤器。

这些 kind 都是独立 Item（不用内嵌数组），所以天然走同一套同步协议。
写入辅助函数复用 service 的 _create/_bump/_load —— 同包内部，保证前像/撤销口径一致。
"""
from __future__ import annotations

import re

from qingdan.core.context import ctx
from qingdan.core.models import Item
from qingdan.core.clock import Rev
from qingdan.core.registry import feature, not_found, validation_error
from qingdan.core.service import _bump, _create, _load
from qingdan.core import stamp, view, task_query
from qingdan.core.icons import check_icon

_HEX = re.compile(r"#?[0-9a-fA-F]{6}")


def _need_kind(item_id: str, kind: str, label: str, allow_deleted: bool = True) -> Item:
    it = ctx.need_store().get(item_id)
    if it is None and kind == "tag" and item_id.startswith("tag-name-"):
        try:
            name = bytes.fromhex(item_id[len("tag-name-"):]).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            raise not_found(f"{label} {item_id}") from None
        if ctx.need_store().task_tag_exists(name):
            return Item(kind="tag", id=item_id, rev=Rev(0, 0, "derived"),
                        data={"name": name, "order": 0, "color": None})
    if it is None or (it.deleted and not allow_deleted) or it.kind != kind:
        raise not_found(f"{label} {item_id}")
    return it


def _check_color(color: str | None) -> str | None:
    if color in (None, ""):
        return None
    c = str(color).strip()
    if not _HEX.fullmatch(c):
        raise validation_error("color 必须是 #RRGGBB", got=color)
    return c if c.startswith("#") else f"#{c}"


def _tasks_by_tag(name: str) -> list[Item]:
    return [it for it in ctx.need_store().list_items(kind="task")
            if name in (it.data.get("tags") or [])]


# --------------------------------------------------------------------------- 文件夹


@feature(name="folder.create", group="folder", http=("POST", "/api/v1/folders"), undoable=True)
def folder_create(name: str, order: int = 0, color: str | None = None) -> dict:
    if not name or not name.strip():
        raise validation_error("文件夹名不能为空")
    return _create("folder", {"name": name.strip(), "order": int(order),
                              "color": _check_color(color)}).to_dict()


@feature(name="folder.list", group="folder", http=("GET", "/api/v1/folders"), idempotent=True)
def folder_list(include_deleted: bool = False) -> dict:
    items = ctx.need_store().list_items(kind="folder", include_deleted=include_deleted)
    items.sort(key=lambda it: (int(it.data.get("order", 0)), it.data.get("name", "")))
    return {"total": len(items), "items": [it.to_dict() for it in items]}


@feature(name="folder.update", group="folder", http=("PATCH", "/api/v1/folders/{item_id}"),
         undoable=True)
def folder_update(item_id: str, name: str | None = None, order: int | None = None,
                  color: str | None = None) -> dict:
    it = _need_kind(item_id, "folder", "文件夹")
    data = dict(it.data)
    if name is not None:
        if not name.strip():
            raise validation_error("文件夹名不能为空")
        data["name"] = name.strip()
    if order is not None:
        data["order"] = int(order)
    if color is not None:
        data["color"] = _check_color(color)
    return _bump(it, data).to_dict()


@feature(name="folder.delete", group="folder", http=("DELETE", "/api/v1/folders/{item_id}"),
         undoable=True)
def folder_delete(item_id: str, move_groups_to: str | None = None) -> dict:
    """删文件夹时其下分组不跟着消失 —— 分组是独立 Item，只清 folder_id 悬空引用。"""
    it = _need_kind(item_id, "folder", "文件夹")
    st = ctx.need_store()
    for g in st.list_items(kind="list"):
        if g.data.get("folder_id") == item_id:
            _bump(g, {**g.data, "folder_id": move_groups_to})
    return _bump(it, deleted=True).to_dict()


# --------------------------------------------------------------------------- 小节


@feature(name="section.create", group="section", http=("POST", "/api/v1/sections"), undoable=True)
def section_create(list_id: str, name: str, order: int = 0) -> dict:
    _need_kind(list_id, "list", "分组")
    if not name or not name.strip():
        raise validation_error("小节名不能为空")
    return _create("section", {"list_id": list_id, "name": name.strip(),
                               "order": int(order)}).to_dict()


@feature(name="section.list", group="section", http=("GET", "/api/v1/sections"), idempotent=True)
def section_list(list_id: str | None = None, include_deleted: bool = False) -> dict:
    items = [it for it in ctx.need_store().list_items(kind="section", include_deleted=include_deleted)
             if not list_id or it.data.get("list_id") == list_id]
    items.sort(key=lambda it: (int(it.data.get("order", 0)), it.id))
    return {"total": len(items), "items": [it.to_dict() for it in items]}


@feature(name="section.update", group="section", http=("PATCH", "/api/v1/sections/{item_id}"),
         undoable=True)
def section_update(item_id: str, name: str | None = None, order: int | None = None) -> dict:
    it = _need_kind(item_id, "section", "小节")
    data = dict(it.data)
    if name is not None:
        if not name.strip():
            raise validation_error("小节名不能为空")
        data["name"] = name.strip()
    if order is not None:
        data["order"] = int(order)
    return _bump(it, data).to_dict()


@feature(name="section.delete", group="section", http=("DELETE", "/api/v1/sections/{item_id}"),
         undoable=True)
def section_delete(item_id: str) -> dict:
    """删小节：其下任务的 section_id 置空，任务本身不动。"""
    it = _need_kind(item_id, "section", "小节")
    for task in ctx.need_store().list_items(kind="task"):
        if task.data.get("section_id") == item_id:
            _bump(task, {**task.data, "section_id": None})
    return _bump(it, deleted=True).to_dict()


@feature(name="section.reorder", group="section", http=("POST", "/api/v1/sections/reorder"),
         undoable=True)
def section_reorder(list_id: str, order: list) -> dict:
    """按给定 id 序列重排小节。order 是 section id 数组。"""
    if not isinstance(order, list) or not order:
        raise validation_error("order 必须是非空的 section id 数组")
    st = ctx.need_store()
    moved = []
    for idx, sid in enumerate(order):
        it = st.get(sid)
        if it is None or it.kind != "section":
            raise not_found(f"小节 {sid}")
        if it.data.get("list_id") != list_id:
            raise validation_error("小节不属于该分组", section=sid, list_id=list_id)
        _bump(it, {**it.data, "order": (idx + 1) * 10})
        moved.append(sid)
    return {"list_id": list_id, "reordered": moved}


# --------------------------------------------------------------------------- 标签


@feature(name="tag.create", group="tag", http=("POST", "/api/v1/tags"), undoable=True)
def tag_create(name: str, color: str | None = None, order: int = 0, icon: str = "") -> dict:
    if not name or not name.strip():
        raise validation_error("标签名不能为空")
    n = name.strip().lstrip("#")
    if not n.strip():
        raise validation_error("标签名不能为空")
    n = n.strip()
    dup = [t for t in ctx.need_store().list_items(kind="tag") if t.data.get("name") == n]
    if dup:
        raise validation_error("标签已存在", name=n)
    return _create("tag", {"name": n, "color": _check_color(color), "order": int(order), "icon": check_icon(icon)}).to_dict()


@feature(name="tag.list", group="tag", http=("GET", "/api/v1/tags"), idempotent=True)
def tag_list(include_deleted: bool = False, limit: int | None = 5,
             offset: int = 0, q: str = "") -> dict:
    return ctx.need_store().catalog_page("tag", limit, offset, q, include_deleted)


@feature(name="tag.get", group="tag", http=("GET", "/api/v1/tags/{item_id}"), idempotent=True)
def tag_get(item_id: str) -> dict:
    return _need_kind(item_id, "tag", "标签", allow_deleted=False).to_dict()


@feature(name="tag.rename", group="tag", http=("PATCH", "/api/v1/tags/{item_id}"), undoable=True)
def tag_rename(item_id: str, name: str | None = None, color: str | None = None,
               order: int | None = None, icon: str | None = None) -> dict:
    """改名要连带改写所有引用该标签的任务 —— 标签名是任务的值，不是外键。"""
    it = _need_kind(item_id, "tag", "标签", allow_deleted=False)
    old_name = it.data.get("name")
    data = dict(it.data)
    if icon is not None: data['icon'] = check_icon(icon)
    if name is not None:
        if not name.strip().lstrip("#").strip():
            raise validation_error("标签名不能为空")
        data["name"] = name.strip().lstrip("#").strip()
        if data["name"] != old_name:
            if ctx.need_store().tag_name_exists(data["name"], exclude_id=item_id):
                raise validation_error("标签已存在", name=data["name"])
    if color is not None:
        data["color"] = _check_color(color)
    if order is not None:
        data["order"] = int(order)
    out = _bump(it, data).to_dict()
    if data.get("name") != old_name:
        touched = 0
        for task in _tasks_by_tag(old_name):
            tags = [data["name"] if t == old_name else t for t in (task.data.get("tags") or [])]
            _bump(task, {**task.data, "tags": tags})
            touched += 1
        out["tasks_updated"] = touched
    return out


@feature(name="tag.delete", group="tag", http=("DELETE", "/api/v1/tags/{item_id}"), undoable=True)
def tag_delete(item_id: str) -> dict:
    it = _need_kind(item_id, "tag", "标签", allow_deleted=False)
    name = it.data.get("name")
    touched = 0
    for task in _tasks_by_tag(name):
        _bump(task, {**task.data,
                     "tags": [t for t in (task.data.get("tags") or []) if t != name]})
        touched += 1
    out = _bump(it, deleted=True).to_dict()
    out["tasks_updated"] = touched
    return out


@feature(name="tag.assign", group="tag", http=("POST", "/api/v1/tasks/{item_id}/tags"),
         undoable=True)
def tag_assign(item_id: str, tag: str) -> dict:
    task = _load(item_id, allow_deleted=False)
    n = str(tag).strip().lstrip("#")
    if not n:
        raise validation_error("tag 不能为空")
    tags = list(task.data.get("tags") or [])
    if n in tags:
        return task.to_dict()
    tags.append(n)
    return _bump(task, {**task.data, "tags": tags}).to_dict()


@feature(name="tag.unassign", group="tag", http=("DELETE", "/api/v1/tasks/{item_id}/tags"),
         undoable=True)
def tag_unassign(item_id: str, tag: str) -> dict:
    task = _load(item_id, allow_deleted=False)
    n = str(tag).strip().lstrip("#")
    tags = [t for t in (task.data.get("tags") or []) if t != n]
    return _bump(task, {**task.data, "tags": tags}).to_dict()


# --------------------------------------------------------------------------- 自定义过滤器


@feature(name="filter.create", group="filter", http=("POST", "/api/v1/filters"), undoable=True)
def filter_create(name: str, criteria: dict, color: str | None = None, order: int = 0, icon: str = "") -> dict:
    """自定义过滤器 = 一段条件定义，不是数据副本。滴答/Todoist 都把它做成付费点，我们免费给。"""
    if not name or not name.strip():
        raise validation_error("过滤器名不能为空")
    if not isinstance(criteria, dict) or not criteria:
        raise validation_error("criteria 必须是非空条件对象")
    unknown = set(criteria) - {"status", "open_only", "list_id", "group_id", "no_group", "tag",
                               "tags_any", "priority", "priority_max", "due_within_days",
                               "due_from_days", "overdue", "has_due", "q"}
    if unknown:
        raise validation_error("不支持的过滤条件", fields=sorted(unknown))
    return _create("filter", {"name": name.strip(), "criteria": criteria,
                              "color": _check_color(color), "order": int(order),
                              "created_at": stamp.now_text(), "icon": check_icon(icon)}).to_dict()


@feature(name="filter.list", group="filter", http=("GET", "/api/v1/filters"), idempotent=True)
def filter_list(include_deleted: bool = False, limit: int | None = 5, offset: int = 0, q: str = "") -> dict:
    return ctx.need_store().catalog_page("filter", limit, offset, q, include_deleted)


@feature(name="filter.get", group="filter", http=("GET", "/api/v1/filters/{item_id}"), idempotent=True)
def filter_get(item_id: str) -> dict:
    return _need_kind(item_id, "filter", "过滤器", allow_deleted=False).to_dict()


@feature(name="filter.update", group="filter", http=("PATCH", "/api/v1/filters/{item_id}"),
         undoable=True)
def filter_update(item_id: str, name: str | None = None, criteria: dict | None = None,
                  color: str | None = None, order: int | None = None, icon: str | None = None) -> dict:
    it = _need_kind(item_id, "filter", "过滤器")
    data = dict(it.data)
    if icon is not None: data['icon'] = check_icon(icon)
    if name is not None:
        if not name.strip():
            raise validation_error("过滤器名不能为空")
        data["name"] = name.strip()
    if criteria is not None:
        if not isinstance(criteria, dict) or not criteria:
            raise validation_error("criteria 必须是非空条件对象")
        data["criteria"] = criteria
    if color is not None:
        data["color"] = _check_color(color)
    if order is not None:
        data["order"] = int(order)
    return _bump(it, data).to_dict()


@feature(name="filter.delete", group="filter", http=("DELETE", "/api/v1/filters/{item_id}"),
         undoable=True)
def filter_delete(item_id: str) -> dict:
    return _bump(_need_kind(item_id, "filter", "过滤器"), deleted=True).to_dict()


@feature(name="filter.tasks", group="filter", http=("GET", "/api/v1/filters/{item_id}/tasks"),
         idempotent=True)
def filter_tasks(item_id: str, today: str | None = None, sort: str = "smart",
                 group: str = "none", reverse: bool = False, limit: int = 0, offset: int = 0) -> dict:
    """返回形状与 view.query 一致（items + groups），前端的分组渲染才能复用同一段代码。"""
    it = _need_kind(item_id, "filter", "过滤器")
    d = view.as_date(today) if today else view.as_date(stamp.now_day())
    where, args = task_query.criteria_where(it.data.get("criteria") or {}, d)
    ordered, page = task_query.select(ctx.need_store(), where, args, sort, reverse, limit, offset)
    names = ctx.need_store().list_names(task.data.get("list_id") for task in ordered)
    buckets = view.group_items(ordered, group, d, names)
    return {"filter": it.data.get("name"), **page, "list_names": names,
            "groups": [{"key": b["key"], "label_key": b["label_key"], "label": b["label"],
                        "total": len(b["items"]),
                        "items": [x.to_dict() for x in b["items"]]} for b in buckets],
            "items": [x.to_dict() for x in ordered]}
