"""分层铁律：Core 不许依赖 GUI / Web 框架（开发文档 §3）。

用源码扫描实现，零依赖、跑得快。违反一次即架构腐化，值得单独一条红线。
"""
from __future__ import annotations

import ast
from pathlib import Path

CORE = Path(__file__).resolve().parents[1] / "src" / "qingdan" / "core"

FORBIDDEN = {
    "fastapi", "uvicorn", "starlette", "pywebview", "webview",
    "PySide6", "PyQt5", "PyQt6", "tkinter", "clr", "winotify", "pystray",
}


def _imports_of(py: Path) -> set[str]:
    tree = ast.parse(py.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module.split(".")[0])
    return found


def test_core_has_no_gui_or_web_dependency():
    files = sorted(CORE.glob("*.py"))
    assert files, f"没找到 core 源码：{CORE}"
    offenders = {f.name: sorted(_imports_of(f) & FORBIDDEN) for f in files}
    bad = {k: v for k, v in offenders.items() if v}
    assert not bad, f"Core 层引入了上层依赖：{bad}"


def test_ui_layer_is_absent_until_m2():
    """M2 才建 web 前端；现在出现 ui/ 说明有人绕过接口层写业务。"""
    assert not (CORE.parent / "ui").exists()
