"""生成应用图标（M6，2026-11-10 改为按尺寸原生绘制）。

设计约束：**16×16 下必须仍可辨认**。清单类图标的通病是画一本"记事本"，
缩到 16px 就糊成一团灰块。所以这里用最简单的形状语言：
圆角方块（品牌底）+ 一根粗对勾（语义），没有文字、没有细节。

**为什么按尺寸原生绘制**：早期版本只画一张 1024 母图再 LANCZOS 缩到各尺寸，
16px 任务栏上对勾细到发虚、圆角边距显得空旷。小尺寸需要**更粗的笔画和更小的边距**，
这是缩放给不了的。所以 `draw(s)` 直接按目标尺寸画，ICO 里每个条目都是一张原生图。

**为什么自己写 ICO 容器**：Pillow 的 `save(..., sizes=[...])` 只会从母图缩放；
`append_images` 在 ICO 下只写出 1 个条目（实测踩过，产物 689 字节）。
PNG 压缩的 ICO 条目是 Vista 以来的标准格式，手写 40 行、可被 `ico_entries` 逐条验证。

Pillow 只在打包期用，不进运行时依赖 —— 产物里只有 .ico 文件。
"""
from __future__ import annotations

import io
import struct
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "packaging"

ACCENT = (47, 111, 237, 255)    # 与 CSS --accent blue 对齐
WHITE = (255, 255, 255, 255)
ICO_SIZES = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
MASTER = 1024                   # 高分辨率母图，只用于 icon.png 与商店图


def draw(s: int) -> Image.Image:
    """按目标尺寸原生绘制。笔画/边距随尺寸分档，小尺寸刻意加粗。

    分档而不是连续插值：连续公式在 24px 这种中间尺寸会算出 2.04px 这种半像素笔画，
    抗锯齿后边缘发灰；分档保证每档都是整数像素。
    """
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    if s <= 32:
        pad_f, rad_f, w_f = 0.03, 0.24, 0.145      # 任务栏/托盘：笔画最粗、边距最小
    elif s <= 64:
        pad_f, rad_f, w_f = 0.05, 0.23, 0.115
    else:
        pad_f, rad_f, w_f = 0.06, 0.22, 0.085      # 大图：留白多、笔画细，显得精致

    pad = max(1, round(s * pad_f))
    radius = max(2, round(s * rad_f))
    d.rounded_rectangle([pad, pad, s - pad - 1, s - pad - 1], radius=radius, fill=ACCENT)

    w = max(2, round(s * w_f))
    pts = [(s * 0.28, s * 0.52), (s * 0.44, s * 0.68), (s * 0.74, s * 0.34)]
    d.line(pts, fill=WHITE, width=w, joint="curve")
    r = w / 2
    for x, y in pts:
        d.ellipse([x - r, y - r, x + r, y + r], fill=WHITE)
    return img


def write_ico(path: Path, renders: list[Image.Image]) -> None:
    """手写 PNG 压缩的 ICO 容器：目录项 16 字节 + 各尺寸的 PNG 字节流。"""
    blobs = []
    for im in renders:
        buf = io.BytesIO()
        im.save(buf, format="PNG")
        blobs.append(buf.getvalue())

    n = len(renders)
    header = struct.pack("<HHH", 0, 1, n)
    offset = 6 + 16 * n
    directory = b""
    for im, blob in zip(renders, blobs):
        w, h = im.size
        directory += struct.pack("<BBBBHHII",
                                 0 if w >= 256 else w, 0 if h >= 256 else h,
                                 0, 0, 1, 32, len(blob), offset)
        offset += len(blob)
    path.write_bytes(header + directory + b"".join(blobs))


def main() -> None:
    master = draw(MASTER)
    png = OUT / "icon.png"
    master.save(png)

    # .ico 每个尺寸都是原生绘制，Windows 按上下文挑：任务栏 32、托盘 16、快捷方式 48。
    ico = OUT / "qd.ico"
    write_ico(ico, [draw(w) for w, _ in ICO_SIZES])

    entries = ico_entries(ico)
    print(f"Generated icons: {png} ({png.stat().st_size} B) / {ico} ({ico.stat().st_size} B)")
    print(f"ICO sizes: {entries}")
    assert len(entries) == len(ICO_SIZES), f"ico 只写入了 {len(entries)} 个尺寸，应为 {len(ICO_SIZES)}"
    assert sorted(entries) == sorted(ICO_SIZES), f"ico 尺寸与预期不符：{entries}"


def ico_entries(path: Path) -> list[tuple[int, int]]:
    """直接读 ICO 目录，确认真的每个尺寸都在——文件大小会骗人，条目数不会。

    目录项 16 字节：width(0) height(1) colorcount(2) reserved(3) planes(4-5)
    bitcount(6-7) bytesInRes(8-11) offset(12-15)。宽高各 1 字节，0 表示 256。
    （曾经误读 e[6]/e[7]，那是 bitcount，打出来的"尺寸"全是假的。）
    """
    with open(path, "rb") as f:
        header = f.read(6)
        count = struct.unpack("<H", header[4:6])[0]
        out = []
        for _ in range(count):
            e = f.read(16)
            w, h = e[0], e[1]
            out.append((256 if w == 0 else w, 256 if h == 0 else h))
        return out


if __name__ == "__main__":
    main()

