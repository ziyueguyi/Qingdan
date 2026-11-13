"""Synthetic native tray-event check; does not touch an existing application."""
import ctypes
import json
import sys
import threading
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from qingdan.shell.notifications import NotificationHost
from qingdan.shell.tray import Tray


def main():
    if sys.platform!='win32':raise RuntimeError('Windows is required')
    from pystray._util import win32
    host=NotificationHost();seen=threading.Event()
    def clicked():host._open();seen.set()
    tray=Tray(on_show=lambda:None,on_quit=lambda:None,on_toggle_alerts=lambda:None,
              is_visible=lambda:True,alerts_enabled=lambda:True,on_notification=clicked)
    try:
        assert tray.start(),'Isolated tray did not initialize'
        hwnd=getattr(tray.icon,'_hwnd',None)
        assert hwnd,'Isolated tray window is missing'
        send=ctypes.windll.user32.SendMessageW
        send.argtypes=[ctypes.c_void_p,ctypes.c_uint,ctypes.c_size_t,ctypes.c_ssize_t]
        send.restype=ctypes.c_ssize_t
        send(hwnd,win32.WM_NOTIFY,0,0x405)
        assert seen.wait(5),'Native notification event was not dispatched'
        assert json.loads(host.takeNotification())=={'inbox':True}
        assert host.takeNotification() is None
        print(json.dumps({'native_tray_event':'passed','bridge_consume_once':'passed',
                          'event':'synthetic NIN_BALLOONUSERCLICK to owned tray HWND',
                          'system_balloon_display':'not tested','physical_mouse_click':'not tested'}))
    finally:tray.stop()


if __name__=='__main__':main()
