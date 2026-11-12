"""最小 RRULE 子集（RFC 5545）—— 纯函数，零第三方依赖。

**为什么不引 dateutil**：它只为这一个功能而来，PyInstaller 会把整个包打进 exe，而体积
门禁 42MB 的余量只剩 1.5MB。这里真正需要的只是「日/周/月/年 + INTERVAL + BYDAY +
BYMONTHDAY + UNTIL + COUNT」，自己写死并用属性测试钉住比引依赖更划算。

三条设计纪律（对应开发文档 §6.5）：

1. **只算日期，不预生成实例**。重复任务永远只有一个 Item，`due_at` 就是「当前这一次」；
   预生成实例会让同步数据量随时间线性爆炸。
2. **时刻跟随原值**。推进只换日期：20:00 的任务推进后还是 20:00，全天任务推进后仍是全天。
3. **月末夹取，绝不溢出**。1 月 31 日 + 每月 → 2 月 28/29 日，而不是 3 月 3 日；
   2 月 29 日 + 每年 → 平年落 2 月 28 日。溢出会让「每月最后一天还款」这类任务悄悄漂走。

**同步安全**：本模块只做纯计算，不碰数据库、不读墙钟，所以两台机器对同一份
`(due_at, repeat, repeat_start)` 必然算出同一个下一次 —— 这是它能进 LWW 同步的前提。
"""
from __future__ import annotations

import calendar
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Iterator

from qingdan.core import workdays

FREQS = ("DAILY", "WEEKLY", "MONTHLY", "YEARLY")

#: 星期缩写 → Python weekday()（周一=0）
_WEEKDAYS = {"MO": 0, "TU": 1, "WE": 2, "TH": 3, "FR": 4, "SA": 5, "SU": 6}
_DAY_KEYS = ("wd_mo", "wd_tu", "wd_we", "wd_th", "wd_fr", "wd_sa", "wd_su")

#: 简写（service 层的规范值）→ FREQ
_SHORT = {"daily": "DAILY", "weekly": "WEEKLY", "monthly": "MONTHLY", "yearly": "YEARLY"}

_FREQ_KEY = {"DAILY": "rp_daily", "WEEKLY": "rp_weekly",
             "MONTHLY": "rp_monthly", "YEARLY": "rp_yearly"}
_FREQ_N_KEY = {"DAILY": "rp_every_n_days", "WEEKLY": "rp_every_n_weeks",
               "MONTHLY": "rp_every_n_months", "YEARLY": "rp_every_n_years"}

#: 迭代上限。任何规则最多走这么多步，防死循环（畸形规则来自另一台机器时也不能挂）。
_MAX_STEPS = 5000


class RuleError(ValueError):
    """规则不合法。service 层把它翻成 validation_error，视图层用 try_parse 吞掉。"""


class CalendarError(RuleError):
    def __init__(self, error: workdays.CalendarUnavailable):
        self.calendar_id, self.year = error.calendar_id, error.year
        super().__init__(str(error))


@dataclass(frozen=True)
class Rule:
    freq: str
    interval: int = 1
    byday: tuple[int, ...] = ()
    bymonthday: tuple[int, ...] = ()
    until: date | None = None
    count: int | None = None
    workday: str | None = None
    calendar_id: str | None = None
    raw: str = field(default="", compare=False)


# --------------------------------------------------------------------------- 解析


def _parse_until(value: str) -> date:
    v = value.strip().replace("Z", "").replace("z", "")
    if not v:
        raise RuleError("UNTIL 为空")
    # RFC5545 的 20261231 / 20261231T235959
    digits = v.split("T", 1)[0]
    if len(digits) == 8 and digits.isdigit():
        try:
            return date(int(digits[:4]), int(digits[4:6]), int(digits[6:8]))
        except ValueError as e:
            raise RuleError(f"UNTIL 不是合法日期：{value!r}") from e
    # 也接受 ISO，方便界面与手写
    try:
        return datetime.fromisoformat(v).date()
    except ValueError as e:
        raise RuleError(f"UNTIL 无法解析：{value!r}（用 20261231 或 2026-12-31）") from e


def parse(text: Any) -> Rule:
    """解析重复规则。不合法就抛 `RuleError`（不做静默兜底）。"""
    if text is None:
        raise RuleError("重复规则为 None")
    s = str(text).strip()
    if not s:
        raise RuleError("重复规则为空串")

    if not s.upper().startswith("FREQ="):
        key = s.lower()
        if key not in _SHORT:
            raise RuleError(f"未知重复规则：{text!r}（支持 daily/weekly/monthly/yearly 或 FREQ=…）")
        return Rule(freq=_SHORT[key], raw=s)

    parts: dict[str, str] = {}
    for seg in s.split(";"):
        seg = seg.strip()
        if not seg:
            continue
        if "=" not in seg:
            raise RuleError(f"RRULE 片段缺少 '='：{seg!r}")
        k, v = seg.split("=", 1)
        k = k.strip().upper()
        if k in parts:
            raise RuleError(f"RRULE 里 {k} 出现两次")
        parts[k] = v.strip()

    unknown = set(parts) - {"FREQ", "INTERVAL", "BYDAY", "BYMONTHDAY", "UNTIL", "COUNT",
                            "X-QD-WORKDAY", "X-QD-CALENDAR"}
    if unknown:
        # 明确拒绝而不是忽略：忽略了用户会以为生效了（宁缺毋假）
        raise RuleError(f"本实现不支持的 RRULE 键：{sorted(unknown)}"
                        "（支持 FREQ/INTERVAL/BYDAY/BYMONTHDAY/UNTIL/COUNT）")

    freq = parts.get("FREQ", "").upper()
    if freq not in FREQS:
        raise RuleError(f"FREQ 只能是 {list(FREQS)}，收到 {parts.get('FREQ')!r}")

    interval = 1
    if "INTERVAL" in parts:
        try:
            interval = int(parts["INTERVAL"])
        except ValueError as e:
            raise RuleError(f"INTERVAL 必须是整数：{parts['INTERVAL']!r}") from e
        if not 1 <= interval <= 1000:
            raise RuleError(f"INTERVAL 需在 1..1000，收到 {interval}")

    byday: tuple[int, ...] = ()
    if "BYDAY" in parts:
        if freq != "WEEKLY":
            raise RuleError("BYDAY 只在 FREQ=WEEKLY 下支持（-1MO 这类序数形式本实现不做）")
        days: list[int] = []
        for tok in (t.strip().upper() for t in parts["BYDAY"].split(",")):
            if not tok:
                continue
            if tok not in _WEEKDAYS:
                raise RuleError(f"BYDAY 只能是 MO/TU/WE/TH/FR/SA/SU，收到 {tok!r}")
            wd = _WEEKDAYS[tok]
            if wd not in days:
                days.append(wd)
        if not days:
            raise RuleError("BYDAY 为空")
        byday = tuple(sorted(days))

    bymonthday: tuple[int, ...] = ()
    if "BYMONTHDAY" in parts:
        if freq not in ("MONTHLY", "YEARLY"):
            raise RuleError("BYMONTHDAY 只在 FREQ=MONTHLY/YEARLY 下支持")
        md: list[int] = []
        for tok in (t.strip() for t in parts["BYMONTHDAY"].split(",")):
            if not tok:
                continue
            try:
                n = int(tok)
            except ValueError as e:
                raise RuleError(f"BYMONTHDAY 必须是整数：{tok!r}") from e
            if n == 0 or not -31 <= n <= 31:
                raise RuleError(f"BYMONTHDAY 需在 -31..-1 或 1..31，收到 {n}")
            if n not in md:
                md.append(n)
        if not md:
            raise RuleError("BYMONTHDAY 为空")
        bymonthday = tuple(sorted(md))

    until = _parse_until(parts["UNTIL"]) if "UNTIL" in parts else None

    count = None
    if "COUNT" in parts:
        try:
            count = int(parts["COUNT"])
        except ValueError as e:
            raise RuleError(f"COUNT 必须是整数：{parts['COUNT']!r}") from e
        if count < 1:
            raise RuleError(f"COUNT 需 >= 1，收到 {count}")

    if until is not None and count is not None:
        raise RuleError("UNTIL 与 COUNT 不能同时出现（RFC 5545 同样禁止）")

    policy = parts.get("X-QD-WORKDAY", "").upper() or None
    calendar_id = parts.get("X-QD-CALENDAR", "").upper() or None
    if (policy is None) != (calendar_id is None):
        raise RuleError("X-QD-WORKDAY 与 X-QD-CALENDAR 必须同时指定")
    if policy is not None and policy not in {"SKIP", "NEXT"}:
        raise RuleError("X-QD-WORKDAY 只支持 SKIP（跳过）或 NEXT（顺延）")
    if calendar_id is not None and calendar_id != workdays.CALENDAR_ID:
        raise RuleError(f"不支持的工作日历版本：{calendar_id}，请更新客户端")
    return Rule(freq=freq, interval=interval, byday=byday, bymonthday=bymonthday,
                until=until, count=count, workday=policy, calendar_id=calendar_id, raw=s)


def try_parse(text: Any) -> Rule | None:
    """视图层专用：另一台机器同步来的畸形规则**不许把整个日历带崩**。

    与 `parse` 的区别只在于失败时返回 None；写路径一律用 `parse`（要让错误可见）。
    """
    if not text:
        return None
    try:
        return parse(text)
    except RuleError:
        return None


# --------------------------------------------------------------------------- 日期推进


def _as_dt(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    s = str(value).strip().replace("Z", "+00:00")
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is not None:                 # 折算到本地墙钟，与 view.parse_dt 一致
        dt = dt.astimezone().replace(tzinfo=None)
    return dt


def _month_len(y: int, m: int) -> int:
    return calendar.monthrange(y, m)[1]


def _shift_month(y: int, m: int, delta: int) -> tuple[int, int]:
    idx = y * 12 + (m - 1) + delta
    return idx // 12, idx % 12 + 1


def _month_days(y: int, m: int, rule: Rule, anchor_day: int) -> list[int]:
    """某个月里该规则命中的日号（升序、去重）。

    两种情况刻意不同，都是可预期的行为：

    - **显式 BYMONTHDAY**：按 RFC 5545，该月没有这一天就**跳过这个月**（`BYMONTHDAY=31`
      不会在 2 月产生一次）。想要「每月最后一天」用 `-1`，它永远存在。
    - **隐式锚日**（没写 BYMONTHDAY）：夹取到当月最后一天。1 月 31 日的每月任务
      在 2 月落 28/29 日 —— 跳过整个 2 月会让用户以为任务丢了。
    """
    length = _month_len(y, m)
    if rule.bymonthday:
        out = set()
        for md in rule.bymonthday:
            day = (length + md + 1) if md < 0 else md
            if 1 <= day <= length:            # 显式日号：不存在就跳过该月
                out.add(day)
        return sorted(out)
    return [min(anchor_day, length)]


def _dates(anchor: date, rule: Rule) -> Iterator[date]:
    """从 anchor 出发的候选日期流（升序，可能有早于 anchor 的头部，由调用方过滤）。"""
    step = rule.interval
    if rule.freq == "DAILY":
        for k in range(_MAX_STEPS + 1):
            yield anchor + timedelta(days=step * k)
    elif rule.freq == "WEEKLY":
        if rule.byday:
            monday = anchor - timedelta(days=anchor.weekday())
            for wk in range(_MAX_STEPS + 1):
                for wd in rule.byday:
                    yield monday + timedelta(weeks=step * wk, days=wd)
        else:
            for k in range(_MAX_STEPS + 1):
                yield anchor + timedelta(weeks=step * k)
    elif rule.freq == "MONTHLY":
        for k in range(_MAX_STEPS + 1):
            y, m = _shift_month(anchor.year, anchor.month, step * k)
            for dd in _month_days(y, m, rule, anchor.day):
                yield date(y, m, dd)
    else:                                     # YEARLY
        for k in range(_MAX_STEPS + 1):
            y = anchor.year + step * k
            for dd in _month_days(y, anchor.month, rule, anchor.day):
                try:
                    yield date(y, anchor.month, dd)
                except ValueError:            # 夹取后仍非法（理论上不会）就跳过该次
                    continue


def occurrences(anchor: Any, rule: Rule, limit: int = _MAX_STEPS) -> Iterator[date]:
    """合法日期序列：从 anchor 起、升序、去重、按 UNTIL/COUNT 截断。"""
    a = _as_dt(anchor).date()
    emitted = 0
    last: date | None = None
    for d in _dates(a, rule):
        if d < a:
            continue
        if rule.until is not None and d > rule.until:
            return
        if rule.workday:
            try:
                if rule.workday == "SKIP":
                    if not workdays.is_workday(d, rule.calendar_id):
                        continue
                else:
                    d = workdays.on_or_after(d, rule.calendar_id)
            except workdays.CalendarUnavailable as error:
                raise CalendarError(error) from None
        if last is not None and d <= last:     # BYMONTHDAY 夹取可能撞日（如 30 与 -1）
            continue
        if rule.until is not None and d > rule.until:
            return
        last = d
        emitted += 1
        yield d
        if rule.count is not None and emitted >= rule.count:
            return
        if emitted >= int(limit):
            return


def next_after(due: Any, rule: Rule | str | None, anchor: Any = None) -> datetime | None:
    """当前这一次 `due` 之后的下一次；规则用尽（UNTIL/COUNT 到头）返回 None。

    `anchor` 是第一次的日期（`repeat_start`）。COUNT 与 INTERVAL 的对齐都靠它；
    缺失时退化为以 `due` 为锚 —— 老数据没有 anchor，宁可算得保守也不能抛。
    """
    if rule is None:
        return None
    r = rule if isinstance(rule, Rule) else try_parse(rule)
    if r is None:
        return None
    dt = _as_dt(due)
    target = dt.date()
    a = _as_dt(anchor).date() if anchor else target
    if a > target:                             # anchor 晚于 due（用户改过到期日）→ 以 due 为准
        a = target
    for d in occurrences(a, r):
        if d > target:
            return dt.replace(year=d.year, month=d.month, day=d.day)
    return None


def upcoming(due: Any, rule: Rule | str | None, n: int = 5, anchor: Any = None,
             end: Any = None, warnings: list[dict] | None = None) -> list[datetime]:
    """`due` 之后的 n 次（可选限制到 `end` 日期），给界面预览与日历展开用。"""
    if rule is None or int(n) <= 0:
        return []
    r = rule if isinstance(rule, Rule) else try_parse(rule)
    if r is None:
        return []
    dt = _as_dt(due)
    target = dt.date()
    a = _as_dt(anchor).date() if anchor else target
    if a > target:
        a = target
    end_d = _as_dt(end).date() if end else None
    out: list[datetime] = []
    try:
        # Stop nominal iteration at the requested window before looking up an
        # unknown future year. UNTIL and end both bound effective dates.
        from dataclasses import replace
        bounded = replace(r, until=min(r.until, end_d) if r.until and end_d else r.until or end_d)
        for d in occurrences(a, bounded):
            if d <= target:
                continue
            out.append(dt.replace(year=d.year, month=d.month, day=d.day))
            if len(out) >= int(n):
                break
    except CalendarError as error:
        if warnings is None:
            raise
        warnings.append({"key": "calendar_missing_year", "calendar_id": error.calendar_id,
                         "year": error.year, "message": str(error)})
    return out


# --------------------------------------------------------------------------- 展示


def summary(rule: Rule | str | None) -> dict[str, Any]:
    """给前端的**机器键**摘要，前端只翻译、不解析 RRULE。

    前端自己拆 RRULE 就是「已逾期硬编码进响应体」那一类重复实现泄漏，所以拆解在后端做。
    """
    r = rule if isinstance(rule, Rule) else try_parse(rule)
    if r is None:
        return {}
    out: dict[str, Any] = {"interval": r.interval}
    if r.interval == 1 and not r.byday and not r.bymonthday:
        out["key"] = _FREQ_KEY[r.freq]
    else:
        out["key"] = _FREQ_N_KEY[r.freq]
        out["n"] = r.interval
    if r.byday:
        out["days"] = [_DAY_KEYS[i] for i in r.byday]
    if r.bymonthday:
        out["monthdays"] = list(r.bymonthday)
    if r.until is not None:
        out["until"] = r.until.isoformat()
    if r.count is not None:
        out["count"] = r.count
    if r.workday:
        out["workday"] = r.workday
        out["calendar_id"] = r.calendar_id
    return out
