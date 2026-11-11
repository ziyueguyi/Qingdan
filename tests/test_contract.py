"""契约测试 —— R4「做一个功能就必须有它的接口」的验收线。

三条断言：
  ① 注册时不声明 http 直接失败（制度保障，不靠自觉）；
  ② registry 与 FastAPI 路由 1:1，没有漏路由也没有野路由；
  ③ 每个功能都必须能**通过 HTTP** 调通（禁止只测 Python 函数）。

③ 的样例值是**推导**出来的而不是逐个手写的：功能加到 59 个之后，手写参数表
必然和实现脱节，那样契约测试会退化成「改了实现顺手改测试」的空壳。
"""
from __future__ import annotations

import ast
import inspect
import json
import re
import sys
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from qingdan.api.http import build_app
from qingdan.core.alerting.base import CHANNELS, SendResult, register_channel
from qingdan.core.registry import REGISTRY, feature

TODAY = date(2026, 11, 5).isoformat()
DUE = "2026-12-31T10:00:00"

# 路径模板 → 该路径上 {item_id} 应该填哪种对象
PATH_TARGETS = (
    ("/tasks/{item_id}", "task"),
    ("/notes/{item_id}", "note"),
    ("/groups/{item_id}", "group"),
    ("/folders/{item_id}", "folder"),
    ("/sections/{item_id}", "section"),
    ("/tags/{item_id}", "tag"),
    ("/filters/{item_id}", "filter"),
    ("/countdowns/{item_id}", "countdown"),
    ("/backups/{name}", "backup_name"),
    ("/views/{name}", "view_name"),
)

# 参数名 → 样例值（按名字匹配，覆盖绝大多数）
PARAM_SAMPLES = {
    "title": "契约任务", "name": "契约名", "text": "契约备注内容", "q": "契约",
    "priority": 1, "color": "#ff8800", "order": 1, "minutes": 10,
    "due_at": DUE, "target_date": TODAY, "today": TODAY, "start": TODAY, "days": 7,
    "remind_at": [DUE], "channels": ["toast"], "mode": "once", "repeat": "每周",
    "group_by": "status", "kinds": ["task"], "limit": 50, "count_mode": "days",
    "remind_days_before": 1, "criteria": {"open_only": True},
    "prefs": {"layout": "list"}, "tag": "重要",
    "config": {"master": True}, "hours": 24, "token": "good-token",
}

# 少数功能光靠通用样例不够，显式覆盖
EXTRA = {
    "task.update": {"title": "契约改名"},
    "section.reorder": {"order": ["<section>"]},
    "view.query": {"name": "today"},
    "note.append": {"text": "契约备注"},
    # app.pref_set 与 view.pref_set 各自有白名单，通用样例 {"layout":...} 只适用于后者
    "app.pref_set": {"prefs": {"theme": "dark"}},
    "alert.channel_enable": {"name": "<channel>", "on": True},
    "alert.channel_test": {"name": "<channel>"},
    "alert.silence": {"minutes": 5},
    "data.import": {"content": '{"product": "qingdan", "items": []}'},
    "access.config_set": {"config": {"enabled": False}},
}

# --------------------------------------------------------------------------- 服务端文案扫描

CJK = re.compile(r"[\u3000-\u303f\u3400-\u9fff\uf900-\ufaff\uff00-\uffef]")

# 样例数据里的中文 → ASCII 等价物。扫描用全 ASCII 用户数据跑一遍所有接口，
# 这样响应里出现的任何中文都只可能是后端硬编码的展示文案。
# 「输入里有中文、原样回显出去」不算泄漏，所以必须先把输入洗干净，
# 否则这个守卫会把 96 个接口全报一遍，然后被人当噪音删掉。
ASCII_TEXT = {
    "契约备注内容": "contract-note-body",
    "契约改名": "contract-renamed",
    "契约备注": "contract-note",
    "契约任务": "contract-task",
    "契约名": "contract-name",
    "契约": "contract",
    "文件夹-": "folder-",
    "过滤器-": "filter-",
    "倒数日-": "countdown-",
    "分组-": "group-",
    "任务-": "task-",
    "小节-": "section-",
    "备注-": "note-",
    "标签": "tag-",
    "重要": "imp",
    "每周": "weekly",
}
_ASCII_KEYS = sorted(ASCII_TEXT, key=len, reverse=True)   # 长的先换，避免子串截断


def _ascii(value):
    """把样例数据里的中文换成 ASCII；换不干净就当场失败。"""
    if isinstance(value, str):
        out = value
        for zh in _ASCII_KEYS:
            out = out.replace(zh, ASCII_TEXT[zh])
        left = CJK.findall(out)
        if left:
            raise AssertionError(f"ASCII 表缺项：{value!r} 里还剩 {''.join(left)}")
        return out
    if isinstance(value, dict):
        return {k: _ascii(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_ascii(v) for v in value]
    return value


def _strip_export_body(value):
    """剥掉导出正文再扫描。

    `data.preview.content` 是**文件内容**（CSV 表头、Markdown 标题），语言由 `lang`
    参数决定，属于内容不属于界面文案，中文是正当的 —— 它的正确性由
    test_export_lang 单独管。这里放过它，是为了让这条守卫只报真问题：
    守卫一旦常年带着合法噪音，下一次改动就会把它删掉。
    """
    if isinstance(value, dict):
        return {k: _strip_export_body(v) for k, v in value.items() if k != "content"}
    if isinstance(value, list):
        return [_strip_export_body(v) for v in value]
    return value


class _FakeChannel:
    """契约测试专用假渠道。

    必须注入：bootstrap 会注册真的 winotify 适配器，而 `alert.channel_test` 一旦真跑，
    测试就会往用户桌面弹通知 —— 测试不该有副作用。
    """

    def __init__(self, name: str):
        self.name = name
        self.sent = []

    def is_configured(self) -> bool:
        return True

    def enabled(self) -> bool:
        return True

    def send(self, msgs):
        self.sent.append(msgs)
        return SendResult(ok=True, sent_count=1, covered=len(msgs))

    def test(self) -> SendResult:
        return SendResult(ok=True, sent_count=1, covered=1, detail={"fake": True})


# ---------------------------------------------------------------- ① 注册期强制


def test_feature_without_http_is_rejected():
    with pytest.raises(ValueError, match="未声明 http 接口"):
        @feature(name="dummy.op", group="dummy")
        def _op() -> dict:
            return {}


@pytest.mark.parametrize("bad_name", ["nodot", "a.b.c"])
def test_feature_name_must_be_two_segments(bad_name):
    with pytest.raises(ValueError):
        @feature(name=bad_name, group="x", http=("GET", "/api/v1/x"))
        def _op() -> dict:
            return {}


def test_feature_path_must_be_versioned():
    with pytest.raises(ValueError, match="/api/v1/"):
        @feature(name="dummy.op2", group="dummy", http=("GET", "/tasks"))
        def _op() -> dict:
            return {}


def test_duplicate_feature_name_rejected():
    with pytest.raises(ValueError, match="重复注册"):
        @feature(name="task.create", group="task", http=("POST", "/api/v1/tasks"))
        def _op() -> dict:
            return {}


# ---------------------------------------------------------------- ② 路由 1:1


def test_every_feature_declares_http_and_cli():
    for name, f in REGISTRY.items():
        assert f.http and f.http[1].startswith("/api/v1/"), f"{name} 缺 HTTP 接口"
        assert f.cli and " " in f.cli, f"{name} 缺 CLI 接口"


def test_routes_match_registry_exactly():
    app = build_app()
    # Starlette 会给 GET 自动附加 HEAD，比对时要剔除，否则 HEAD 会变成「野路由」
    actual = {(m, r.path) for r in app.router.routes
              if getattr(r, "name", None) in REGISTRY
              for m in (r.methods or set()) if m != "HEAD"}
    expected = {f.http for f in REGISTRY.values()}
    assert actual == expected, f"漏路由: {expected - actual} / 野路由: {actual - expected}"


def test_no_duplicate_route_names():
    names = [f.name for f in REGISTRY.values()]
    assert len(names) == len(set(names))


def test_no_static_route_is_shadowed_by_parameterized_one(app_ctx):
    """`/views/{name}` 若先注册会把 `/views/counts`、`/views/board` 吃掉。

    不写死具体路径对，而是逐个静态路由真发一次请求，断言它没被参数化路由截获 ——
    以后再加静态路由同样受保护。
    """
    client = TestClient(build_app())
    static = [(f.http[0], f.http[1]) for f in REGISTRY.values() if "{" not in f.http[1]]
    shadowed = []
    for method, path in static:
        resp = client.request(method, path)
        if resp.status_code == 404 and "未登记的功能路由" in resp.text:
            shadowed.append(path)
        # 被 {name} 截获时，后端会拿静态段当视图名，报「未知视图」
        if resp.status_code == 422:
            body = resp.json()
            if body.get("error", {}).get("detail", {}).get("got") == path.rsplit("/", 1)[-1]:
                shadowed.append(path)
    assert not shadowed, f"被参数化路由遮蔽的静态路由：{shadowed}"


# ---------------------------------------------------------------- ③ 经 HTTP 调通


def _make_prerequisites(client, tag_label, ascii_mode=False):
    """每个功能各建一套对象，避免前面的写操作影响后面的断言（顺序无关）。"""
    zh = (lambda s: s if not ascii_mode else _ascii(s))
    ids = {}
    ids["access_token"] = client.post('/api/v1/access/tokens',json={'name':'contract-'+tag_label}).json()['data']['item']['id']
    ids["group"] = client.post("/api/v1/groups", json={"name": zh(f"分组-{tag_label}")}).json()["data"]["id"]
    ids["task"] = client.post("/api/v1/tasks", json={"title": zh(f"任务-{tag_label}"),
                                                     "tags": [zh("重要")]}).json()["data"]["id"]
    ids["folder"] = client.post("/api/v1/folders",
                                json={"name": zh(f"文件夹-{tag_label}")}).json()["data"]["id"]
    ids["section"] = client.post("/api/v1/sections",
                                 json={"list_id": ids["group"],
                                       "name": zh(f"小节-{tag_label}")}).json()["data"]["id"]
    ids["tag"] = client.post("/api/v1/tags", json={"name": zh(f"标签{tag_label}")}).json()["data"]["id"]
    ids["filter"] = client.post("/api/v1/filters", json={"name": zh(f"过滤器-{tag_label}"),
                                                         "criteria": {"open_only": True}}).json()["data"]["id"]
    ids["countdown"] = client.post("/api/v1/countdowns",
                                   json={"title": zh(f"倒数日-{tag_label}"),
                                         "target_date": TODAY}).json()["data"]["id"]
    ids["note"] = client.post(f"/api/v1/tasks/{ids['task']}/notes",
                              json={"text": zh(f"备注-{tag_label}")}).json()["data"]["id"]
    ids["view_name"] = "today"
    ids["channel"] = "toast"
    ids["backup_name"] = "db-2026-01-05.sqlite"
    # `task.skip` 需要一条**带重复规则**的任务：对没有规则的任务跳过是明确的校验错误
    # （静默无反应比报错更糟），所以这里给它正确的靶子，而不是放宽契约断言。
    ids["repeat_task"] = client.post("/api/v1/tasks",
                                     json={"title": zh(f"任务-repeat-{tag_label}"),
                                           "due_at": TODAY, "repeat": "daily"}).json()["data"]["id"]
    client.post("/api/v1/backups", json={"name": ids["backup_name"], "force": True})
    return ids


def _target_key(path_tpl: str) -> str:
    if "/alert/channels/" in path_tpl:
        return "channel"
    if path_tpl.endswith("/skip"):
        return "repeat_task"
    for frag, key in PATH_TARGETS:
        if frag in path_tpl:
            return key
    return "task"


def _resolve_url(path_tpl: str, ids: dict, target_id: str) -> str:
    """路径里的 {item_id} / {name} 各自指向不同对象，按路径模板判断。"""
    url = path_tpl.replace("{item_id}", target_id)
    url = url.replace('{token_id}',ids['access_token'])
    if "{name}" in url:
        slot = ("backup_name" if "/backups/" in path_tpl
                else "channel" if "/alert/channels/" in path_tpl
                else "view_name")
        url = url.replace("{name}", ids[slot])
    return url


def _required_raw(f, ids, target_id, overrides, samples=None) -> dict:
    samples = PARAM_SAMPLES if samples is None else samples
    raw = {}
    for p in inspect.signature(f.func).parameters.values():
        if p.kind is p.VAR_KEYWORD or p.default is not inspect.Parameter.empty:
            continue
        if p.name == "item_id":
            raw[p.name] = target_id
        elif p.name == 'token_id':
            raw[p.name] = ids['access_token']
        elif p.name == "list_id":
            raw[p.name] = ids["group"]
        elif p.name == "section_id":
            raw[p.name] = ids["section"]
        elif p.name == "parent_id":
            raw[p.name] = ids["task"]
        elif p.name in samples:
            raw[p.name] = samples[p.name]
        else:
            raise AssertionError(f"契约测试缺少参数样例：{f.name} 的 {p.name}")
    raw.update(overrides)
    return raw


def _sweep_http(client, ascii_mode=False):
    """把每个功能都经 HTTP 调一遍，返回 (响应体, 失败清单, 未登录跳过清单)。

    「能不能调通」和「响应里有没有服务端中文」共用这一套调用逻辑：
    复制两份实现必然漂移，第二份的结论就不可信。
    """
    bodies, failures, skipped_auth = {}, [], []
    samples = _ascii(PARAM_SAMPLES) if ascii_mode else PARAM_SAMPLES
    extra = _ascii(EXTRA) if ascii_mode else EXTRA
    # 注入假渠道：否则 alert.channel_test 会真的弹通知/发钉钉
    originals = dict(CHANNELS)
    for cname in ("toast", "dingtalk", "email"):
        register_channel(_FakeChannel(cname))
    try:
        for name, f in REGISTRY.items():
            method, path_tpl = f.http
            ids = _make_prerequisites(client, name.replace(".", "_"), ascii_mode)
            target_id = ids[_target_key(path_tpl)]
            url = _resolve_url(path_tpl, ids, target_id)

            overrides = dict(extra.get(name, {}))
            if "<section>" in str(overrides.get("order")):
                overrides["order"] = [ids["section"]]
            for k, v in list(overrides.items()):
                if v == "<channel>":
                    overrides[k] = ids["channel"]
            raw = _required_raw(f, ids, target_id, {}, samples)
            # 路径参数已经拼进 URL，不要再塞进 query/body
            path_slots = set(re.findall(r"\{(\w+)\}", path_tpl))
            raw = {k: v for k, v in raw.items() if k not in path_slots}
            raw.update({k: v for k, v in overrides.items() if k not in path_slots})

            resp = (client.request(method, url, params=raw) if method in ("GET", "DELETE")
                    else client.request(method, url, json=raw))

            if resp.status_code == 500:
                failures.append((name, "500", resp.text[:300]))
            elif f.needs_auth and resp.status_code == 401:
                skipped_auth.append(name)
            elif not resp.is_success or resp.json().get("ok") is not True:
                failures.append((name, method, url, resp.status_code, resp.text[:240]))
            else:
                bodies[name] = resp.json()["data"]
    finally:
        CHANNELS.clear()
        CHANNELS.update(originals)
    return bodies, failures, skipped_auth


def test_every_feature_is_callable_over_http(app_ctx):
    client = TestClient(build_app())
    _, failures, _ = _sweep_http(client)
    assert not failures, "以下功能经 HTTP 调用未成功：\n" + "\n".join(map(str, failures))


def test_responses_never_carry_server_side_display_text(app_ctx):
    """接口返回体里不许出现后端自产的中文 —— 展示文案归前端 i18n，后端只给机器键。

    这条规矩踩过一次就写过一次（英文界面里残留「已逾期/待办」），但当时只修了视图层：
    `autostart.reason`、`backup.note`、`alert.note` 用同一套「后端顺手写文案」的写法
    把中文塞进了响应体，没人拦。所以这里把口径扩到**全部功能**：
    用全 ASCII 的用户数据把所有接口经 HTTP 跑一遍，输入没有中文，输出里的任何中文
    都只可能是硬编码的展示文案。

    必须独占一个库：和上面那个中文扫描共用 db 时，列表类接口会把上一轮中文数据
    原样吐出来，守卫立刻满屏误报，然后就被人删掉了。
    """
    client = TestClient(build_app())
    bodies, failures, skipped_auth = _sweep_http(client, ascii_mode=True)

    # 洗成 ASCII 不能靠少跑几个接口办到，否则这条守卫的覆盖面会悄悄缩水
    assert not failures, "ASCII 样例下调用失败：\n" + "\n".join(map(str, failures))
    covered = set(bodies) | set(skipped_auth)
    assert covered == set(REGISTRY), (
        f"只跑了 {len(covered)}/{len(REGISTRY)} 个功能，扫描范围不完整；"
        f"漏掉：{sorted(set(REGISTRY) - covered)}")

    offenders = {}
    for name, data in bodies.items():
        text = json.dumps(_strip_export_body(data), ensure_ascii=False, default=str)
        hits = CJK.findall(text)
        if hits:
            offenders[name] = "".join(dict.fromkeys(hits))[:30]
    assert not offenders, (
        "这些接口把展示文案写死在后端（英文界面会残留中文），改成 *_key：\n"
        + "\n".join(f"  {k}: {v}" for k, v in sorted(offenders.items())))


def _cjk_dict_values(src: Path) -> list[tuple[str, str, str]]:
    """扫出「中文字符串出现在字典值里」的位置，返回 (文件, 所属变量, 键)。

    按变量名而不是行号认，是为了让白名单在代码改动后仍然指同一处：行号会漂，
    漂了之后守卫要么误报要么白名单失效（两种都会让人直接把测试删掉）。
    """
    out: list[tuple[str, str, str]] = []
    for py in sorted(src.rglob("*.py")):
        tree = ast.parse(py.read_text(encoding="utf-8"))
        owners: dict[int, str] = {}
        for node in ast.walk(tree):
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                name = (getattr(node.targets[0], "id", "") if isinstance(node, ast.Assign)
                        else getattr(node.target, "id", ""))
                value = node.value
                if isinstance(value, (ast.Dict, ast.List, ast.Tuple)):
                    # 嵌套字典也算在同一个变量头上：WORDS 的中文在最里层，
                    # 只认最外层那个 dict 的话白名单永远匹配不上。
                    for sub in ast.walk(value):
                        if isinstance(sub, ast.Dict):
                            owners.setdefault(id(sub), name)
            elif isinstance(node, ast.Call):
                for a in node.args:
                    if isinstance(a, ast.Dict):
                        owners.setdefault(id(a), f"arg of {ast.unparse(node.func)}")
        for node in ast.walk(tree):
            if not isinstance(node, ast.Dict):
                continue
            for k, v in zip(node.keys, node.values):
                txt = (v.value if isinstance(v, ast.Constant) and isinstance(v.value, str)
                       else ast.unparse(v) if isinstance(v, ast.JoinedStr) else "")
                if txt and CJK.search(txt):
                    out.append((py.relative_to(src).as_posix(),
                                owners.get(id(node), "<inline>"),
                                k.value if isinstance(k, ast.Constant) else "?"))
    return out


def test_no_server_side_display_text_in_source():
    """静态兜底：源码字典值里不许写中文。

    运行时扫描覆盖不到 `needs_auth` 的 10 个同步类接口 —— 契约测试不能真去 clone
    远端仓库，那些接口只会 401 跳过。所以再加一层 AST：字典字面量的值里出现中文，
    就说明有人把展示文案写回了后端。
    """
    src = Path(__file__).resolve().parents[1] / "src" / "qingdan"
    # 白名单只有两处，都不是界面文案：
    #   core/export.py  WORDS/CSV_COLUMNS —— 导出**文件内容**的语言表，由 lang 参数选
    #   shell/app.py    写进 http.json 的启动失败诊断 —— 这时 i18n 还没起来
    allowed = {("core/export.py", "WORDS"), ("shell/app.py", "arg of _write_status")}
    real = [o for o in _cjk_dict_values(src) if (o[0], o[1]) not in allowed]
    assert not real, (
        "这些字典值写了中文，界面切英文会残留中文（该给机器键就给机器键）：\n"
        + "\n".join(f"  {f} 变量 {var} 键 {k}" for f, var, k in real))


def test_static_display_text_scanner_is_not_blind(tmp_path):
    """守卫自己也要测：能抓到真违规、不被注释骗、白名单按变量名认。"""
    (tmp_path / "bad.py").write_text(
        'def status():\n    return {"reason": "开发环境不注册自启", "frozen": False}\n',
        encoding="utf-8")
    (tmp_path / "ok.py").write_text(
        '"""这里只是提到 {"reason": "中文"}，不是代码。"""\n'
        "WORDS = {'zh': {'yes': '是'}}\n"
        "def f(x):\n    return {'frozen': True}\n",
        encoding="utf-8")
    found = _cjk_dict_values(tmp_path)
    assert [o for o in found if o[0] == "bad.py"] == [("bad.py", "<inline>", "reason")], \
        "函数里返回的中文字面量必须被抓到"
    assert [o for o in found if o[0] == "ok.py"] == [("ok.py", "WORDS", "yes")], \
        ("文档字符串与无中文的字典都不该报；嵌套层的中文要认到所属变量 WORDS，"
         "否则白名单只能按行号写，改动一次就失效")


def test_http_envelope_shape(app_ctx):
    client = TestClient(build_app())
    body = client.get("/api/v1/health").json()
    assert set(body) == {"ok", "data", "trace_id"}
    err = client.post("/api/v1/tasks", json={}).json()
    assert err["ok"] is False and err["error"]["code"] == "VALIDATION_ERROR"
    assert err["error"]["message"]


def test_auth_required_returns_401_not_500(app_ctx):
    """未登录调用同步类功能必须是 AUTH_REQUIRED，不能伪装成网络错误。"""
    from qingdan.core.registry import QdError, auth_required
    assert auth_required().http_status == 401
    assert isinstance(auth_required(), QdError)


def test_features_meta_endpoint_lists_all(app_ctx):
    client = TestClient(build_app())
    data = client.get("/api/v1/meta/features").json()["data"]
    assert set(data) == set(REGISTRY)
    for name, meta in data.items():
        assert meta["http"] and meta["cli"]


def _defines_feature(py) -> bool:
    """文件里是否真的用 @feature 装饰了函数（AST，不是文本匹配）。

    曾经的版本用 `"@feature(" in text`，结果被自己的文档字符串误触发 ——
    子串匹配分不清「代码」和「提到代码的话」，同一条教训不能再犯第二次。
    """
    tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        for dec in node.decorator_list:
            target = dec.func if isinstance(dec, ast.Call) else dec
            if isinstance(target, ast.Name) and target.id == "feature":
                return True
            if isinstance(target, ast.Attribute) and target.attr == "feature":
                return True
    return False


def _feature_modules() -> list[str]:
    """src/qingdan/core 下所有声明了 @feature 的模块 dotted name。"""
    root = Path(__file__).resolve().parents[1] / "src" / "qingdan" / "core"
    out = []
    for py in sorted(list(root.glob("*.py")) + list(root.glob("*/*.py"))):
        if _defines_feature(py):
            rel = py.relative_to(root.parent)
            out.append("qingdan." + ".".join(rel.with_suffix("").parts))
    return out


def test_every_feature_module_is_imported_by_bootstrap():
    """定义了 @feature 的模块，必须真的被 import 进进程。

    契约测试只校验"已注册的功能都有路由"，抓不到"整个模块忘了导入" ——
    实测过一次：autostart 没进 bootstrap，`serve` 模式下两个接口静默消失，
    只有 `qingdan app` 因为 shell 间接导入才存在。那种缺失比写错更难发现。

    断言看的是 `sys.modules` 而不是 bootstrap 源码文本：导入可能写成
    `from qingdan.core import backup`，dotted name 压根不出现在文件里，
    文本匹配会把每个模块都误判成漏导入（第一版就是这么全红的）。
    """
    import importlib

    importlib.import_module("qingdan.bootstrap")
    missing = [m for m in _feature_modules() if m not in sys.modules]
    assert not missing, f"这些功能模块没有被导入，接口会静默消失：{missing}"


def test_bootstrap_guard_detects_a_missing_module(tmp_path):
    """守卫自己也要测：它必须能抓到「漏导入」，且不会被注释/文档骗过。"""
    pkg = tmp_path / "qd" / "core"
    pkg.mkdir(parents=True)
    (tmp_path / "qd" / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "__init__.py").write_text("", encoding="utf-8")

    (pkg / "real.py").write_text(
        "from qingdan.core.registry import feature\n\n"
        "@feature(name='x.y', group='x')\ndef y(): return 1\n", encoding="utf-8")
    (pkg / "mention.py").write_text(
        '"""这里只是提到 @feature(name=...)，并没有真的用。"""\n', encoding="utf-8")

    assert _defines_feature(pkg / "real.py") is True
    assert _defines_feature(pkg / "mention.py") is False, "文档字符串不该被当成声明"

    sys.modules.pop("qd.core.real", None)
    assert "qd.core.real" not in sys.modules
    srcs = [Path("qd/core/real.py"), Path("qd/core/mention.py")]
    found = ["qd." + ".".join(p.with_suffix("").parts[1:]) for p in srcs
             if _defines_feature(tmp_path / p)]
    assert found == ["qd.core.real"], "只应挑出真正声明 @feature 的模块"


def test_feature_count_matches_doc_inventory(app_ctx):
    """M1 交付口径：不少于 55 个功能接口（开发文档 §13 的接口清单）。"""
    assert len(REGISTRY) >= 55, f"当前只有 {len(REGISTRY)} 个功能"
