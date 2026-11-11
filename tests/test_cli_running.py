"""CLI uses the live service instead of reopening a database owned by the GUI."""
import json
from fastapi.testclient import TestClient
from qingdan import cli
from qingdan.api.http import build_app
from qingdan.core import instance
from qingdan.core.registry import call


def test_cli_forwards_to_running_instance_without_init(app_ctx,monkeypatch,capsys):
    backend=instance.FakeBackend()
    owner=instance.Instance(backend=backend)
    owner.acquire(app_ctx.data_dir)
    (app_ctx.data_dir/'http.json').write_text(json.dumps({'port':54321}),encoding='utf-8')
    marker=app_ctx.data_dir/'restore_pending.json'
    marker.write_text('{"name":"do-not-apply"}',encoding='utf-8')
    def forbidden(*args,**kwargs):
        raise AssertionError('CLI must not initialize or restore an active database')
    monkeypatch.setattr(cli,'init',forbidden)
    monkeypatch.setattr(instance,'default_backend',lambda:backend)
    client=TestClient(build_app())
    monkeypatch.setattr(cli.httpx,'Client',lambda **kwargs:client)
    assert cli.main(['--data-dir',str(app_ctx.data_dir),'task','create','--title','Via running service'])==0
    result=json.loads(capsys.readouterr().out)
    assert result['data']['title']=='Via running service'
    assert marker.exists()
    assert owner.held
    assert call('task.list')['total']==1
    owner.release()


def test_cli_refuses_when_owner_has_no_service(app_ctx,monkeypatch,capsys):
    backend=instance.FakeBackend()
    owner=instance.Instance(backend=backend);owner.acquire(app_ctx.data_dir)
    monkeypatch.setattr(instance,'default_backend',lambda:backend)
    def forbidden(*args,**kwargs): raise AssertionError('must not open database')
    monkeypatch.setattr(cli,'init',forbidden)
    assert cli.main(['--data-dir',str(app_ctx.data_dir),'task','create','--title','No unsafe fallback'])==2
    assert json.loads(capsys.readouterr().err)['error']['code']=='LOCAL_SERVICE_UNAVAILABLE'
    owner.release()


def test_change_token_detects_api_write(app_ctx):
    before=call('app.health')['change_token']
    assert call('app.health')['change_token']==before
    call('task.create',title='Changed')
    assert call('app.health')['change_token']!=before


def test_generic_cli_boolean_and_equal_syntax():
    assert cli._parse_extra(['--title=Hello','--no-include-done','--count','2'])=={
        'title':'Hello','include_done':False,'count':2}
    import pytest
    with pytest.raises(ValueError): cli._parse_extra(['--title','Hello','unexpected'])


def test_browser_origin_guard(app_ctx):
    from fastapi.testclient import TestClient
    from qingdan.api.http import build_app
    with TestClient(build_app(),base_url='http://127.0.0.1:54321') as client:
        blocked=client.post('/api/v1/tasks',content='{"title":"External"}',
                            headers={'Origin':'https://external.example','Content-Type':'text/plain'})
        assert blocked.status_code==403
        assert blocked.json()['error']['code']=='ORIGIN_DENIED'
        assert client.post('/api/v1/tasks',json={'title':'Local'},
                           headers={'Origin':'http://127.0.0.1:54321','Sec-Fetch-Site':'same-origin'}).status_code==200
        assert client.get('/api/v1/health',headers={'Host':'external.example:54321','Sec-Fetch-Site':'same-origin'}).status_code==403
        assert client.get('/api/v1/health').json()['data']['tasks']==1
