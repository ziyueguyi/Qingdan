"""托盘 spike：pystray 和 pywebview 能不能同时跑。

两个都要 Windows 消息循环。pystray 自己开线程跑 loop，理论上不冲突，
但这类"两个 GUI 库抢消息泵"的问题只能实测，不能推理。

验三件事：
  1. 窗口 + 托盘同时存活（不死锁、不秒退）
  2. 托盘菜单能构造（含 checkable 项）
  3. 托盘 notify() 能不能弹出来 —— 它走 Shell_NotifyIcon 气球，
     **不需要 AUMID**，可能比 winotify 的 toast 更可靠（A0 的坑就在这）
"""
import sys
import threading
import time

print(f"[tray-spike] python {sys.version.split()[0]}")

try:
    import pystray
    from PIL import Image
    print(f"[tray-spike] OK  pystray {getattr(pystray, '__version__', '?')}")
except Exception as e:
    print(f"[tray-spike] FAIL import pystray -> {e}")
    sys.exit(1)

try:
    import webview
    print("[tray-spike] OK  webview")
except Exception as e:
    print(f"[tray-spike] FAIL import webview -> {e}")
    sys.exit(1)

state = {"menu_clicked": False, "visible": None, "errors": []}

image = Image.new("RGBA", (64, 64), (47, 111, 237, 255))


def on_open(icon, item):
    state["menu_clicked"] = True


def is_visible():
    return state["visible"] if state["visible"] is not None else True


menu = pystray.Menu(
    pystray.MenuItem("打开主窗口", on_open, default=True),
    pystray.MenuItem("提醒已启用", lambda i, m: None, checked=lambda item: True),
    pystray.MenuItem("退出", lambda i, m: i.stop()),
)
icon = pystray.Icon("qingdan", image, "清单", menu)

tray_thread = threading.Thread(target=lambda: icon.run(setup=lambda i: None), daemon=True)
tray_thread.start()
time.sleep(1.5)
print(f"[tray-spike] 托盘线程存活: {tray_thread.is_alive()}")

win = webview.create_window("清单 托盘 spike", html="<h2>窗口 + 托盘同时运行测试</h2>",
                            width=460, height=260, on_top=True)


def driver():
    time.sleep(2.0)
    try:
        icon.notify("这条来自托盘气球通知", "清单")
        print("[tray-spike] OK  icon.notify() 未抛异常")
    except Exception as e:
        state["errors"].append(f"notify: {type(e).__name__}: {e}")
    time.sleep(2.0)
    print("[tray-spike] 关闭")
    icon.stop()
    win.destroy()


threading.Thread(target=driver, daemon=True).start()

try:
    webview.start(gui="edgechromium", debug=False)
    print("[tray-spike] OK  webview.start 正常返回")
except Exception as e:
    print(f"[tray-spike] FAIL webview.start -> {type(e).__name__}: {e}")
    sys.exit(2)

time.sleep(0.5)
print(f"[tray-spike] 错误记录: {state['errors'] or '无'}")
print("[tray-spike] 完成 —— 请确认：右下角是否出现托盘图标？是否弹出了气球通知？")
