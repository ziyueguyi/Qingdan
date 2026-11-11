"""前后端数据契约：前端读到的形状必须和后端返回的一致。

起因是真实 bug —— store.js 把 `group.list` 的 `{total, items}` 信封整个赋给
`state.groups`，渲染时 `state.groups.map is not a function` 直接白屏。
这类错误只有浏览器跑起来才暴露，所以在这里用断言提前拦住。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from qingdan.core.registry import call

WEB = Path(__file__).resolve().parents[1] / "src" / "qingdan" / "web"
PKG = WEB.parent                              # src/qingdan

TODAY = "2026-11-05"


def api(feature: str, params: dict | None = None):
    """registry.call 的第二个参数是仅位置的 **raw，只能用 ** 展开。"""
    return call(feature, **(params or {}))


@pytest.fixture
def seeded(app_ctx):
    """造一批各类型数据，保证集合类接口不是空数组（空数组会掩盖形状错误）。"""
    g = call("group.create", name="工作任务", color="#b8730f")
    t = call("task.create", title="写周报", list_id=g["id"], priority=1,
             due_at=f"{TODAY}T20:00:00", tags=["重要"])
    call("tag.create", name="重要")
    call("folder.create", name="工作")
    call("section.create", list_id=g["id"], name="本周")
    call("filter.create", name="逾期", criteria={"overdue": True})
    call("countdown.create", title="统考", target_date="2026-12-01")
    call("note.append", item_id=t["id"], text="备注一条")
    return {"group": g["id"], "task": t["id"]}


# --------------------------------------------------------------------------- 集合类返回信封


@pytest.mark.parametrize("feature,params", [
    ("group.list", {}), ("tag.list", {}), ("folder.list", {}), ("section.list", {}),
    ("filter.list", {}), ("countdown.list", {}), ("task.list", {}),
    ("search.query", {"q": "周报"}),
])
def test_collection_endpoints_return_total_and_items(app_ctx, seeded, feature, params):
    out = api(feature, params)
    assert isinstance(out, dict), f"{feature} 不该返回裸数组"
    assert isinstance(out["items"], list), f"{feature}.items 必须是数组"
    assert out["items"], f"{feature} 演示数据为空，形状断言会被空数组掩盖"
    assert out["total"] == len(out["items"])


def test_view_query_returns_items_and_groups(app_ctx, seeded):
    for name in ("today", "next7", "inbox", "done", "trash", "all"):
        out = api("view.query", {"name": name, "today": TODAY})
        assert isinstance(out["items"], list), name
        assert isinstance(out["groups"], list), name
        assert out["total"] == len(out["items"]), name


def test_view_board_returns_columns(app_ctx, seeded):
    out = api("view.board", {"group_by": "status", "today": TODAY})
    assert isinstance(out["columns"], list) and out["columns"]
    for c in out["columns"]:
        assert {"key", "label", "total", "items"} <= set(c)


def test_view_calendar_returns_cells(app_ctx, seeded):
    out = api("view.calendar", {"days": 7, "today": TODAY})
    assert len(out["cells"]) == 7
    cell = out["cells"][0]
    assert {"date", "in_month", "is_today", "count", "events"} <= set(cell)
    assert isinstance(cell["events"], list)


def test_app_health_has_every_field_the_shell_reads(app_ctx, seeded):
    """app.js / 状态栏读的键，一个都不能少。"""
    h = api("app.health")
    for key in ("ok", "version", "device_id", "items", "tasks", "trash_count",
                "dirty", "authed", "data_dir"):
        assert key in h, f"app.health 缺字段 {key}"


def test_stats_today_has_every_field_the_shell_reads(app_ctx, seeded):
    s = api("stats.today", {"today": TODAY})
    for key in ("today", "open_total", "overdue", "due_today", "done_today",
                "by_priority", "countdown_soon", "groups", "tags"):
        assert key in s, f"stats.today 缺字段 {key}"


def test_countdown_items_expose_derived_days(app_ctx, seeded):
    out = api("countdown.list", {"today": TODAY})["items"][0]
    for key in ("id", "title", "target_date", "days", "direction", "data"):
        assert key in out, f"countdown.list 缺字段 {key}"


def test_prefs_endpoints_shape(app_ctx, seeded):
    api("app.pref_set", {"prefs": {"theme": "dark"}})
    api("view.pref_set", {"prefs": {"layout": "board"}})
    assert api("app.pref_get")["prefs"]["theme"] == "dark"
    assert api("view.pref_get")["prefs"]["layout"] == "board"
    assert api("app.pref_get")["synced"] is False


def test_task_dto_shape(app_ctx, seeded):
    d = api("task.get", {"item_id": seeded["task"]})
    assert {"schema", "kind", "id", "rev", "deleted", "deleted_at_ms", "data", "meta"} <= set(d)
    assert isinstance(d["rev"], str) and d["rev"].count(".") == 2, "rev 必须是 ms.seq.dev 字符串"


def test_quick_add_shape(app_ctx, seeded):
    out = api("task.quick_add", {"text": "明天下午3点 交周报 #工作任务 !P1", "today": TODAY})
    assert out["created"] is None
    for key in ("title", "due_at", "list_id", "tags", "priority", "matched", "warnings"):
        assert key in out["parsed"], f"parsed 缺字段 {key}"


def test_board_group_by_is_mapped_before_the_call(app_ctx):
    """UI 的分组下拉能给出 date/tag/none，而后端 view.board 只认 status/group/priority。
    前端不先映射，切到看板就是一片空白（实测踩过，且只有一条 422 藏在 toast 里）。"""
    src = (WEB / "store.js").read_text(encoding="utf-8")
    m = re.search(r'const by = \[([^\]]+)\]\.includes\(state\.group\)', src)
    assert m, "store.js 必须把 state.group 映射到看板支持的 group_by"
    mapped = set(re.findall(r'"(\w+)"', m.group(1)))
    assert mapped == {"status", "group", "priority"}, f"映射表与后端支持不一致：{mapped}"
    # 后端确实拒绝 date —— 证明这条映射不是多余的
    with pytest.raises(Exception):
        api("view.board", {"group_by": "date"})


def test_toolbar_group_options_cover_what_the_ui_sends(app_ctx):
    """工具栏分组下拉给出的值，必须是 view.query 的 group 参数接受的枚举。"""
    from qingdan.core import view
    js = (WEB / "app.js").read_text(encoding="utf-8")
    ui = set(re.findall(r'\["(\w+)",\s*"g_', js))
    assert ui <= set(view.GROUP_MODES), f"UI 给出了后端不支持的分组方式：{ui - set(view.GROUP_MODES)}"


# --------------------------------------------------------------------------- 静态一致性


def _state_body() -> str:
    src = (WEB / "store.js").read_text(encoding="utf-8")
    return src[src.index("export const state"):]


@pytest.mark.parametrize("name", ["app.js", "store.js"])
def test_frontend_only_reads_declared_state_keys(name):
    """前端读 state.foo 但 store 里没声明这个键 → 一定是 undefined，渲染期才炸。"""
    keys = set(re.findall(r"^\s{2}(\w+):", _state_body(), re.M))
    js = (WEB / name).read_text(encoding="utf-8")
    used = set(re.findall(r"\bstate\.(\w+)", js))
    missing = used - keys
    assert not missing, f"{name} 读了未声明的 state 键：{sorted(missing)}"


def test_array_typed_state_keys_are_initialized_as_arrays():
    """被 `.map(` 用的 state 键，初始值必须是数组，不能是信封对象。"""
    declared = dict(re.findall(r"^\s{2}(\w+):\s*(.+?),?\s*(?://.*)?$", _state_body(), re.M))
    js = (WEB / "app.js").read_text(encoding="utf-8")
    for key in sorted(set(re.findall(r"state\.(\w+)\.map\(", js))):
        initial = declared.get(key, "")
        assert initial.startswith("["), f"state.{key} 被 .map 使用但初始值不是数组：{initial!r}"


# --------------------------------------------------------------------------- 冲突面板契约

def _js_table(js: str, table: str) -> set[str]:
    """取出 `const X = { key: "..." }` 的键集合。

    一行可能写好几个键（i18n 风格就是这样排的），所以不能用行首锚定 ——
    第一版用 `^\\s*(\\w+):` 只认到每行第一个键，结果把"前端漏了 6 个 kind"
    误报出来。守卫误报和漏报一样有害：它会让人怀疑所有红灯。
    """
    m = re.search(rf"const {table} = \{{(.*?)\}};", js, re.S)
    assert m, f"app.js 里找不到映射表 {table}"
    return set(re.findall(r'(?:^|[{,\s])\s*([a-z_][a-z0-9_]*)\s*:', m.group(1)))


def _emitted_states() -> set[str]:
    """**跑一遍** diff_data 看它真能吐出哪些 state，而不是正则猜源码。

    源码扫描的问题是这样扫出来的是"我以为会产生的值"，跑出来的才是实际值：
    少一个分支、拼错一个字符串，扫描照样通过，界面就退回一个看起来像翻译的错标签。
    """
    from qingdan.core.merge import diff_data

    states: set[str] = set()
    combos = [
        ({"title": "a"}, {"title": "b"}, {"title": "a"}),      # 只有远端改
        ({"title": "a"}, {"title": "b"}, {"title": "b"}),      # 只有本地改
        ({"title": "a"}, {"title": "b"}, {"title": "c"}),      # 两边都改
        (None, {"title": "b"}, {"title": "b"}),                # 本机没有
        ({"title": "a"}, None, {"title": "a"}),                # 另一台没有
    ]
    for local, remote, base in combos:
        for row in diff_data("task", local, remote, base):
            states.add(row["state"])
    return states


def test_data_dir_source_labels_exist():
    """后端 `DIR_SOURCES` 里登记的每一种来源，前端都要有对应文案键。

    来源集合直接读常量而不是正则扫源码：扫出来的"我以为会产生哪些值"一旦和代码脱节，
    守卫就变成摆设；常量是权威，界面漏配就红。
    """
    from qingdan.core.context import DIR_SOURCES

    js = (WEB / "app.js").read_text(encoding="utf-8")
    m = re.search(r"const DIR_SOURCE_KEY = \{(.*?)\};", js, re.S)
    assert m, "app.js 里找不到 DIR_SOURCE_KEY 表"
    keys = set(re.findall(r'(?:^|[{,\s])\s*(\w+)\s*:', m.group(1)))
    missing = DIR_SOURCES - keys
    assert not missing, f"这些数据目录来源没有前端文案：{sorted(missing)}"
    # 反向也要成立：前端留了个后端永远不会产生的键，说明两边已经不同步
    assert not keys - DIR_SOURCES, f"前端有后端不会用到的来源键：{sorted(keys - DIR_SOURCES)}"


def test_js_table_scanner_is_not_blind():
    """守卫自己也要测：一行塞两个键必须都认出来。

    第一版就是漏在这里，把"前端少写 6 个 kind"误报成缺陷 —— 假阳性比没测试更糟，
    它教会大家忽略红灯。
    """
    js = 'const T = {\n  a: "x", b: "y",\n  c: "z"\n};\n'
    assert _js_table(js, "T") == {"a", "b", "c"}


def test_conflict_states_all_have_frontend_labels():
    js = (WEB / "app.js").read_text(encoding="utf-8")
    emitted = _emitted_states()
    assert len(emitted) >= 5, f"只跑出 {sorted(emitted)}，diff_data 的行为和这条测试脱节了"
    missing = emitted - _js_table(js, "CS_STATE_KEY")
    assert not missing, f"这些冲突状态没有前端文案，会退回错标签：{sorted(missing)}"


def test_conflict_kind_and_reason_tables_are_complete():
    import ast

    from qingdan.core.models import KINDS

    js = (WEB / "app.js").read_text(encoding="utf-8")
    miss_kind = set(KINDS) - _js_table(js, "KIND_LABEL_KEY")
    assert not miss_kind, f"这些 kind 出现在冲突里会没有名字：{sorted(miss_kind)}"

    src = (PKG / "core" / "sync.py").read_text(encoding="utf-8")
    reasons = set()
    for node in ast.walk(ast.parse(src)):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "record_conflict" and len(node.args) >= 2
                and isinstance(node.args[1], ast.Constant)):
            reasons.add(node.args[1].value)
    assert reasons, "sync.py 里没找到 record_conflict 的原因常量，这条守卫要跟着改"
    miss_reason = reasons - _js_table(js, "CONFLICT_REASON_KEY")
    assert not miss_reason, f"这些冲突原因没有前端文案：{sorted(miss_reason)}"


def test_conflict_field_label_keys_exist_in_both_languages():
    """后端给的 `f_*` 机器键必须中英两边都有译文，否则界面上就是裸键名。"""
    from test_web import _dict_section, _has_key      # 复用同一套解析，别写第二份

    src = (WEB / "i18n.js").read_text(encoding="utf-8")
    zh, en = _dict_section(src, "zh"), _dict_section(src, "en")
    merge_src = (PKG / "core" / "merge.py").read_text(encoding="utf-8")
    keys = set(re.findall(r':\s*\("(f_[a-z_]+)"\s*,', merge_src))
    assert len(keys) >= 20, f"只扫到 {len(keys)} 个字段元数据，八成是 FIELD_META 格式变了"
    missing = [k for k in sorted(keys) if not _has_key(zh, k) or not _has_key(en, k)]
    assert not missing, f"字段名缺译文：{missing}"
