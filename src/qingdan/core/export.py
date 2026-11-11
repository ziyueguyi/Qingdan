"""数据导出与导入（M6）。

**为什么没有 xlsx**：导出用 openpyxl 要多背约 1.5MB 依赖，与"exe 体积尽量小"直接冲突。
CSV 用 `utf-8-sig` 写出，Excel 双击就能正确显示中文，对个人清单这个量级足够。
真要 xlsx，可以之后单独加一个可选依赖，不进主包。

**导出不含凭据**：只导出 Item，`secret` 表一个字节都不出去。有测试守着这条。
"""
from __future__ import annotations

import csv
import io
import json
import time
from pathlib import Path

from qingdan.core.context import ctx
from qingdan.core.models import Item, SCHEMA_VERSION
from qingdan.core.registry import feature, validation_error
from qingdan.core.view import due_key

EXPORT_DIR = "exports"
FORMATS = ("csv", "json", "md")
LANGS = ("zh", "en")

# 表头/章节标题属于**文件内容**（给 Excel 看），不属于界面文案，所以由 lang 参数决定；
# 但绝不能写死中文 —— 英文界面下导出的文件会整份变成中文，而用户的偏好是"语言不写死"。
CSV_COLUMNS = {
    "zh": ["标题", "类型", "分组", "小节", "状态", "优先级", "到期", "提醒", "重复",
           "标签", "颜色", "备注", "创建时间", "完成时间", "已删除", "版本戳"],
    "en": ["title", "kind", "group", "section", "status", "priority", "due", "remind",
           "repeat", "tags", "color", "note", "created", "done", "deleted", "rev"],
}
WORDS = {
    "zh": {"none": "无", "yes": "是", "inbox": "收集箱", "md_title": "清单导出",
           "countdown": "倒数日", "done_note": "另有 {n} 条已完成任务未列出"},
    "en": {"none": "none", "yes": "yes", "inbox": "inbox", "md_title": "Checklist export",
           "countdown": "Countdowns", "done_note": "{n} completed tasks are not listed"},
}


def exports_dir() -> Path:
    d = Path(ctx.data_dir) / EXPORT_DIR
    d.mkdir(parents=True, exist_ok=True)
    return d


def _group_names() -> dict[str, str]:
    return {it.id: (it.data.get("name") or "") for it in ctx.need_store().list_items(kind="list")}


def collect(include_deleted: bool = False) -> list[Item]:
    store = ctx.need_store()
    return [it for it in store.list_items(include_deleted=include_deleted)]


# --------------------------------------------------------------------------- 各格式


def to_csv(items: list[Item], lang: str = "zh") -> str:
    names = _group_names()
    words = WORDS[lang]
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(CSV_COLUMNS[lang])
    for it in items:
        d = it.data
        w.writerow([
            d.get("title") or d.get("name") or "",
            it.kind,
            names.get(d.get("list_id") or "", ""),
            names.get(d.get("section_id") or "", ""),
            d.get("status") or "",
            f"P{d.get('priority', 4)}" if d.get("priority", 4) < 4 else words["none"],
            d.get("due_at") or d.get("target_date") or "",
            ";".join(d.get("remind_at") or []),
            d.get("repeat") or "",
            ";".join(d.get("tags") or []),
            d.get("color") or "",
            (d.get("note") or "").replace("\n", " ⏎ "),
            d.get("created_at") or "",
            d.get("done_at") or "",
            words["yes"] if it.deleted else "",
            f"{it.rev.ms}.{it.rev.seq}.{it.rev.dev}",
        ])
    return buf.getvalue()


def to_json(items: list[Item]) -> str:
    """完整 Item，可直接喂给 data.import 还原。不含任何凭据。"""
    return json.dumps({
        "product": "qingdan",
        "schema": SCHEMA_VERSION,
        "exported_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "count": len(items),
        "items": [it.payload() for it in items],
    }, ensure_ascii=False, indent=2, sort_keys=True)


def to_markdown(items: list[Item], lang: str = "zh") -> str:
    names = _group_names()
    words = WORDS[lang]
    lines = [f"# {words['md_title']} · {time.strftime('%Y-%m-%d %H:%M')}", ""]
    alive = [it for it in items if not it.deleted and it.kind == "task"]
    by_group: dict[str, list[Item]] = {}
    for it in alive:
        label = names.get(it.data.get("list_id") or "", "") or words["inbox"]
        by_group.setdefault(label, []).append(it)
    for label in sorted(by_group):
        lines.append(f"## {label}")
        for it in sorted(by_group[label], key=lambda x: (x.data.get("due_at") or "9999",
                                                         x.data.get("priority", 4))):
            d = it.data
            mark = "x" if d.get("status") == "done" else " "
            bits = [f"- [{mark}] {d.get('title', '')}"]
            if d.get("due_at"):
                bits.append(f" · {d['due_at']}")
            if d.get("priority", 4) < 4:
                bits.append(f" · P{d['priority']}")
            for tag in (d.get("tags") or []):
                bits.append(f" · #{tag}")
            lines.append("".join(bits))
        lines.append("")
    cds = [it for it in items if not it.deleted and it.kind == "countdown"]
    if cds:
        lines.append(f"## {words['countdown']}")
        for it in sorted(cds, key=lambda x: x.data.get("target_date") or ""):
            lines.append(f"- {it.data.get('title')} · {it.data.get('target_date')}")
        lines.append("")
    done = [it for it in items if not it.deleted and it.kind == "task"
            and it.data.get("status") == "done"]
    if done:
        lines.append(f"> {words['done_note'].format(n=len(done))}")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- 导入


def import_json(text: str) -> dict:
    """幂等导入：只在来版比本地更新时才写入，重复导入同一份不会产生改动。"""
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as e:
        raise validation_error(f"不是合法 JSON：{e}") from e
    if not isinstance(payload, dict) or payload.get("product") != "qingdan":
        raise validation_error("不是 Qingdan 导出文件（缺 product 标记）")
    items_in = payload.get("items")
    if not isinstance(items_in, list):
        raise validation_error("items 必须是数组")

    store = ctx.need_store()
    applied, skipped, invalid = 0, 0, 0
    for raw in items_in:
        try:
            incoming = Item.from_payload(raw)
        except Exception:
            invalid += 1
            continue
        current = store.get(incoming.id)
        if current is not None and current.version >= incoming.version:
            skipped += 1
            continue
        store.put(incoming, dirty=True)
        store.set_base(incoming.id, incoming.rev, incoming.deleted)
        store.db.execute("UPDATE item SET dirty = 1 WHERE id = ?", (incoming.id,))
        store.db.commit()
        applied += 1
    return {"applied": applied, "skipped": skipped, "invalid": invalid,
            "total": len(items_in)}


# --------------------------------------------------------------------------- 接口


@feature(name="data.export", group="data", http=("POST", "/api/v1/export"))
def data_export(format: str = "json", include_deleted: bool = False,
                lang: str = "zh") -> dict:
    if format not in FORMATS:
        raise validation_error("format 只能是 csv|json|md", got=format)
    _check_lang(lang)
    items = collect(include_deleted)
    body = render(format, items, lang)
    # utf-8-sig：Excel 直接双击打开 CSV 才不会把中文显示成乱码
    encoding = "utf-8-sig" if format == "csv" else "utf-8"
    path = exports_dir() / f"export-{time.strftime('%Y%m%d-%H%M%S')}.{format}"
    path.write_text(body, encoding=encoding)
    return {"format": format, "path": str(path), "items": len(items), "lang": lang,
            "bytes": path.stat().st_size}


@feature(name="data.preview", group="data", http=("GET", "/api/v1/export/preview"),
         idempotent=True)
def data_preview(format: str = "csv", limit: int = 20, lang: str = "zh") -> dict:
    """不落盘，直接看内容。导出前先确认字段对不对，省得来回试。"""
    if format not in FORMATS:
        raise validation_error("format 只能是 csv|json|md", got=format)
    _check_lang(lang)
    items = collect()[: int(limit)]
    body = render(format, items, lang)
    return {"format": format, "items": len(items), "lang": lang, "content": body}


def render(format: str, items: list[Item], lang: str = "zh") -> str:
    """JSON 是机器可读的完整 Item，没有需要翻译的表头，所以只有 csv/md 吃 lang。"""
    if format == "csv":
        return to_csv(items, lang)
    if format == "md":
        return to_markdown(items, lang)
    return to_json(items)


def _check_lang(lang: str) -> None:
    if lang not in LANGS:
        raise validation_error("lang 只能是 zh|en", got=lang, allowed=list(LANGS))


@feature(name="data.import", group="data", http=("POST", "/api/v1/import"))
def data_import(content: str | None = None, path: str | None = None) -> dict:
    if content:
        return import_json(content)
    if path:
        p = Path(path)
        if not p.is_file():
            raise validation_error("文件不存在", got=path)
        return import_json(p.read_text(encoding="utf-8"))
    raise validation_error("content 与 path 至少给一个")
