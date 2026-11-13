"""Small pywebview API: consume a tray click without exposing task mutations."""
import json
import threading


class NotificationHost:
    def __init__(self):
        self._lock=threading.Lock()
        self._pending=False
        self._window=None

    def _open(self):
        with self._lock:
            self._pending=True
        if self._window is not None:
            self._window.show()
            self._window.restore()

    def takeNotification(self):
        with self._lock:
            if not self._pending:return None
            self._pending=False
        # A tray balloon has no per-message identity. Never guess which task
        # Windows displayed; open the durable inbox containing all targets.
        return json.dumps({'inbox':True})


def notification_icon(base,callback):
    """pystray leaves NIN_BALLOONUSERCLICK unhandled on Windows."""
    class Icon(base):
        def _on_notify(self,wparam,lparam):
            if (int(lparam)&0xffff)==0x405: # NIN_BALLOONUSERCLICK (WM_USER+5)
                callback()
            else:
                super()._on_notify(wparam,lparam)
    return Icon
