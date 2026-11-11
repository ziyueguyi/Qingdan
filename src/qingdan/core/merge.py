"""三方合并原语 —— R3b 的核心。

为什么不能只比 local / remote：两边都「没有」这条记录时，无法区分
「别人删了」和「我这台机器压根没有」。所以必须有 base（上次同步快照）。

墓碑（deleted=true）和普通版本走同一条比较路径，删除不需要特例。
"""
from __future__ import annotations

from typing import TypeAlias

from qingdan.core.models import Item

#: None 表示「该 id 在这一侧不存在」
Side: TypeAlias = Item | None


def _checked(role: str, value):
    """把「不存在」写错成 clock.ABSENT 时，当场说清楚而不是抛一句看不懂的 AttributeError。

    `ABSENT` 是 `Rev`，只参与 `rev` 字段之间的比较；本模块的三方裁决吃的是
    `Item | None`。两个哨兵很容易混用（写测试时就踩了：`pick(L, R, ABSENT)`
    报的是 `'Rev' object has no attribute 'version'`，看不出哪里错）。
    """
    if value is not None and not isinstance(value, Item):
        raise TypeError(
            f"pick 的参数 {role} 必须是 Item 或 None，收到 {type(value).__name__}。"
            f"「这一侧没有这条记录」请传 None；clock.ABSENT 是 Rev 哨兵，只用于版本戳比较。")
    return value


def same(a: Side, b: Side) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return a.version == b.version


def newer(a: Side, b: Side) -> Side:
    """返回版本较大的一侧；None 视为最旧。"""
    if a is None:
        return b
    if b is None:
        return a
    return a if a.version >= b.version else b


def pick(local: Side, remote: Side, base: Side) -> tuple[Side, Side | None]:
    """三方裁决，返回 (胜出, 落败或 None)。

    | local | remote | base | 判定 |
    |---|---|---|---|
    | =base | =base | * | 无变化 |
    | =base | ≠base | * | 只远端改 → 远端胜 |
    | ≠base | =base | * | 只本地改 → 本地胜 |
    | ≠base | ≠base | * | 双方都改 → HLC 大者胜，小者落 conflict |
    """
    _checked("local", local)
    _checked("remote", remote)
    _checked("base", base)
    if same(local, base) and same(remote, base):
        return base, None
    if same(local, base):
        return remote, None
    if same(remote, base):
        return local, None
    if local is not None and (remote is None or local.version > remote.version):
        return local, remote
    return remote, local


def should_record_conflict(winner: Side, loser: Side) -> bool:
    """落败版本要不要留档。

    `pick` 保持纯裁决，冲突策略放这里 —— 两边都删时结果都是「删除」，
    语义一致，不该打扰用户；其余双方都改的情况一律留档，保证不静默丢数据。
    """
    if winner is None or loser is None:
        return False
    if winner.deleted and loser.deleted:
        return False
    return winner.version != loser.version


def external_delete_detected(local: Side, remote: Side, base: Side) -> bool:
    """远端文件被人在网页上手工删了（相对 base 消失，且没有墓碑）。

    这是协议外的异常，默认策略是 keep + 告警，绝不据此删本地 ——
    这条保险丝保证任何客户端 bug 都不可能把远端整库清空。
    """
    return base is not None and not base.deleted and remote is None and local is not None


# --------------------------------------------------------------------------- 字段级差异

#: 每个字段：`label_key` 是给前端翻译的机器键，`type` 决定界面用什么控件。
#: 为什么差异在后端算：前端再实现一遍"哪边改了、改了什么"就会有两套判断
#: （视图层那次「已逾期」硬编码进响应体，正是同一类重复实现暴露的）。
FIELD_META: dict[str, tuple[str, str]] = {
    "title": ("f_title", "text"),
    "name": ("f_name", "text"),
    "text": ("f_text", "text"),
    "note": ("f_note", "text"),
    "status": ("f_status", "enum_status"),
    "priority": ("f_priority", "enum_priority"),
    "color": ("f_color", "color"),
    "due_at": ("f_due", "datetime"),
    "done_at": ("f_done_at", "datetime"),
    "target_date": ("f_target_date", "date"),
    "repeat": ("f_repeat", "enum_repeat"),
    "repeat_start": ("f_repeat_start", "datetime"),
    "repeat_yearly": ("f_repeat_yearly", "bool"),
    "remind_at": ("f_remind", "list"),
    "remind_days_before": ("f_remind_days_before", "int"),
    "alert_channels": ("f_alert_channels", "list"),
    "remind_mode": ("f_remind_mode", "enum_remind_mode"),
    "tags": ("f_tags", "list"),
    "count_mode": ("f_count_mode", "enum_count_mode"),
    "show_age": ("f_show_age", "bool"),
    "criteria": ("f_criteria", "json"),
    "icon": ("f_icon", "text"),
    "order": ("f_order", "int"),
    "parent_id": ("f_parent", "ref"),
    "list_id": ("f_group", "ref"),
    "folder_id": ("f_folder", "ref"),
    "section_id": ("f_section", "ref"),
}

#: 每个 kind 按什么顺序摆字段（也限定哪些字段参与比较，多余的内部字段不展示）。
KIND_FIELDS: dict[str, tuple[str, ...]] = {
    "task": ("title", "note", "status", "priority", "due_at", "repeat", "repeat_start",
             "remind_at", "alert_channels", "tags", "color", "list_id", "section_id",
             "parent_id", "done_at", "order"),
    "countdown": ("title", "target_date", "count_mode", "repeat_yearly",
                  "remind_days_before", "remind_at", "show_age", "note", "color"),
    "list": ("name", "color", "icon", "folder_id", "order"),
    "folder": ("name", "icon", "order"),
    "section": ("name", "list_id", "order"),
    "tag": ("name", "color", "icon"),
    "note": ("text", "parent_id"),
    "filter": ("name", "criteria", "icon"),
}

#: 引用类字段存的是 id，直接显示给用户是一串 ULID，必须先换成名字。
REF_FIELDS = frozenset({"list_id", "folder_id", "section_id", "parent_id"})


def diff_data(kind: str, local: dict | None, remote: dict | None, base: dict | None,
              names: dict[str, str] | None = None) -> list[dict[str, Any]]:
    """逐字段比较两份数据，只返回**有差异**的行。

    `state` 是机器键，前端据此上色与给默认选择：
      local_only  只有本地改了（默认留本地）
      remote_only 只有远端改了（默认取远端）
      both_diff   两边都改了且不一样（必须人工裁决）
      local_missing / remote_missing  一边整条不存在（新增 vs 删除那种对撞）
    """
    names = names or {}
    fields = KIND_FIELDS.get(kind, ())
    rows: list[dict[str, Any]] = []
    for key in fields:
        meta = FIELD_META.get(key, (f"f_{key}", "text"))
        label_key, ftype = meta
        lv = (local or {}).get(key)
        rv = (remote or {}).get(key)
        bv = (base or {}).get(key)
        if local is None or remote is None:
            state = "local_missing" if local is None else "remote_missing"
        elif _eq(lv, rv):
            continue                                  # 两边一致，不该占用用户注意力
        elif _eq(lv, bv):
            state = "remote_only"
        elif _eq(rv, bv):
            state = "local_only"
        else:
            state = "both_diff"
        row = {"key": key, "label_key": label_key, "type": ftype, "state": state,
               "local": _show(lv, ftype, names), "remote": _show(rv, ftype, names),
               "base": _show(bv, ftype, names)}
        if ftype == "ref":                            # 展示名字，提交仍用 id
            row["local_id"], row["remote_id"] = lv, rv
        rows.append(row)
    return rows


def _eq(a: Any, b: Any) -> bool:
    """列表按集合比（tags 顺序不算差异），其余按值比。"""
    if isinstance(a, list) or isinstance(b, list):
        if not (isinstance(a, list) and isinstance(b, list)):
            return False
        return sorted(map(str, a)) == sorted(map(str, b))
    return a == b


def _show(value: Any, ftype: str, names: dict[str, str]) -> Any:
    if ftype == "ref":
        if value in (None, ""):
            return None
        return names.get(value) or value          # 名字查不到（已删/别的机器建的）就退回 id
    if isinstance(value, list):
        return list(value)
    return value
