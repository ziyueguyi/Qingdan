"""图标可辨认性的可执行守卫。

图标这种东西最容易"改完看着还行"就提交，等用户在 16px 任务栏上看见一团灰才发现。
这里把「小尺寸下对勾必须还在」变成数字：白色笔画像素占比、品牌蓝占比都要落在
经验区间内，而且**原生 16px 必须明显优于把 1024 母图缩下来的 16px** ——
后者是 2026-11-10 改成按尺寸原生绘制的理由（实测 0.102 vs 0.043）。
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from make_icon import ICO_SIZES, MASTER, draw, ico_entries   # noqa: E402

ICO = ROOT / "packaging" / "qd.ico"


def _ratios(im: Image.Image) -> tuple[float, float]:
    px = list(im.getdata())
    n = len(px)
    white = sum(1 for p in px if p[3] > 200 and p[0] > 200 and p[1] > 200 and p[2] > 200)
    blue = sum(1 for p in px if p[3] > 200 and p[2] > 180 and p[0] < 120)
    return white / n, blue / n


def test_native_16px_check_is_legible():
    white, blue = _ratios(draw(16))
    assert 0.07 <= white <= 0.20, f"16px 白色笔画占比 {white:.3f} 出界（对勾太细或太粗）"
    assert 0.45 <= blue <= 0.75, f"16px 品牌蓝占比 {blue:.3f} 出界（底色被吃掉或过满）"


def test_native_beats_downscaled_at_16():
    """按尺寸原生绘制的意义所在：缩放版在小尺寸上会丢掉笔画。"""
    native_white, _ = _ratios(draw(16))
    down_white, _ = _ratios(draw(MASTER).resize((16, 16), Image.LANCZOS))
    assert native_white > down_white * 1.5, (
        f"原生 16px（{native_white:.3f}）没有明显优于缩放版（{down_white:.3f}），"
        "分档参数可能被改坏了")


def test_32px_taskbar_size_is_legible():
    white, blue = _ratios(draw(32))
    assert 0.07 <= white <= 0.20 and 0.45 <= blue <= 0.75


def test_ico_carries_every_advertised_size():
    """Windows 按上下文挑尺寸：任务栏 32、托盘 16、快捷方式 48，缺一个就回退模糊放大。"""
    entries = ico_entries(ICO)
    assert sorted(entries) == sorted(ICO_SIZES), f"ico 尺寸不全：{entries}"


def test_ico_is_not_a_stub():
    """116 字节 / 689 字节的事故都出过：文件大小会骗人，但太小一定有问题。"""
    assert ICO.stat().st_size > 2000, f"ico 只有 {ICO.stat().st_size} B，疑似只写了一个条目"


def test_master_has_transparent_margin():
    """四角必须透明，否则任务栏上会顶出一个方块边。"""
    im = draw(256)
    corners = [im.getpixel((0, 0)), im.getpixel((255, 0)),
               im.getpixel((0, 255)), im.getpixel((255, 255))]
    assert all(c[3] == 0 for c in corners), f"四角不透明：{corners}"
