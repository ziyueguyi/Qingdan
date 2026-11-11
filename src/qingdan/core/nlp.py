"""快速添加的自然语言解析（纯函数，不碰数据库）。

支持的写法：
    明天下午3点 交周报 #重要 !P1
识别 到期日/时间、标签（`#` 与 `@` 同义）、优先级，剩下的作为标题。

**`#` 为什么是标签而不是清单**：清单/分组的界面入口已移除（2026-11-10），`#名字` 若仍
指向清单，就会把任务归进一个界面上看不见的容器 —— 用户无法理解也无法管理。标签是
侧栏里真实存在的维度，所以 `#` 与 `@` 统一解析为标签。`list_id` 键保留但恒为 None
（接口签名不变，老调用方不受影响）。

设计约束：**解析失败不报错**，降级成「整句当标题」。快速添加是脑随手记的入口，
弹一个校验错误会让人当场放弃这个功能。无法确定的部分留空，由 UI 回显让用户确认。
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Any

from qingdan.core import stamp

WEEKDAYS = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}
# 相对日：偏移天数。**必须按词长倒序匹配**，否则「大后天」会被「后天」抢先命中。
REL_DAYS = {"今天": 0, "今日": 0, "明天": 1, "明日": 1, "后天": 2, "大后天": 3,
            "昨天": -1, "前天": -2, "大前天": -3}
_REL_ORDER = sorted(REL_DAYS, key=len, reverse=True)
PERIODS = {"凌晨": 0, "早上": 8, "上午": 9, "中午": 12, "下午": 14, "晚上": 19, "夜里": 21,
           "傍晚": 18, "今晚": 20}

_PRIORITY_RE = re.compile(r"[!！]\s*[Pp]\s*([1-4])")
_LIST_RE = re.compile(r"#\s*([^\s#@!！]+)")
_TAG_RE = re.compile(r"@\s*([^\s#@!！]+)")
_HM_RE = re.compile(r"(\d{1,2})[:：.](\d{2})")
_POINT_RE = re.compile(r"(\d{1,2})\s*点\s*(半|一刻|\d{1,2}分?)?")
_REL_N_RE = re.compile(r"(?:([0-9]+|[一二两三四五六七八九十]+))\s*(分钟后|小时后|天后|周后|个月后)")
_MD_RE = re.compile(r"(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]")
_ISO_RE = re.compile(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})")
_WEEK_RE = re.compile(r"(?:下\s*)?周\s*([一二三四五六日天])")

_CN_NUM = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
           "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


def _num(token: str) -> int | None:
    if token.isdigit():
        return int(token)
    if token in _CN_NUM:
        return _CN_NUM[token]
    if token.startswith("十") and len(token) == 2:
        return 10 + _CN_NUM.get(token[1], 0)
    return None


def _apply_time(base: datetime, hour: int, minute: int) -> datetime:
    return base.replace(hour=max(0, min(23, hour)), minute=max(0, min(59, minute)),
                        second=0, microsecond=0)


def _find_time(text: str) -> tuple[int, int, list[str]] | None:
    """返回 (时, 分, 需要一并从原文剔除的片段)。

    必须把时段词一起返回：只删「3点」不删「下午」，标题就会留下「下午 交周报」这种残渣。
    """
    m = _HM_RE.search(text)
    if m:
        h, mi = int(m.group(1)), int(m.group(2))
        if 0 <= h <= 23 and 0 <= mi <= 59:
            return h, mi, [m.group(0)]
    pm = None
    for word, default_hour in PERIODS.items():
        if word in text:
            pm = (word, default_hour)
            break
    m2 = _POINT_RE.search(text)
    if m2:
        h = int(m2.group(1))
        frac = m2.group(2) or ""
        minute = 30 if frac == "半" else (15 if frac == "一刻" else 0)
        if frac.isdigit():
            minute = int(frac[:2])
        if pm and pm[0] not in ("凌晨", "早上", "上午") and h < 12:
            h += 12
        elif not pm and h <= 7:
            h += 12          # 「6点」「3点半」在待办语境里多半是下午，不看 frac
        raws = [m2.group(0)] + ([pm[0]] if pm else [])
        return h, minute, raws
    if pm:
        return pm[1], 0, [pm[0]]
    return None


def parse_quick(text: str, today: date | None = None, now: datetime | None = None,
                groups: list[dict[str, Any]] | None = None,
                tags: list[str] | None = None) -> dict[str, Any]:
    """解析一句快速添加。groups 传 [{id,name}] 用于把 #名字 映射成 id。"""
    if not text or not text.strip():
        return {"title": "", "matched": [], "warnings": ["empty"]}

    today = today or stamp.now_dt().date()
    now = now or stamp.now_dt()
    rest = text.strip()
    matched: list[str] = []
    warnings: list[str] = []

    # 优先级
    priority = None
    if m := _PRIORITY_RE.search(rest):
        priority = int(m.group(1))
        matched.append(m.group(0))
        rest = rest.replace(m.group(0), " ", 1)

    # 标签：# 与 @ 同义（清单界面移除后 # 不再指向看不见的清单，见模块 docstring）
    list_id = list_name = None
    tag_names: list[str] = []
    for rx in (_LIST_RE, _TAG_RE):
        while m := rx.search(rest):
            if m.group(1) not in tag_names:
                tag_names.append(m.group(1))
            matched.append(m.group(0))
            rest = rest.replace(m.group(0), " ", 1)
    known = set(tags or [])
    for t in tag_names:
        if known and t not in known:
            warnings.append(f"unknown_tag:{t}")

    # 日期
    due_day: date | None = None
    for word in _REL_ORDER:
        if word in rest:
            due_day = today + timedelta(days=REL_DAYS[word])
            matched.append(word)
            rest = rest.replace(word, " ", 1)
            break
    if due_day is None:
        if m := _REL_N_RE.search(rest):
            n = _num(m.group(1))
            unit = m.group(2)
            if n is not None:
                delta = {"分钟后": None, "小时后": None, "天后": timedelta(days=n),
                         "周后": timedelta(weeks=n), "个月后": timedelta(days=30 * n)}[unit]
                if delta is not None:
                    due_day = today + delta
                else:                       # 分钟/小时级：直接用当前时间推
                    shift = timedelta(minutes=n) if unit == "分钟后" else timedelta(hours=n)
                    due_day = (now + shift).date()
                matched.append(m.group(0))
                rest = rest.replace(m.group(0), " ", 1)
    if due_day is None:
        if m := _ISO_RE.search(rest):
            try:
                due_day = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
                matched.append(m.group(0))
                rest = rest.replace(m.group(0), " ", 1)
            except ValueError:
                warnings.append(f"bad_date:{m.group(0)}")
    if due_day is None:
        if m := _MD_RE.search(rest):
            try:
                candidate = date(today.year, int(m.group(1)), int(m.group(2)))
                if candidate < today - timedelta(days=180):
                    candidate = date(today.year + 1, int(m.group(1)), int(m.group(2)))
                due_day = candidate
                matched.append(m.group(0))
                rest = rest.replace(m.group(0), " ", 1)
            except ValueError:
                warnings.append(f"bad_date:{m.group(0)}")
    if due_day is None:
        if m := _WEEK_RE.search(rest):
            offset = (WEEKDAYS[m.group(1)] - today.weekday()) % 7
            if "下" in m.group(0):
                offset += 7
            offset = offset or 7
            due_day = today + timedelta(days=offset)
            matched.append(m.group(0))
            rest = rest.replace(m.group(0), " ", 1)

    # 时间：没写日期但写了时间（如「15:30 开会」）时，默认落到今天
    hour = minute = None
    all_day = True
    t = _find_time(rest)
    if t:
        hour, minute, raws = t
        all_day = False
        matched.extend(raws)
        for raw in raws:
            rest = rest.replace(raw, " ", 1)
        if due_day is None:
            due_day = today

    if due_day is not None and hour is not None:
        dt = _apply_time(datetime(due_day.year, due_day.month, due_day.day), hour, minute)
        if dt < now:
            warnings.append("due_in_past")
        due_at = dt.isoformat(timespec="seconds")
    elif due_day is not None:
        due_at = due_day.isoformat()
    else:
        due_at = None

    title = re.sub(r"\s+", " ", rest).strip(" ，,、-")
    if not title:
        title = text.strip()          # 全是 token 时退回原文，别建出一条空任务
        warnings.append("title_is_raw_text")

    return {"title": title, "due_at": due_at, "due_all_day": all_day if due_at else True,
            "list_id": list_id, "list_name": list_name, "tags": tag_names,
            "priority": priority, "matched": matched, "warnings": warnings}
