"""A0 spike：Windows 系统通知到底能不能用。

winotify 是纯 Python（py3-none-any），但它内部通过 PowerShell 调 WinRT
`Windows.UI.Notifications`，所以「装得上」不等于「弹得出」——必须真跑一次。
"""
import subprocess
import sys

print(f"[a0] python {sys.version.split()[0]}")

# ① winotify 能否 import 并真正 show
try:
    from winotify import Notification

    toast = Notification(app_id="Qingdan 清单", title="M3 A0 spike",
                         msg="如果你看到这条通知，系统通知渠道可用", duration="short")
    toast.show()
    print("[a0] OK  winotify.show() 未报错")
except Exception as e:
    print(f"[a0] FAIL winotify -> {type(e).__name__}: {e}")

# ② 它依赖的 PowerShell 通道是否可用（失败时错误常被吞掉，所以单独探一次）
for shell in ("powershell", "pwsh"):
    try:
        r = subprocess.run([shell, "-NoProfile", "-Command", "$PSVersionTable.PSVersion.ToString()"],
                           capture_output=True, text=True, timeout=20)
        print(f"[a0] {shell}: rc={r.returncode} {(r.stdout or r.stderr).strip()[:80]}")
    except Exception as e:
        print(f"[a0] {shell}: 不可用 {type(e).__name__}: {e}")

# ③ 兜底方案：ctypes 调 user32.MessageBoxW（零依赖，一定能弹，但是模态框）
try:
    import ctypes
    print("[a0] ctypes user32 可用:", hasattr(ctypes.windll, "user32"))
except Exception as e:
    print("[a0] ctypes fallback FAIL:", e)

print("[a0] 完成 —— 请确认桌面上是否出现了标题为「M3 A0 spike」的通知")
