from contextlib import nullcontext
from types import SimpleNamespace
import sys

import pytest

from qingdan.shell import runtime


@pytest.mark.parametrize('user,machine,state,version', [
    ('', '154.0.1.2', 'installed', '154.0.1.2'),
    ('154.0.1.3', None, 'installed', '154.0.1.3'),
    (None, None, 'missing', None),
    ('0.0.0.0', '', 'missing', None),
    ('garbage', None, 'missing', None),
    ('9'*4000+'.1.1.1', None, 'missing', None),
    (PermissionError(), None, 'unknown', None),
    (PermissionError(), '154.0.1.2', 'installed', '154.0.1.2'),
])
def test_runtime_registry_detection(monkeypatch, user, machine, state, version):
    calls=[]
    def open_key(root, path, reserved, access):
        calls.append((root,path,access))
        value = user if root == 'user' else machine
        if isinstance(value, Exception): raise value
        if value is None: raise FileNotFoundError()
        return nullcontext(value)
    registry=SimpleNamespace(HKEY_CURRENT_USER='user', HKEY_LOCAL_MACHINE='machine',
        KEY_READ=1, KEY_WOW64_32KEY=512, OpenKey=open_key,
        QueryValueEx=lambda value,name:(value,1))
    monkeypatch.setitem(sys.modules,'winreg',registry)
    monkeypatch.setattr(runtime,'os',SimpleNamespace(name='nt'))
    assert runtime.webview2_status()=={'state':state,'version':version}
    assert all(path.endswith('F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}') for _,path,_ in calls)
    assert all(access==513 for root,_,access in calls if root=='machine')


def test_non_windows_does_not_read_registry(monkeypatch):
    monkeypatch.setattr(runtime,'os',SimpleNamespace(name='posix'))
    assert runtime.webview2_status()['state']=='not_applicable'


def test_missing_runtime_stops_before_database_and_releases_lock(monkeypatch,tmp_path):
    import json
    from qingdan.core import context,instance
    from qingdan.shell import app
    events=[]
    lock=SimpleNamespace(acquire=lambda target:{'held':True},
                         release=lambda:events.append('release'))
    monkeypatch.setattr(instance,'current',lambda:lock)
    monkeypatch.setattr(context,'resolve_data_dir',lambda value:(tmp_path,'explicit'))
    monkeypatch.setattr(context,'init',lambda *args:pytest.fail('database must not start'))
    monkeypatch.setattr(runtime,'webview2_status',lambda:{'state':'missing','version':None})
    monkeypatch.setattr(runtime,'show_missing_runtime',lambda:events.append('guide'))
    assert app.run(tmp_path)==1
    assert events==['guide','release']
    status=json.loads((tmp_path/'http.json').read_text(encoding='utf-8'))
    assert status['error_code']=='WEBVIEW2_MISSING'


def test_renderer_failure_after_unknown_registry_has_native_diagnostic(monkeypatch,tmp_path):
    import json
    from qingdan.shell import app
    events=[]
    monkeypatch.setattr(runtime,'show_missing_runtime',lambda:events.append('guide'))
    result=app._renderer_failure(tmp_path,{'state':'unknown','version':None},RuntimeError('renderer load failed'))
    assert result==1 and events==['guide']
    status=json.loads((tmp_path/'http.json').read_text(encoding='utf-8'))
    assert status['error_code']=='WEBVIEW2_START_FAILED'
    assert 'renderer load failed' in status['error']


def test_non_windows_renderer_errors_are_not_misreported(tmp_path):
    from qingdan.shell import app
    with pytest.raises(RuntimeError,match='other platform'):
        app._renderer_failure(tmp_path,{'state':'not_applicable','version':None},RuntimeError('other platform'))


@pytest.mark.parametrize('answer,opened',[(6,True),(7,False)])
def test_native_runtime_guide_opens_download_only_on_confirmation(monkeypatch,answer,opened):
    calls=[]
    user32=SimpleNamespace(MessageBoxW=lambda *args:answer)
    monkeypatch.setitem(sys.modules,'ctypes',SimpleNamespace(windll=SimpleNamespace(user32=user32)))
    monkeypatch.setattr(runtime.webbrowser,'open',lambda url:calls.append(url))
    runtime.show_missing_runtime()
    assert calls==([runtime.DOWNLOAD_URL] if opened else [])
