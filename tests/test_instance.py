"""单实例互斥（§17）。

这里最有价值的一条是 `test_two_processes_really_are_refused`：它起两个真子进程。
进程间互斥用假后端在同一个 Python 进程里测等于什么都没测 —— 真问题是
"另一个进程会不会照样把同一个库打开"，只有真进程能回答。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from qingdan.core import clock as clock_mod
from qingdan.core import instance
from qingdan.core.instance import (FakeBackend, Instance, WinMutexBackend,
                                   foreground_existing, mutex_name)
from qingdan.core.registry import call
from qingdan.core.store import Store

SRC = str(Path(__file__).resolve().parents[1] / "src")
IS_WIN = os.name == "nt"


@pytest.fixture
def fake():
    return FakeBackend()


def _status_file(data_dir: Path, port: int = 51234) -> None:
    (data_dir / "http.json").write_text(
        json.dumps({"port": port, "token": "t"}), encoding="utf-8")


def test_second_instance_is_refused_and_points_at_the_running_one(tmp_path, fake):
    _status_file(tmp_path, 47117)
    first = Instance(backend=fake)
    got = first.acquire(tmp_path)
    assert got["held"] is True and got["pid"] == os.getpid()

    second = Instance(backend=fake)
    out = second.acquire(tmp_path)
    assert out["held"] is False
    assert out["reason_key"] == "another_instance", "只出机器键，文案归调用方"
    assert out["running"]["port"] == 47117, "得告诉用户正在跑的是哪一个"


def test_different_data_dirs_are_different_instances(tmp_path, fake):
    a, b = tmp_path / "work", tmp_path / "home"
    a.mkdir(); b.mkdir()
    one, two = Instance(backend=fake), Instance(backend=fake)
    assert one.acquire(a)["held"] and two.acquire(b)["held"], \
        "锁按目录分，多份清单并存不该互相拦住启动"
    assert mutex_name(a) != mutex_name(b)


def test_mutex_name_follows_the_resolved_path(tmp_path):
    """同一个目录，写法不同必须落到同一把锁 —— 否则双击、快捷方式、命令行各算一个实例。"""
    (tmp_path / "qd").mkdir()
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        assert mutex_name("qd") == mutex_name(tmp_path / "qd"), "相对路径要归一"
        assert mutex_name("QD") == mutex_name(tmp_path / "qd"), "大小写写法要归一"
        assert mutex_name(tmp_path / "qd") != mutex_name(tmp_path / "qd2")
    finally:
        os.chdir(cwd)
    assert mutex_name(tmp_path / "qd").startswith("Local\\qingdan-")


def test_release_then_reacquire(tmp_path, fake):
    inst = Instance(backend=fake)
    assert inst.acquire(tmp_path)["held"] is True
    inst.release()
    assert inst.held is False
    assert Instance(backend=fake).acquire(tmp_path)["held"] is True


def test_acquiring_twice_in_the_same_process_is_not_self_deadlock(tmp_path, fake):
    inst = Instance(backend=fake)
    inst.acquire(tmp_path)
    out = inst.acquire(tmp_path)
    assert out["held"] is True and out["reason_key"] == "already_acquired_by_us", \
        "自己重复获取不能被判成「另一个实例在跑」，否则重启路径会把活着的自己赶走"


@pytest.mark.skipif(not IS_WIN, reason="内核互斥体是 Windows 机制")
def test_real_windows_mutex_is_mutually_exclusive(tmp_path):
    """真 CreateMutexW：同进程两次申请也要被拒 —— 语义与跨进程一致。"""
    real = Instance(backend=WinMutexBackend())
    assert real.acquire(tmp_path)["held"] is True
    try:
        assert Instance(backend=WinMutexBackend()).acquire(tmp_path)["held"] is False
        real.release()
        again = Instance(backend=WinMutexBackend())
        assert again.acquire(tmp_path)["held"] is True, "还锁之后要能重新拿到"
        again.release()
    finally:
        real.release()


@pytest.mark.skipif(not IS_WIN, reason="内核互斥体是 Windows 机制")
def test_two_processes_really_are_refused(tmp_path):
    """起两个真子进程：第二个必须被拒，第一个正常持有。"""
    data = tmp_path / "db-dir"
    data.mkdir()
    env = dict(os.environ, PYTHONPATH=SRC)
    holder = subprocess.Popen(
        [sys.executable, "-c",
         f"import sys; sys.path.insert(0, {SRC!r});"
         "from qingdan.core.instance import Instance, WinMutexBackend;"
         f"i = Instance(backend=WinMutexBackend());"
         f"print('HELD', i.acquire(r'{data}')['held'], flush=True);"
         "import time; time.sleep(120)"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
    try:
        line = holder.stdout.readline().strip()
        assert line == "HELD True", f"第一个进程没拿到锁：{line} {holder.stderr.read()}"

        second = subprocess.run(
            [sys.executable, "-c",
             f"import sys; sys.path.insert(0, {SRC!r});"
             "from qingdan.core.instance import Instance, WinMutexBackend;"
             "i = Instance(backend=WinMutexBackend());"
             f"r = i.acquire(r'{data}');"
             "print('HELD', r['held'], r.get('reason_key'))"],
            capture_output=True, text=True, env=env, timeout=90)
        assert "HELD False another_instance" in second.stdout, \
            second.stdout + second.stderr
    finally:
        holder.kill()
        holder.wait(timeout=30)


def test_two_instances_on_one_db_emit_identical_revs(tmp_path, monkeypatch):
    """锁要防的到底是什么：两个进程共用持久化的 device_id，各自发号必然撞车。

    这不是"猜想有风险"，是当场复现：`(ms, seq, dev)` 三元组完全相同，
    LWW 用 seq 决胜负的那一半直接失效；而 `observe()` 看到 dev 相同又跳过保护，
    连补救通道都没有。所以单实例不是体验问题，是数据正确性问题。
    """
    db = tmp_path / "db.sqlite"
    monkeypatch.setattr(clock_mod.time, "time", lambda: 1_700_000_000.5)

    a = Store(db)                            # 第一个"进程"
    b = Store(db)                            # 第二个：同一个文件，device_id 从表里读回来
    assert a.device_id == b.device_id, "device_id 是持久化的，两个进程天然共用"

    ra, rb = a.next_rev(), b.next_rev()
    assert ra == rb, f"两个实例没撞车（{ra} / {rb}），这条测试的前提消失了"

    # 第二跳也照样撞：observe() 对同 dev 直接 return，不会把对方的号顶上去
    b.observe(ra)
    assert b.next_rev() == a.next_rev(), "撞一次是巧合，每跳都撞才说明必须互斥"


def test_instance_status_endpoint_exists_and_is_honest(app_ctx):
    st = call("app.instance_status")
    assert isinstance(st["held"], bool)
    assert st["pid"] == os.getpid()
    assert st["supported"] is IS_WIN, "Windows 上必须真支持互斥；平台不支持要如实说"
    assert st["data_dir"], "接口要能指出锁的是哪个目录"


def test_foreground_existing_never_raises():
    """找不到窗口时只能返回 False。二次启动的兜底路径里抛异常，
    用户看到的就是"双击没反应"，比多开一个实例更难排查。"""
    assert foreground_existing("清单-这个标题一定不存在") is False


def test_refusal_is_visible_even_without_a_console(monkeypatch, tmp_path):
    """windowed exe 没有控制台：抢不到锁时必须走原生提示，不能只 print。

    用户双击第二次若"什么都没发生"，第一反应是去任务管理器杀掉正在跑的那个 ——
    那才是真的把数据截断在半途。这里断言提示文案把"为什么"说清楚了。
    """
    seen: list[tuple[str, str]] = []
    monkeypatch.setattr(instance, "POPUP", lambda caption, text: seen.append((caption, text)))

    ok = instance.warn_already_running(tmp_path / "qd", port=51234)
    assert ok is True, "Windows 上必须真的给出可见提示"
    caption, text = seen[0]
    assert caption == "清单"
    assert "已经在运行" in text and "51234" in text
    assert "版本戳" in text, "得说清后果，否则用户会以为是无语了"

    monkeypatch.setattr(instance, "POPUP",
                        lambda caption, text: (_ for _ in ()).throw(OSError("no window station")))
    assert instance.warn_already_running(tmp_path) is False, "提示失败也不能让启动流程崩"


def test_no_test_pops_a_real_dialog():
    """守卫：任何测试都不许真的往用户桌面弹模态框（MessageBoxW 会等人点确定）。

    这条不是洁癖 —— 假渠道、假注册表、假 transport 已经是这个项目的规矩，
    弹窗是同一类副作用，漏一个就会让整个测试集卡在无人操作的机器上等点击。
    """
    here = Path(__file__).resolve().parent
    offenders = []
    for py in sorted(here.glob("test_*.py")):
        text = py.read_text(encoding="utf-8")
        if "MessageBoxW" in text and "monkeypatch" not in text:
            offenders.append(py.name)
    assert not offenders, f"这些测试直接调了模态框且没打桩：{offenders}"
