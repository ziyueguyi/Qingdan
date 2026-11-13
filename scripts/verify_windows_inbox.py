"""Real Edge WebView2/pywebview bridge check in an isolated, hidden test app."""
import ctypes
import json
from pathlib import Path
import sys
import threading
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))


def main():
    import uvicorn
    import webview
    from pystray._util import win32
    from qingdan.core.context import init
    from qingdan.core.notification_inbox import capture
    from qingdan.core.alerting.base import AlertMessage
    from qingdan.api.http import build_app
    from qingdan.shell.app import _free_port,_wait_healthy
    from qingdan.shell.notifications import NotificationHost
    from qingdan.shell.tray import Tray
    root=Path(__file__).resolve().parents[1]
    directory=root/'buildtmp'/('windows-inbox-smoke-'+str(time.time_ns()))
    assert directory.resolve().is_relative_to((root/'buildtmp').resolve())
    store=init(directory,device_id='windows-inbox-smoke').store
    rows=[];messages=[]
    for index in range(10000):
        item_id=f'WIN{index:05}'
        data={'title':f'Native reminder {index:05}','status':'todo','priority':4,'tags':[],'order':index}
        rows.append((item_id,json.dumps(data)))
        messages.append(AlertMessage(item_id=item_id,kind='task',title=data['title'],fire_at_ms=1000,
                                     revision={'ms':1,'seq':0,'dev':'smoke'}))
    store.db.executemany("INSERT INTO item(id,kind,rev_ms,rev_seq,rev_dev,data,updated_at) VALUES(?,'task',1,0,'smoke',?,1)",rows)
    store.db.commit();capture(messages)
    port=_free_port();server=uvicorn.Server(uvicorn.Config(build_app(),host='127.0.0.1',port=port,log_level='error'))
    threading.Thread(target=server.run,daemon=True).start()
    assert _wait_healthy(port)
    host=NotificationHost()
    class QuietWindow:
        def show(self):pass
        def restore(self):pass
    host._window=QuietWindow() # Exercise real JS RPC without stealing user focus.
    window=webview.create_window('Qingdan isolated notification check',f'http://127.0.0.1:{port}/',
                                 width=1100,height=800,hidden=True,js_api=host)
    tray=Tray(on_show=lambda:None,on_quit=lambda:None,on_toggle_alerts=lambda:None,
              is_visible=lambda:False,alerts_enabled=lambda:True,on_notification=host._open)
    loaded=threading.Event();window.events.loaded+=loaded.set
    results={}
    def check():
        try:
            assert loaded.wait(30),'Hidden WebView2 did not load'
            assert tray.start(),'Isolated native tray did not start'
            send=ctypes.windll.user32.SendMessageW
            send.argtypes=[ctypes.c_void_p,ctypes.c_uint,ctypes.c_size_t,ctypes.c_ssize_t]
            send.restype=ctypes.c_ssize_t
            send(tray.icon._hwnd,win32.WM_NOTIFY,0,0x405)
            deadline=time.monotonic()+30
            while time.monotonic()<deadline:
                if window.evaluate_js("document.querySelectorAll('.notification-event').length===50"):break
                time.sleep(.1)
            assert window.evaluate_js("document.querySelectorAll('.notification-event').length===50"),'Real native bridge did not open inbox'
            window.evaluate_js("document.querySelector('[data-act=notification-page][data-v=\"1\"]').click()")
            deadline=time.monotonic()+15
            while time.monotonic()<deadline:
                if window.evaluate_js("document.querySelector('#modal .task-pager')?.textContent.includes('51–100')"):break
                time.sleep(.1)
            assert window.evaluate_js("document.querySelector('#modal .task-pager')?.textContent.includes('51–100')"),'Native second page missing'
            results.update(native_bridge='passed',rows=window.evaluate_js("document.querySelectorAll('.notification-event').length"),
                           tasks=10000,range=window.evaluate_js("document.querySelector('#modal .task-pager').textContent"),
                           window='hidden Edge WebView2',click='synthetic owned-tray event')
            target=window.evaluate_js("document.querySelector('.notification-event [data-act=notification-open]').dataset.id")
            window.evaluate_js("document.querySelector('.notification-event [data-act=notification-open]').click()")
            deadline=time.monotonic()+15
            # Generated fixture IDs contain only WIN and decimal digits.
            expression="Boolean(document.querySelector('#detail [data-id='+"+json.dumps(target)+"+']'))"
            while time.monotonic()<deadline:
                if window.evaluate_js(expression):break
                time.sleep(.1)
            assert window.evaluate_js(expression),'Reminder did not open its corresponding task'
            results['opened_task']=target
        except Exception as error:results['error']=repr(error)
        finally:tray.stop();window.destroy()
    try:webview.start(check,gui='edgechromium',debug=False)
    finally:server.should_exit=True;store.close()
    print(json.dumps(results,ensure_ascii=False))
    assert 'error' not in results,results


if __name__=='__main__':main()
