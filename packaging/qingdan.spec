# -*- mode: python ; coding: utf-8 -*-
"""Qingdan 打包配置。

体积策略（对应开发文档 §4/§17）：
  · onedir 而不是 onefile —— onefile 每次启动要解压到临时目录，慢且杀软误报更多；
  · 显式 excludes 把 stdlib 里用不到的大件踢掉（tkinter/unittest/pydoc_data）；
  · UPX 压缩，但**必须排除 VC/UCRT/python 运行时 DLL** —— 压了会崩（实测过的常见坑）；
  · 前端资源走 datas，运行时从 sys._MEIPASS 读。

体积门禁见 `scripts/check_size.py`，阈值定在实测值之上而不是估算值。
"""
import os
import subprocess
from pathlib import Path

ROOT = Path(SPECPATH).parent          # noqa: F821  （PyInstaller 注入）
SRC = ROOT / "src"
WEB = SRC / "qingdan" / "web"
ICON = ROOT / "packaging" / "qd.ico"
TRAY_PNG = ROOT / "packaging" / "icon.png"
CLI_LAUNCHER = ROOT / "buildtmp" / "cli-launcher" / "QingdanCLI.exe"
CLI_LAUNCHER.parent.mkdir(parents=True, exist_ok=True)
CSC = Path(os.environ.get("WINDIR", "C:/Windows")) / "Microsoft.NET/Framework64/v4.0.30319/csc.exe"
subprocess.run([str(CSC), "/nologo", "/optimize+", "/target:exe", "/out:" + str(CLI_LAUNCHER),
                str(ROOT / "packaging" / "cli_launcher.cs")], check=True)


# 只排除"确定用不到"的，宁可少排也别排错 —— 排错的表现是运行时 ImportError，
# 而 windowed 模式下没有控制台，那种错误极难定位。
# 真实教训：这版曾经排除 http.server，结果 pywebview 经 wsgiref.simple_server
# 依赖它，exe 启动直接崩。所以 stdlib 的间接依赖不要凭直觉砍。
EXCLUDES = [
    "tkinter", "_tkinter",
    "unittest", "pydoc_data",
    "setuptools", "pkg_resources", "pip",
    "PySide6", "PyQt5", "PyQt6",
    "numpy", "pandas",
    "pytest", "pygments",
    "dulwich.tests",
    # Application backups/exports use SQLite/JSON/CSV; Git packs use zlib.
    # Python 3.14 shutil/tarfile gracefully handle missing optional Zstandard.
    # Keep gzip/zip/zlib and HTTPS; omit only unused .tar.zst support (~0.5 MB).
    "compression.zstd", "_zstd",
    # Icons use PNG/ICO/BMP with no ICC profile conversion. Keep Image itself.
    "PIL.ImageCms", "PIL._imagingcms",
    # Pillow 只为托盘图标服务。它默认会拖进 ~10MB 的图片格式解码器，
    # 其中 _avif 单独就 7.5MB —— 实测体积从 37MB 冲到 48MB 直接顶破门禁。
    #
    # 但 **ICO 和 BMP 不能排除**：pystray 在 Windows 上会把图标在内存里转成 ICO
    # 再交给 Shell_NotifyIcon，排掉 IcoImagePlugin 会让托盘线程直接崩
    # （KeyError: 'ICO'）。省体积省到砍功能，是这次实测踩出来的边界。
    "PIL.AvifImagePlugin",
    "PIL.BlpImagePlugin", "PIL.BufrStubImagePlugin", "PIL.CurImagePlugin",
    "PIL.DcxImagePlugin", "PIL.DdsImagePlugin", "PIL.EpsImagePlugin",
    "PIL.FitsImagePlugin", "PIL.FliImagePlugin", "PIL.FpxImagePlugin",
    "PIL.FtexImagePlugin", "PIL.GbrImagePlugin", "PIL.GifImagePlugin",
    "PIL.GribStubImagePlugin", "PIL.Hdf5StubImagePlugin", "PIL.IcnsImagePlugin",
    "PIL.ImImagePlugin", "PIL.ImtImagePlugin",
    "PIL.IptcImagePlugin", "PIL.Jpeg2KImagePlugin", "PIL.JpegImagePlugin",
    "PIL.McIdasImagePlugin", "PIL.MicImagePlugin", "PIL.MpegImagePlugin",
    "PIL.MpoImagePlugin", "PIL.MspImagePlugin", "PIL.PalmImagePlugin",
    "PIL.PcdImagePlugin", "PIL.PcxImagePlugin", "PIL.PdfImagePlugin",
    "PIL.PixarImagePlugin", "PIL.PsdImagePlugin",
    "PIL.QoiImagePlugin", "PIL.SgiImagePlugin", "PIL.SpiderImagePlugin",
    "PIL.SunImagePlugin", "PIL.TgaImagePlugin", "PIL.TiffImagePlugin",
    "PIL.WebPImagePlugin", "PIL.WmfImagePlugin", "PIL.XbmImagePlugin",
    "PIL.XVThumbImagePlugin",
]

# 动态导入的地方 PyInstaller 静态分析抓不到，必须显式列出来
HIDDEN = [
    "uvicorn.logging",
    "uvicorn.loops",
    "uvicorn.loops.auto",
    "uvicorn.loops.asyncio",
    "uvicorn.protocols",
    "uvicorn.protocols.http",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.protocols.websockets.wsproto_impl",
    "uvicorn.lifespan",
    "uvicorn.lifespan.on",
    "webview.platforms.edgechromium",
    "webview.platforms.winforms",
    "clr",
    "anyio._backends._asyncio",
    "winotify",
]

a = Analysis(
    [str(ROOT / "packaging" / "entry.py")],
    pathex=[str(SRC)],
    binaries=[],
    datas=[(str(WEB), "qingdan/web"), (str(CLI_LAUNCHER), "."),
           # 托盘运行时用 PIL 打开 PNG（.ico 是 exe 自身的图标，两者用途不同）
           (str(TRAY_PNG), "qingdan/assets")],
    hiddenimports=HIDDEN + ["pystray", "pystray._win32", "PIL", "PIL._tkinter_finder"],
    hookspath=[],
    runtime_hooks=[],
    excludes=EXCLUDES,
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Qingdan",
    icon=str(ICON) if ICON.is_file() else None,   # 缺图标时不阻断构建，只退回默认图标
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,          # 桌面程序不带控制台窗口
    disable_windowed_traceback=False,
    contents_directory=".",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[
        # 压这些会崩：VC 运行时与解释器本体带签名/校验，UPX 改字节流就废了
        "vcruntime140*.dll", "vcruntime140_1.dll",
        "ucrtbase*.dll", "msvcp*.dll",
        "python*.dll",
        "libcrypto*.dll", "libssl*.dll",
    ],
    name="Qingdan",
)

