"""Preflight diagnostics that work before the HTML renderer is available.

Registry detection follows Microsoft's WebView2 distribution guidance:
https://learn.microsoft.com/microsoft-edge/webview2/concepts/distribution
No installer is downloaded or executed by this module.
"""
from __future__ import annotations

import os
import re
import webbrowser

DOWNLOAD_URL = 'https://developer.microsoft.com/microsoft-edge/webview2/#download-section'
_CLIENT = r'SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}'


def webview2_status() -> dict:
    if os.name != 'nt':
        return {'state': 'not_applicable', 'version': None}
    import winreg

    unreadable = False
    # Explicit 32-bit registry view also works from the packaged x64 process.
    for root, view in ((winreg.HKEY_CURRENT_USER, 0),
                       (winreg.HKEY_LOCAL_MACHINE, winreg.KEY_WOW64_32KEY)):
        try:
            with winreg.OpenKey(root, _CLIENT, 0, winreg.KEY_READ | view) as key:
                version, _ = winreg.QueryValueEx(key, 'pv')
            if isinstance(version, str) and re.fullmatch(r'\d{1,5}(?:\.\d{1,5}){3}', version):
                if any(int(part) for part in version.split('.')):
                    return {'state': 'installed', 'version': version}
        except FileNotFoundError:
            continue
        except OSError:
            unreadable = True
    # Access denied is not proof the runtime is absent. Permit startup to try.
    return {'state': 'unknown' if unreadable else 'missing', 'version': None}


def show_missing_runtime() -> None:
    """Native dialog is intentional: the renderer and its i18n do not exist yet."""
    import ctypes

    message = ('清单需要可用的 Microsoft Edge WebView2 Runtime 才能显示窗口。\n\n'
               'Runtime 可能缺失、损坏或无法初始化。请从微软页面安装或修复 Evergreen Runtime 后重新打开清单。'
               '离线电脑可在其他电脑下载匹配系统架构的 Standalone Installer。\n'
               '本次未启动数据库、提醒或同步。\n\n'
               'Qingdan needs a working Microsoft Edge WebView2 Runtime. Install or repair the Evergreen '
               'Runtime and reopen Qingdan. For offline PCs, use the matching '
               'Standalone Installer.\n\n打开微软下载页面？ / Open Microsoft download page?')
    if ctypes.windll.user32.MessageBoxW(None, message, '清单 / Qingdan', 0x24) == 6:
        webbrowser.open(DOWNLOAD_URL)
