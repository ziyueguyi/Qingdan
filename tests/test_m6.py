"""M6：开机自启、托盘后端选择、图标产物。"""
from __future__ import annotations

import json
import struct
from pathlib import Path

import pytest

from qingdan.adapters.toast import ToastChannel, set_tray_notifier
from qingdan.core import autostart as az
from qingdan.core.alerting import config as acfg
from qingdan.core.registry import QdError, call

PACKAGING = Path(__file__).resolve().parents[1] / "packaging"


# --------------------------------------------------------------------------- 自启逻辑


def _mgr(frozen=True, exe=r"C:\Program Files\Qingdan\Qingdan.exe"):
    reg = az.FakeRegistry()
    return az.Autostart(reg, lambda: exe, lambda: frozen), reg


def test_dev_mode_refuses_to_register():
    """非 frozen 时 sys.executable 是 python.exe —— 写进 Run 键等于开机拉起解释器。"""
    m, reg = _mgr(frozen=False)
    out = m.enable()
    assert out["changed"] is False
    assert reg.values == {}, "开发环境绝不能写入自启项"
    assert out["reason_key"] == "dev_mode", "只出机器键，中文文案归前端 i18n"


def test_enable_writes_quoted_command_when_path_has_space():
    m, reg = _mgr()
    out = m.enable()
    assert out["changed"] is True
    assert reg.values[az.VALUE_NAME] == '"C:\\Program Files\\Qingdan\\Qingdan.exe"'


def test_enable_no_quotes_when_path_clean():
    m, reg = _mgr(exe=r"C:\Qingdan\Qingdan.exe")
    m.enable()
    assert reg.values[az.VALUE_NAME] == "C:\\Qingdan\\Qingdan.exe"


def test_disable_removes_value():
    m, reg = _mgr()
    m.enable()
    out = m.disable()
    assert out["changed"] is True and az.VALUE_NAME not in reg.values


def test_disable_is_idempotent():
    m, reg = _mgr()
    assert m.disable()["changed"] is False


def test_status_reports_mismatch():
    m, reg = _mgr()
    reg.values[az.VALUE_NAME] = '"D:\\old\\Qingdan.exe"'
    st = m.status()
    assert st["enabled"] is True
    assert st["command"] != st["expected"], "状态必须能暴露指向旧路径的问题"


def test_repair_fixes_stale_path():
    """exe 被移动过 → Run 键指向旧位置 → 开机静默失败。启动时自查纠正。"""
    m, reg = _mgr()
    reg.values[az.VALUE_NAME] = '"D:\\old\\Qingdan.exe"'
    out = m.repair_if_stale()
    assert out is not None
    assert out["repaired_to"] == '"C:\\Program Files\\Qingdan\\Qingdan.exe"'
    assert reg.values[az.VALUE_NAME] == out["repaired_to"]


def test_repair_noop_when_correct_or_dev():
    m, reg = _mgr()
    m.enable()
    assert m.repair_if_stale() is None

    m2, _ = _mgr(frozen=False)
    m2.registry.values[az.VALUE_NAME] = "whatever"
    assert m2.repair_if_stale() is None, "开发环境不去改注册表"


def test_autostart_features_over_api(app_ctx):
    """接口必须存在且走同一套逻辑（R4）。"""
    m, reg = _mgr()
    az.set_manager(m)
    try:
        st = call("app.autostart_get")
        assert st["enabled"] is False and st["frozen"] is True
        out = call("app.autostart_set", on=True)
        assert out["changed"] is True and out["enabled"] is True
        assert call("app.autostart_set", on=False)["enabled"] is False
    finally:
        az.set_manager(None)


# --------------------------------------------------------------------------- 通知后端选择


def _toast():
    return ToastChannel(acfg.get_config)


def test_backend_is_dialog_without_tray(app_ctx):
    set_tray_notifier(None)
    assert _toast().backend == "dialog"


def test_backend_upgrades_to_balloon_when_tray_present(app_ctx):
    """托盘气球不需要 AUMID，比 winotify 的 toast 可靠 —— 托盘一起来就自动升级。"""
    seen = []
    set_tray_notifier(lambda t, b: seen.append((t, b)))
    try:
        ch = _toast()
        assert ch.backend == "balloon"
        r = ch.send([_msg()])
        assert r.ok and r.detail["backend"] == "balloon"
        assert seen, "气球回调必须真的被调用"
    finally:
        set_tray_notifier(None)


def test_explicit_toast_backend_overrides_balloon(app_ctx):
    call("alert.config_update", config={"toast": {"backend": "toast"}})
    seen = []
    set_tray_notifier(lambda t, b: seen.append((t, b)))
    try:
        assert _toast().backend == "toast", "用户显式选了 toast 就不该被自动改掉"
    finally:
        set_tray_notifier(None)
        call("alert.config_update", config={"toast": {"backend": "dialog"}})


def test_balloon_merges_many_alerts_into_one(app_ctx):
    """30 条到期不能弹 30 个气球 —— 那等于把通知系统变成骚扰。"""
    seen = []
    set_tray_notifier(lambda t, b: seen.append((t, b)))
    try:
        r = _toast().send([_msg(title=f"任务{i}") for i in range(30)])
        assert r.ok and len(seen) == 1
        assert seen[0][0] == "30 条提醒"
    finally:
        set_tray_notifier(None)


def _msg(title="写周报"):
    from qingdan.core.alerting.base import AlertMessage
    return AlertMessage(item_id="T", kind="task", title=title, fire_at_ms=1)


# --------------------------------------------------------------------------- 图标产物


def _ico_sizes(path: Path) -> list[tuple[int, int]]:
    with open(path, "rb") as f:
        header = f.read(6)
        count = struct.unpack("<H", header[4:6])[0]
        out = []
        for _ in range(count):
            e = f.read(16)
            out.append((256 if e[0] == 0 else e[0], 256 if e[1] == 0 else e[1]))
        return out


def test_ico_contains_all_standard_sizes():
    ico = PACKAGING / "qd.ico"
    if not ico.is_file():
        pytest.skip("先跑 scripts/make_icon.py")
    sizes = _ico_sizes(ico)
    assert sizes == [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)], \
        "exe 图标必须含多尺寸，否则任务栏/托盘里会糊"
    assert ico.stat().st_size > 1000, "尺寸没真写进去的 .ico 会小得可疑"


def test_tray_png_has_real_content():
    png = PACKAGING / "icon.png"
    if not png.is_file():
        pytest.skip("先跑 scripts/make_icon.py")
    from PIL import Image
    im = Image.open(png)
    assert im.size == (1024, 1024) and im.mode == "RGBA"
    small = im.resize((16, 16))
    opaque = sum(1 for p in small.get_flattened_data() if p[3] > 128) \
        if hasattr(small, "get_flattened_data") else sum(1 for p in small.getdata() if p[3] > 128)
    assert opaque > 100, f"16px 下不透明像素过少（{opaque}），图标在托盘里会看不清"


def test_spec_does_not_exclude_plugins_pystray_needs():
    """静态守卫：spec 不许排除 IcoImagePlugin / BmpImagePlugin。

    pystray 在 Windows 上会把托盘图标在内存里转成 ICO 再交给 Shell_NotifyIcon。
    实测过一次为了压体积排掉 IcoImagePlugin，托盘线程直接 KeyError: 'ICO' 崩掉，
    而且崩在 pystray 自己的线程里，主程序照常启动 —— 属于"看起来没事、通知全哑"那种。
    注释挡不住下一次压体积，所以做成断言。
    """
    spec = (PACKAGING / "qingdan.spec").read_text(encoding="utf-8")
    block = spec[spec.index("EXCLUDES = ["):spec.index("]", spec.index("EXCLUDES = ["))]
    for needed in ("PIL.IcoImagePlugin", "PIL.BmpImagePlugin"):
        assert f'"{needed}"' not in block, f"{needed} 被排除了，托盘会在运行时崩"


def test_pillow_can_encode_ico_at_runtime():
    """直接验证 Pillow 能内存编码 ICO —— pystray 依赖的就是这个能力。"""
    from io import BytesIO

    from PIL import Image

    buf = BytesIO()
    Image.new("RGBA", (32, 32), (47, 111, 237, 255)).save(buf, format="ICO")
    assert len(buf.getvalue()) > 0


def test_icon_generator_is_idempotent():
    """重跑不能改变产物 —— 否则每次构建都会让 exe 字节不同，破坏可复现构建。"""
    import hashlib
    import subprocess
    import sys

    ico = PACKAGING / "qd.ico"
    if not ico.is_file():
        pytest.skip("先跑 scripts/make_icon.py")
    before = hashlib.sha256(ico.read_bytes()).hexdigest()
    root = PACKAGING.parent
    subprocess.run([sys.executable, str(root / "scripts" / "make_icon.py")],
                   cwd=str(root), check=True, capture_output=True)
    assert hashlib.sha256(ico.read_bytes()).hexdigest() == before


# --------------------------------------------------------------------------- 分层


def test_core_does_not_import_shell_or_tray():
    """托盘/窗口是 shell 的事，core 反向依赖会让 core 无法在无 GUI 环境测试。

    只检查**模块级**导入：函数内的延迟导入（如 autostart 里的 winreg）不影响
    "core 能否被导入"这件事，禁掉它只会逼人在测试里加特例，反而稀释了规则。
    """
    import ast

    core = Path(__file__).resolve().parents[1] / "src" / "qingdan" / "core"
    offenders = {}
    for py in list(core.glob("*.py")) + list(core.glob("*/*.py")):
        tree = ast.parse(py.read_text(encoding="utf-8"))
        names: set[str] = set()
        for node in tree.body:                      # 只看顶层
            if isinstance(node, ast.Import):
                names |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                names.add(node.module.split(".")[0])
        bad = names & {"shell", "pystray", "webview", "winreg", "PIL"}
        if bad:
            offenders[py.name] = sorted(bad)
    assert not offenders, f"core 模块级依赖了 shell/平台模块：{offenders}"


def test_autostart_winreg_import_is_lazy():
    """winreg 必须在函数内部导入，否则 core 在非 Windows 上连导入都会失败。"""
    import ast

    src = Path(__file__).resolve().parents[1] / "src" / "qingdan" / "core" / "autostart.py"
    tree = ast.parse(src.read_text(encoding="utf-8"))
    top = {a.name.split(".")[0] for n in tree.body if isinstance(n, ast.Import) for a in n.names}
    assert "winreg" not in top
    deep = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    assert "winreg" in deep, "winreg 应该被延迟导入，而不是根本没有"


def test_channel_registration_does_not_need_context():
    """渠道在 bootstrap 导入时就注册，那时上下文还不存在。

    `is_configured()` 一旦去读 alert 配置就会 RuntimeError（实测踩过），
    所以注册路径必须完全不依赖 ctx.store。
    """
    import subprocess
    import sys

    root = PACKAGING.parent
    code = (
        "import sys; sys.path.insert(0, 'src');"
        "import qingdan.bootstrap;"
        "from qingdan.core.alerting.base import CHANNELS;"
        "print('registered', sorted(CHANNELS))"
    )
    r = subprocess.run([sys.executable, "-c", code], cwd=str(root),
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, f"无上下文导入 bootstrap 失败：{r.stderr[-400:]}"
    assert "registered" in r.stdout


# --------------------------------------------------------------------------- 体积留档


def _load_check_size():
    """脚本不在包里，用路径加载 —— 工具也要有测试：它的承诺是"防体积悄悄回退"，
    没测过的工具等于没有。"""
    import importlib.util

    path = PACKAGING.parent / "scripts" / "check_size.py"
    spec = importlib.util.spec_from_file_location("check_size", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_size_gate_writes_a_comparable_artifact(tmp_path):
    cs = _load_check_size()
    build = tmp_path / "dist" / "Qingdan"
    (build / "_internal").mkdir(parents=True)
    (build / "Qingdan.exe").write_bytes(b"x" * 4096)
    (build / "_internal" / "base_library.zip").write_bytes(b"y" * 1024)

    assert cs.main(str(build)) == 0, "小产物不该触发门禁"
    line = (tmp_path / "dist" / "SIZE").read_text(encoding="utf-8")
    assert "MB" in line and "limit 42" in line, line
    record = json.loads((tmp_path / "dist" / "SIZE.json").read_text(encoding="utf-8"))
    assert record["bytes"] == 4096 + 1024, "留档必须记真实字节数，能跨版本对比"
    assert [f["path"].replace("\\", "/") for f in record["top_files"]] == \
        ["Qingdan.exe", "_internal/base_library.zip"], "最大的文件要逐个记，才知道胖在哪"


def test_size_gate_fails_over_the_limit(tmp_path):
    cs = _load_check_size()
    build = tmp_path / "dist" / "Qingdan"
    build.mkdir(parents=True)
    with (build / "big.dll").open("wb") as fh:
        fh.truncate(int((cs.LIMIT_MB + 1) * 1048576))
    assert cs.main(str(build)) == 1, "超线必须返回非零，否则 CI 拦不住"
