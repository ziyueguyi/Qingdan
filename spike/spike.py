"""M0 spike：验证 Python 3.14 + pythonnet + pywebview(WebView2) 这条链能否真跑起来。

带看门狗与分阶段计时，任何一步卡住都会打印卡在哪并以 99 退出（避免 stdout 缓冲丢失诊断信息）。
必须用 `python -u` 运行。
"""
import os
import sys
import threading
import time

STAGE = "init"
T0 = time.time()


def mark(name):
    global STAGE
    STAGE = name
    print(f"[spike] +{time.time()-T0:5.2f}s  -> {name}", flush=True)


def watchdog(seconds=25):
    time.sleep(seconds)
    print(f"[spike] TIMEOUT 卡在阶段: {STAGE}", flush=True)
    os._exit(99)


threading.Thread(target=watchdog, daemon=True).start()

mark(f"python {sys.version.split()[0]} / {sys.platform}")

try:
    import clr
    mark("OK import clr (pythonnet 已加载 .NET 运行时)")
except Exception as e:
    print(f"[spike] FAIL import clr -> {type(e).__name__}: {e}", flush=True)
    sys.exit(10)

try:
    import webview
    mark(f"OK import webview {getattr(webview, '__version__', '?')}")
except Exception as e:
    print(f"[spike] FAIL import webview -> {type(e).__name__}: {e}", flush=True)
    sys.exit(11)

try:
    win = webview.create_window(
        "qingdan spike", html="<h2 style='font-family:sans-serif'>WebView2 OK</h2>",
        width=380, height=200, on_top=True)
    mark("OK create_window")
except Exception as e:
    print(f"[spike] FAIL create_window -> {type(e).__name__}: {e}", flush=True)
    sys.exit(12)


def _closer():
    time.sleep(4)
    mark("尝试 win.destroy()")
    try:
        win.destroy()          # pywebview 6.x 是 destroy()，没有 close()
    except Exception as e:
        print(f"[spike] destroy 异常 -> {type(e).__name__}: {e}", flush=True)


threading.Thread(target=_closer, daemon=True).start()

mark("进入 webview.start(edgechromium)")
try:
    webview.start(gui="edgechromium", debug=False)
    mark("OK webview.start 已返回")
except Exception as e:
    print(f"[spike] FAIL webview.start -> {type(e).__name__}: {e}", flush=True)
    sys.exit(13)

print("[spike] PASS  py3.14 + pythonnet + WebView2 链路成立", flush=True)
