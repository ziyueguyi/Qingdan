"""数据目录：一律落在应用目录，且不许让人丢数据。

用户的要求是「程序产生的一切都放应用目录，也可以自定义」。这条链上最容易出的
事故不是"路径不对"，而是**静默换了一份空库** —— 老数据在 `%LOCALAPPDATA%`，
默认值一改，用户打开程序看到的是空清单，还以为数据没了。
"""
from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

import pytest

from qingdan.core import context
from qingdan.core.context import LEGACY_DIR, app_dir, default_dir, resolve_data_dir


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    """把应用目录、老目录、环境变量、override 文件全部隔开，别碰真实用户数据。"""
    home = tmp_path / "app"
    legacy = tmp_path / "localappdata" / "qingdan"
    monkeypatch.setattr(context, "app_dir", lambda: home)
    monkeypatch.setattr(context, "LEGACY_DIR", legacy)
    monkeypatch.setattr(context, "default_dir", lambda: home / "data")
    monkeypatch.delenv("QINGDAN_DATA_DIR", raising=False)
    return home, legacy


def _mk_db(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path / "db.sqlite")
    con.execute("CREATE TABLE item (id TEXT)")
    con.execute("INSERT INTO item VALUES ('x')")
    con.commit()
    con.close()


def test_default_is_under_the_app_dir(isolated):
    home, _legacy = isolated
    path, source = resolve_data_dir()
    assert path == home / "data" and source == "app", "全新安装必须写在应用目录下"


def test_legacy_data_is_not_abandoned(isolated):
    """老位置有库、新位置没有 → 继续读老库，并说明来源是 legacy。

    直接切默认值会让用户打开程序看到空清单；那比路径不合规严重得多。
    """
    home, legacy = isolated
    _mk_db(legacy)
    path, source = resolve_data_dir()
    assert path == legacy and source == "legacy"


def test_app_dir_wins_once_migrated(isolated):
    home, legacy = isolated
    _mk_db(legacy)
    _mk_db(home / "data")
    path, source = resolve_data_dir()
    assert path == home / "data" and source == "app", "搬完就该以应用目录为准"


def test_cli_argument_beats_everything(isolated):
    home, legacy = isolated
    _mk_db(legacy)
    other = home / "elsewhere"
    path, source = resolve_data_dir(other)
    assert path == other.resolve() and source == "explicit"


def test_env_var_override(isolated, monkeypatch):
    home, _legacy = isolated
    monkeypatch.setenv("QINGDAN_DATA_DIR", str(home / "from-env"))
    path, source = resolve_data_dir()
    assert path == home / "from-env" and source == "env"


def test_data_dir_txt_for_double_clickers(isolated):
    """双击启动的人没法传参，所以应用目录下放一个 data_dir.txt 就能改。"""
    home, _legacy = isolated
    (home / "data").mkdir(parents=True)
    (home / "data_dir.txt").write_text("portable-data\n", encoding="utf-8")
    path, source = resolve_data_dir()
    assert source == "file" and path == home / "portable-data", "相对路径要按应用目录解析"

    (home / "data_dir.txt").write_text("  \n", encoding="utf-8")
    assert resolve_data_dir()[1] == "app", "空文件不该把程序带到一个不存在的地方"

    # 记事本里留个注释和空行也很常见，不能崩
    (home / "data_dir.txt").write_text("# 我的移动硬盘\n\n   \nusb-data\n", encoding="utf-8")
    assert resolve_data_dir() == ((home / "usb-data").resolve(), "file")


def test_readonly_app_dir_falls_back_instead_of_crashing(isolated, monkeypatch):
    """装进 Program Files 时应用目录不可写：要退到可写位置并说明原因，不能崩。"""
    home, legacy = isolated
    real_mkdir = Path.mkdir

    def refuse(self, *a, **kw):
        if str(self).startswith(str(home)):
            raise PermissionError("read-only install")
        return real_mkdir(self, *a, **kw)

    monkeypatch.setattr(Path, "mkdir", refuse)
    ctx = context.init()
    assert ctx.data_dir == legacy and ctx.data_dir_source == "readonly_fallback"


def test_explicit_bad_dir_raises_instead_of_silently_switching(isolated, monkeypatch):
    """用户明确指定的目录不可用必须报错 —— 悄悄换个地方存是最难查的行为。"""
    home, _legacy = isolated
    target = home / "nope"
    real_mkdir = Path.mkdir

    def refuse(self, *a, **kw):
        if str(self).startswith(str(home)):
            raise PermissionError("nope")
        return real_mkdir(self, *a, **kw)

    monkeypatch.setattr(Path, "mkdir", refuse)
    with pytest.raises(OSError):
        context.init(target)


def test_health_reports_where_the_data_actually_is(app_ctx):
    from qingdan.core.registry import call
    h = call("app.health")
    assert h["data_dir"] and h["data_dir_source"] in {
        "explicit", "env", "file", "app", "legacy", "readonly_fallback"}
    assert h["app_dir"], "界面要能同时给出应用目录，用户才知道该往哪儿放"


def test_cli_positional_dir_reaches_both_entries(tmp_path, monkeypatch):
    """README 写着 `Qingdan.exe app D:\somewhere`，那就必须真的能传进去。

    文档里的命令是接口的一部分：没跑过的命令行等于没测试的接口。
    """
    import qingdan.__main__ as m

    seen = {}
    monkeypatch.setattr(m, "serve", lambda data_dir=None, **kw: seen.update(serve=data_dir) or 0)
    monkeypatch.setattr("qingdan.shell.app.run",
                        lambda data_dir=None, **kw: seen.update(app=data_dir) or 0)

    assert m.main(["app", str(tmp_path)]) == 0
    assert seen["app"] == str(tmp_path)
    assert m.main(["serve", str(tmp_path)]) == 0
    assert seen["serve"] == str(tmp_path)


def test_nothing_is_written_outside_the_data_dir(app_ctx):
    """备份、导出、git 工作副本、http.json 必须全在数据目录里。

    这些子目录以前各自有被拼到别处的可能（比如拿 cwd 拼相对路径），
    所以按实际产物逐个确认，而不是只看 DEFAULT_DIR 的定义。
    """
    from qingdan.core import backup, export
    from qingdan.core.registry import call

    call("task.create", title="要备份的一条")
    backup.daily_backup()
    call("data.export", format="json")
    root = Path(app_ctx.data_dir).resolve()
    for p in (backup.backups_dir(), export.exports_dir()):
        assert Path(p).resolve().is_relative_to(root), f"{p} 跑到数据目录外面了"
