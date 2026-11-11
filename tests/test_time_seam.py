"""统一时间缝的可执行守卫（`core/stamp.py` 那条规矩的制度版）。

规矩：凡是要**和被写入数据的时间戳做比较**的，都必须从 `stamp` 取时间。
写成文档挡不住下一次赶工 —— 2026-11-10 实测就一次性扫出 6 处绕开时间缝的地方
（`alert.status` 的 pending 计数、`dispatch` 的默认 now、`alert.silence` 写
`silence_until`、`alert.preview` 的地平线、`compact` 的墓碑截止、toast 测试发送），
其中 3 处会让「钉住时钟的测试」在真实墙钟下给出不同答案。

这里用 AST 静态扫描，豁免表必须写清理由；**豁免表里的条目如果已经不再使用墙钟，
同样算失败** —— 否则豁免表只会越堆越长，最后变成一张"允许绕开"的白名单。
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "qingdan"

#: 允许直接读墙钟的文件 → 理由。理由必须能说清"为什么不参与数据时间戳的比较"。
EXEMPT = {
    "core/stamp.py": "时间缝本身：唯一被允许持有 datetime.now 的实现处",
    "core/clock.py": "HLC 版本戳发号，有自己的语义（含服务器 offset 与回拨钳制）",
    "core/gitee.py": "时钟偏差校准：必须用真实墙钟与服务器 Date 头比较，走时间缝会让"
                     "校准在钉住时钟的测试里恒为 0（last_offset_ms 恒 0 就是这么来的）",
    "core/backup.py": "备份文件名、快照 mtime 与 prune：真实时刻，不参与『今天』裁决",
    "core/models.py": "ULID 的时间前缀只用于 ID 可排序，不与业务时间戳比较",
    "core/sync.py": "bootstrap_done / last_sync_at 是日志式标记，只用于展示",
    "core/sync_api.py": "prepared_at 同上，日志式标记",
    "core/access.py": "独立安全库的 Token 过期和访问审计使用真实时间，不参与任务日期裁决；"
                      "任务模拟时钟不能延长或提前撤销真实凭据的有效期",
    "shell/app.py": "启动等待循环的 deadline",
    "shell/tray.py": "托盘等待循环的 deadline",
}

#: 既抓调用 `time.time()`，也抓裸引用 `datetime.now`（当默认值/赋值传递同样会绕开时间缝）
_WALL_ATTRS = {"time": "time", "now": "datetime", "utcnow": "datetime"}


def _injected_clock_nodes(tree: ast.AST) -> set[int]:
    """标记「作为函数参数默认值出现的墙钟引用」。

    `def __init__(self, tick: Callable[[], float] = time.time)` 是**依赖注入**：
    量的是真实经过时间（重试退避、扫描循环间隔），不与数据时间戳比较，而且测试
    可以整个替换掉。把它当成绕开时间缝会逼出错误的豁免表，所以精确识别并跳过 ——
    同一个文件里别处真的写 `time.time() * 1000` 仍然会被抓。
    """
    ids: set[int] = set()
    for fn in ast.walk(tree):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            defaults = list(fn.args.defaults) + [d for d in fn.args.kw_defaults if d is not None]
            for d in defaults:
                for sub in ast.walk(d):
                    ids.add(id(sub))
    return ids


def _wall_clock_files() -> set[str]:
    """扫描 src 下所有直接读墙钟的文件（相对路径，用 / 分隔）。"""
    hits: set[str] = set()
    for py in SRC.rglob("*.py"):
        if "__pycache__" in py.parts:
            continue
        tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        injected = _injected_clock_nodes(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Attribute) or node.attr not in _WALL_ATTRS:
                continue
            if id(node) in injected:
                continue
            owner = node.value
            if isinstance(owner, ast.Name) and _WALL_ATTRS[node.attr] == owner.id:
                hits.add(py.relative_to(SRC).as_posix())
                break
    return hits


def test_no_wall_clock_outside_the_exempt_list():
    got = _wall_clock_files()
    stray = got - set(EXEMPT)
    assert not stray, (
        f"这些文件绕开了统一时间缝，请改用 stamp.now_ms()/now_dt()/now_text()：{sorted(stray)}。"
        " 如果确实不该走时间缝（如文件名、HLC、等待循环），把它加进 EXEMPT 并写清理由。"
    )


def test_exempt_list_has_no_stale_entries():
    """豁免表不许留死条目，否则它会慢慢变成一张『随便绕开』的白名单。"""
    got = _wall_clock_files()
    stale = set(EXEMPT) - got
    assert not stale, f"这些文件已经不再读墙钟，应从 EXEMPT 移除：{sorted(stale)}"


def test_exempt_files_exist():
    missing = [f for f in EXEMPT if not (SRC / f).exists()]
    assert not missing, f"豁免表指向不存在的文件：{missing}"


def test_alert_and_compact_paths_are_on_the_seam():
    """点名钉住这次修掉的四处：它们都会被钉住时钟的测试依赖。"""
    for rel in ("core/alert_api.py", "core/alerting/scheduler.py", "core/compact.py",
                "adapters/toast.py"):
        assert rel not in _wall_clock_files(), f"{rel} 又绕开时间缝了"
        text = (SRC / rel).read_text(encoding="utf-8")
        assert "stamp." in text, f"{rel} 没有引用 stamp，时间从哪来的？"


def test_only_stamp_holds_the_bare_clock_reference():
    """收敛点必须唯一：只有 `stamp.py` 可以持有 `datetime.now` 这个**引用**。

    别处出现 `datetime.now` 引用（例如当默认参数传递）等于又开了一个时间源，
    而它不会被上面的调用扫描抓到 —— 这条专门堵那个盲区。
    """
    holders = []
    for py in SRC.rglob("*.py"):
        if "__pycache__" in py.parts:
            continue
        rel = py.relative_to(SRC).as_posix()
        if re.search(r"datetime\.now(?!\()", py.read_text(encoding="utf-8")):
            holders.append(rel)
    assert holders == ["core/stamp.py"], f"持有裸 datetime.now 引用的应当只有 stamp.py，实际：{holders}"
