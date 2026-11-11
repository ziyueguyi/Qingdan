"""前端资产与「主题/语言不写死」的可执行守卫。

这些不是风格检查：用户明确要求主题和语言必须运行时可切换。
只写在文档里会在下一次赶工时被破坏，所以做成测试。
"""
from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from qingdan.api.http import build_app, web_root

WEB = Path(__file__).resolve().parents[1] / "src" / "qingdan" / "web"


@pytest.mark.parametrize("f", sorted(p.name for p in WEB.glob('*.js')))
def test_frontend_js_parses(f):
    """前端语法守卫。

    2026-11-10 实测：模板表达式里漏一个 `}`（`title="${esc(t("x"))">`）能让整个界面
    停在「…」——控制台不报、pytest 全绿、只有用户看见白屏。浏览器自动化截图也只会
    拍到一片灰。所以用 `node --check` 把语法变成测试：拷贝成 .mjs 以支持 import/export。
    """
    node = shutil.which("node")
    if not node:
        pytest.skip("机器上没有 node，跳过前端语法守卫")
    with tempfile.TemporaryDirectory() as d:
        dst = Path(d) / (f + ".mjs")
        dst.write_text((WEB / f).read_text(encoding="utf-8"), encoding="utf-8")
        r = subprocess.run([node, "--check", str(dst)], capture_output=True, text=True)
        assert r.returncode == 0, f"{f} 语法错误：{r.stderr.strip()[:400]}"


def read(name: str) -> str:
    return (WEB / name).read_text(encoding="utf-8")


# --------------------------------------------------------------------------- 资源存在性


def test_settings_tab_keys_match_body_keys():
    """页签键与内容键必须一一对应。曾经页签写 "lang"、内容写 "language"，
    点进去直接渲染成字面量 undefined。

    内容可以是内联模板也可以是函数调用，所以只匹配键名 —— 之前连模板引号一起匹配，
    把 `alert: renderAlertTab()` 这种合法写法误判成缺失。
    """
    js = read("app.js")
    start = js.index("function openSettings")
    block = js[start:js.index("// --------------------------------------------------------------------------- 告警设置页", start)] \
        if "// --------------------------------------------------------------------------- 告警设置页" in js[start:] else js[start:]
    body_start = block.index("const body = {")
    body_end = block.index("}[tab]", body_start)
    tabs = set(re.findall(r'\[\s*"([a-z_]+)",\s*"tab_', block))
    bodies = set(re.findall(r"^    ([a-z_]+):", block[body_start:body_end], re.M))
    assert tabs == bodies, f"页签 {sorted(tabs)} 与内容 {sorted(bodies)} 不一致"


@pytest.mark.parametrize("f", ["index.html", "app.css", "api.js", "i18n.js", "store.js", "app.js"])
def test_frontend_asset_exists(f):
    assert (WEB / f).is_file(), f"缺少前端文件 {f}"


def test_hidden_attribute_is_not_overridden():
    """`.mask{display:grid}` / `.snackbar{display:flex}` 会盖掉 [hidden]，
    空弹窗和提示条一直显示、整页发灰 —— 实测踩过，必须有守卫。"""
    css = read("app.css")
    assert re.search(r"\[hidden\]\s*\{[^}]*display\s*:\s*none\s*!important", css), \
        "缺少 [hidden]{display:none !important}"


def test_collapse_controls_are_wired_in_real_app():
    """折叠侧栏/详情不能只活在原型里（2026-11-10 用户在真程序里找按钮没找到）。

    钉四件事：两侧开关与还原按钮都在真代码里、折叠样式在、还原按钮元素在、
    偏好键进后端白名单（否则跨会话保留会 422）。
    """
    js = read("app.js")
    html = read("index.html")
    for act in ('data-act="fold-sb"', 'data-act="fold-dt"'):
        assert act in js, f"真代码缺折叠入口 {act}"
    assert 'data-act="unfold-dt"' in html, "还原按钮是静态元素，应在 index.html 里"
    assert "function applyCollapse" in js and "function setCollapse" in js
    css = read("app.css")
    for sel in (".app.sb-min{", ".app.dt-min{", ".dt-restore{",
                ".app.dt-min .dt-restore{display:block}"):
        assert sel in css, f"缺折叠样式 {sel}"
    assert 'id="dt-restore"' in html, "缺详情还原按钮元素"
    from qingdan.core.views_api import PREF_KEYS
    assert {"sb_min", "dt_min", "folded_folders"} <= PREF_KEYS, "折叠偏好没进后端白名单"


def test_statusbar_is_compact_and_responsive():
    """2026-11-10 用户两条要求：① 状态栏去掉数据库段；② 窗口缩到最小时条目只显示图标。

    数据库位置的信息没有丢 —— 设置页 → 数据 仍是唯一权威展示处（全路径 + 来源文案）。
    状态栏只留扫一眼就要知道的数，窄窗口下文字标签隐藏、图标与数值保留。
    """
    js = read("app.js")
    start = js.index("function renderStatusbar")
    fn = js[start:js.index("\n}\n", start)]
    assert "status_db" not in fn, "状态栏不该再显示数据库位置（权威展示处是设置页 → 数据）"
    assert 'class="st-i' in fn, "状态栏条目缺图标结构"
    assert 'class="dot"' in fn, "在线状态必须是绿/灰点"
    css = read("app.css")
    assert re.search(r"@media \(max-width: 900px\)\{[^}]*\.statusbar \.st-l\{display:none\}", css), \
        "缺窄窗口下隐藏文字标签的媒体查询"
    assert ".statusbar .mode.ok .dot{background:var(--ok)}" in css, "已登录状态点必须是绿色"


def test_task_context_menu_is_wired():
    """任务右键菜单（2026-11-10 用户参照滴答要求）：监听、容器、样式、派发缺一不可。
    只允许有后端支撑的动作进菜单（宁缺毋假），所以这里也钉住派发 case 的存在。"""
    js = read("app.js")
    assert 'addEventListener("contextmenu"' in js, "缺右键监听"
    assert "async function openTaskMenu" in js and "function closeTaskMenu" in js
    for act in ("ctx-toggle", "ctx-due", "ctx-prio", "ctx-skip", "ctx-snooze",
                "ctx-dup", "ctx-edit", "ctx-del"):
        assert f'case "{act}"' in js, f"右键菜单项 {act} 没有派发实现"
    assert 'id="ctxmenu"' in read("index.html"), "缺菜单容器"
    assert ".ctxmenu{position:fixed" in read("app.css"), "缺菜单样式"


def test_row_tooltips_are_localized():
    """行尾按钮 tooltip 必须走 i18n。曾经硬编码 `title="zzz"`：中文界面悬停看到三个 z，
    用户只能猜（2026-11-10 用户问"zzz 这个按钮是干嘛的"即由此而来）。"""
    js = read("app.js")
    assert 'title="zzz"' not in js, "tooltip 不许硬编码英文"
    assert "snooze_t" in js and "snooze_t" in read("i18n.js")


def test_new_task_dialog_defaults_due_to_tomorrow_now():
    """2026-11-10 用户要求：新建弹窗默认到期 = 明天此刻；编辑保留原值。"""
    js = read("app.js")
    assert "function defaultDueText" in js and "86400000" in js
    assert 'id ? "" : defaultDueText()' in js, \
        "默认到期只该用于新建；编辑已有任务（含没排期的老任务）不能被塞默认值"


def test_default_due_text_is_tomorrow_at_same_time():
    """把默认值函数抽出来用 node 真跑：固定输入 2026-11-10T16:05 → 必须得 2026-11-11T16:05。
    跨月/跨年/闰日都由 Date 算术负责，这里钉住"加一天且保住时分"这个契约。"""
    node = shutil.which("node")
    if not node:
        pytest.skip("机器上没有 node")
    m = re.search(r"function defaultDueText\(.*?\n\}", read("app.js"), re.S)
    assert m, "app.js 里找不到 defaultDueText"
    script = m.group(0) + "\nconsole.log(defaultDueText(new Date('2026-11-10T16:05:00')));"
    r = subprocess.run([node, "-e", script], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "2026-11-11T16:05", r.stdout


def test_task_dialog_is_wired_with_all_requested_fields():
    """2026-11-10 用户要求：添加按钮弹窗，含 标题/日期/任务等级/标签/分组；
    并且任务行要有排期入口（详情面板折叠时它是唯一改日期的地方）。"""
    js = read("app.js")
    assert "async function openTaskDialog" in js and "async function taskDialogSave" in js
    for fid in ("tk-title", "tk-due", "tk-allday", "tk-prio", "tk-tags", "tk-list"):
        assert fid in js, f"弹窗缺字段 {fid}"
    for act in ('data-act="new-task"', 'data-act="edit-task"', 'data-act="task-save"'):
        assert act in js, f"缺派发入口 {act}"
    css = read("app.css")
    assert ".chk{" in css, "弹窗复选（全天）缺样式"
    assert "input[type=datetime-local]" in css, "原生日期控件在暗色主题下会白底"
    svc = (Path(__file__).resolve().parents[1] / "src" / "qingdan" / "core" / "service.py") \
        .read_text(encoding="utf-8")
    assert "due_all_day: bool | None = None" in svc, \
        "task.create 必须接受 due_all_day，否则弹窗选的时间会被标成全天"


def test_quickadd_preview_reads_the_parsed_layer():
    """快速添加预览必须读 `parsed` 层（2026-11-10 实测 bug：读 out.due_at 导致预览
    永远显示"未识别"，用户输入了日期却看不到确认，以为无法指定日期/参数）。"""
    js = read("app.js")
    fn = js[js.index("async function previewQuickAdd"):]
    fn = fn[:fn.index("\n}\n")]
    assert "out.parsed" in fn, "预览必须从接口的 parsed 层取识别结果"
    assert "out.due_at" not in fn and "out.tags" not in fn, \
        "预览读错了响应层：{created, parsed} 的字段在 parsed 里"


def test_list_ui_removed_and_task_entry_visible():
    """2026-11-10 用户两条决定，都钉成断言：

    ① **清单/分组的界面入口移除**（试用滴答式两级后决定不要）；后端 list/folder 接口与
       既有数据保留，只是界面不再提供管理入口，任务统一落收集箱。
    ② **添加任务必须有看得见的按钮**：此前入口是一行灰字输入行，用户反馈"没看到按钮"。
       现在工具栏有「＋ 新任务」（聚焦快速添加行），输入行本身也做成带边框的盒子。
    """
    js = read("app.js")
    assert 'data-act="new-group"' not in js, "清单新建入口应已按用户要求移除"
    assert 'data-act="new-folder"' not in js, "分组新建入口应已按用户要求移除"
    assert 'data-act="new-task"' in js, "缺「＋ 新任务」按钮"
    qa = read("app.css").split(".qa{", 1)[1].split("}", 1)[0]
    assert "border:" in qa, "快速添加行必须看起来是个输入框，不能是一行灰字"


def test_static_assets_are_not_cached(app_ctx):
    """本地服务缓存没有收益，只会造成「改了文件但页面还是旧行为」。"""
    client = TestClient(build_app())
    assert client.get("/store.js").headers.get("cache-control") == "no-store"
    assert client.get("/api/v1/health").headers.get("cache-control") == "no-store"


def test_web_root_matches_package_dir():
    assert web_root() == WEB


# --------------------------------------------------------------------------- 静态挂载不抢 API


def test_api_routes_survive_static_mount(app_ctx):
    """Mount("/") 若注册在 API 路由之前，会把 /api/v1/* 全部吃掉。"""
    client = TestClient(build_app())
    r = client.get("/api/v1/health")
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True


def test_index_served_at_root(app_ctx):
    client = TestClient(build_app())
    r = client.get("/")
    assert r.status_code == 200
    assert "<title>清单</title>" in r.text


def test_assets_loadable(app_ctx):
    client = TestClient(build_app())
    for path in ("/app.css", "/api.js", "/i18n.js", "/store.js", "/app.js"):
        r = client.get(path)
        assert r.status_code == 200, f"{path} -> {r.status_code}"


# --------------------------------------------------------------------------- 主题不写死


def test_css_has_no_hardcoded_color_outside_tokens():
    """组件样式里不允许出现字面量色值，只能引用 var(--x)。

    例外：`:root` 开头的 token 定义块、以及 rgba() 阴影（阴影不参与主题切换）。
    """
    css = read("app.css")
    # 去掉所有 :root[...] / :root 的 token 定义块
    stripped = re.sub(r":root[^{]*\{[^}]*\}", "", css)
    offenders = [line.strip() for line in stripped.splitlines()
                 if re.search(r"#[0-9a-fA-F]{3,8}\b", line)]
    assert not offenders, "组件里写死了颜色：" + " | ".join(offenders[:5])


def test_theme_is_attribute_driven():
    css = read("app.css")
    assert '[data-theme="light"]' in css and '[data-theme="dark"]' in css
    for accent in ("blue", "teal", "amber", "pink", "violet", "navy"):
        assert f'[data-accent="{accent}"]' in css, f"缺少强调色 {accent}"


def test_app_js_switches_theme_by_attribute():
    js = read("app.js")
    assert "dataset.theme" in js and "dataset.accent" in js and "dataset.size" in js


# --------------------------------------------------------------------------- 语言不写死


def _dict_section(src: str, lang: str) -> str:
    i = src.index(f'  {lang}: {{')
    end = src.index("\n  },", i)
    return src[i:end]


def _strip_comments(src: str) -> str:
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return "\n".join(re.sub(r"//.*$", "", line) for line in src.splitlines())


def _used_keys(js: str) -> set[str]:
    """只扫剥掉注释后的代码 —— 否则注释里举的反例会被当成真实调用。"""
    return set(re.findall(r'\bt\(\s*"([a-z0-9_]+)"', _strip_comments(js)))


def _has_key(section: str, key: str) -> bool:
    """字典里多个键写在同一行（`a: 1, b: 2`），所以不能要求键在行首。

    注意别在 f-string 里写 `[\\s{{,]]` —— `{{` 会转成 `{`，末尾多出的 `]`
    变成字面量，正则永远匹配不上。这里只用行首 + 空白/逗号三种边界。
    """
    return bool(re.search(rf'(?:^|[\s,])"?{key}"?\s*:', section, re.M))


@pytest.mark.parametrize("file", ["app.js", "store.js", "access.js"])
def test_every_used_i18n_key_exists_in_both_languages(file):
    """缺键会让界面显示原始 key，且只在切到某语言时才暴露 —— 提前用测试拦住。"""
    src = read("i18n.js")
    zh, en = _dict_section(src, "zh"), _dict_section(src, "en")
    missing = [k for k in sorted(_used_keys(read(file)))
               if not _has_key(zh, k) or not _has_key(en, k)]
    assert not missing, f"i18n 缺键：{missing}"


def test_no_hardcoded_chinese_ui_strings_in_app_js():
    """界面文案必须走 t()。注释里的中文不算，所以只看渲染模板里的中文字面量。"""
    js = _strip_comments(read("app.js"))
    # 不允许跨行匹配，否则一个引号会把整段代码吞进来
    literals = re.findall(r'["\'`][^"\'`\n]*[一-鿿][^"\'`\n]*["\'`]', js)
    offenders = [x for x in literals if "${" not in x and "var(--" not in x]
    assert not offenders, "疑似硬编码文案：" + " | ".join(offenders[:5])


def test_no_fullwidth_punctuation_hardcoded_in_app_js():
    """标点也是文案的一部分 —— 全角冒号别写死在模板里。

    冲突面板刚上线时英文界面显示成 `Reason：Both sides edited...`：汉字守卫抓不到，
    因为它只找 `[一-鿿]` 的汉字，而 `：`(U+FF1A) 落在标点区。
    切到英文还带着中文标点，属于同一条"语言不写死"的规矩没做彻底。
    """
    js = _strip_comments(read("app.js"))
    hits = re.findall(r'["\'`][^"\'`\n]*[：，。（）、！？][^"\'`\n]*["\'`]', js)
    offenders = [x for x in hits if "${" not in x and "var(--" not in x]
    assert not offenders, (
        "模板里写死了全角标点（英文界面会跟着变中文排版），放进 i18n："
        + " | ".join(offenders[:5]))


def test_frontend_does_not_hardcode_api_paths():
    """前端只认功能名，URL 由 /meta/features 推导 —— 后端改路径前端不该崩。"""
    for file in ("app.js", "store.js"):
        assert "/api/v1/" not in read(file), f"{file} 里出现了硬编码 URL"
    api = re.sub(r"/\*.*?\*/", "", read("api.js"), flags=re.S)      # 去块注释
    api = "\n".join(l for l in api.splitlines() if not l.strip().startswith("//"))
    assert api.count("/api/v1/") == 1, "api.js 只允许 bootstrap 那一处固定路径"


def test_frontend_never_uses_browser_storage():
    """主题/偏好存后端 view_pref，不用 localStorage —— 换浏览器、清缓存都不丢。"""
    for file in ("app.js", "store.js", "api.js", "i18n.js"):
        src = read(file)
        assert "localStorage" not in src and "sessionStorage" not in src, file


def test_css_never_uses_an_undefined_variable():
    """`var(--x)` 用到的变量必须在 CSS 里定义过，或由 JS 以内联 style 注入。

    浏览器对未定义变量是静默回退：颜色不对、边框消失，页面照样跑，
    DOM 断言也看不出问题 —— 只能这样静态对齐一遍。
    注入集合从 app.js 里现扫（`style="--c:..."`），不手写白名单，
    否则加一个新注入变量就会让守卫误报，而误报的守卫活不过下一次改动。
    """
    css = (WEB / "app.css").read_text(encoding="utf-8")
    js = (WEB / "app.js").read_text(encoding="utf-8")
    defined = set(re.findall(r"(--[a-z0-9-]+)\s*:", css))
    injected = set(re.findall(r'style="\s*(--[a-z0-9-]+)\s*:', js))
    used = set(re.findall(r"var\((--[a-z0-9-]+)\)", css))
    missing = sorted(used - defined - injected)
    assert not missing, (
        f"app.css 用了没定义的变量（浏览器会静默回退，看不出坏了）：{missing}。"
        f"已定义的有 {len(defined)} 个，用 grep -o -- '--[a-z-]*:' 现查")


def test_css_variable_guard_is_not_blind(tmp_path):
    """守卫自己也要测：真缺变量要报，JS 注入的那种不能误报。"""
    css = tmp_path / "a.css"
    css.write_text(".x{color:var(--accent);border:1px solid var(--bd)}\n"
                   ".y{background:var(--c)}\n--accent:#fff;\n", encoding="utf-8")
    defined = set(re.findall(r"(--[a-z0-9-]+)\s*:", css.read_text(encoding="utf-8")))
    injected = {"--c"}
    used = set(re.findall(r"var\((--[a-z0-9-]+)\)", css.read_text(encoding="utf-8")))
    assert sorted(used - defined - injected) == ["--bd"]
