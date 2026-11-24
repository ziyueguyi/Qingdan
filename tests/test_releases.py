import threading

import httpx
import pytest
from fastapi.testclient import TestClient

from qingdan.api.http import build_app
from qingdan.core import access,releases
from qingdan.core.context import ctx
from qingdan.core.registry import call


def transport(monkeypatch,handler):
    client=httpx.Client
    monkeypatch.setattr(releases.httpx,'Client',lambda **kwargs:client(
        **kwargs,transport=httpx.MockTransport(handler)))


def test_offline_notes_never_contact_network(app_ctx,monkeypatch):
    monkeypatch.setattr(releases,'_latest',lambda:pytest.fail('must remain offline'))
    data=call('app.release_info')
    assert data['current_version']=='0.4.0'
    assert data['status']=='unchecked'
    assert 'Chinese' in data['current_notes']
    assert '中国' in call('app.release_info',lang='zh')['current_notes']
    assert data['releases_url']==releases.RELEASES_URL
    assert access.policy('app.release_info') is None


@pytest.mark.parametrize('tag,status',[('v0.4.1','available'),('0.4.0','current'),
    ('v0.3.9','current'),('v0.10.0','available')])
def test_stable_version_comparison(app_ctx,monkeypatch,tag,status):
    requests=[]
    def handler(request):
        requests.append(request)
        return httpx.Response(200,json={'tag_name':tag,'draft':False,'prerelease':False,
                                      'body':'<img src=x onerror=alert(1)> notes'})
    transport(monkeypatch,handler)
    with TestClient(build_app()) as client:
        response=client.post('/api/v1/app/release',json={'check':True})
    assert response.status_code==200
    data=response.json()['data']
    assert data['status']==status
    assert data['latest_version']==tag.removeprefix('v')
    assert data['latest_notes'].startswith('<img') # caller renders escaped text
    assert len(requests)==1 and str(requests[0].url)==releases.LATEST_URL
    assert 'authorization' not in requests[0].headers


@pytest.mark.parametrize('status,body,reason',[
    (403,{},'rate_limit'),(429,{},'rate_limit'),(404,{},'http'),(302,{},'http'),
    (200,{'tag_name':'v0.5.0-rc1','draft':False,'prerelease':False},'invalid'),
    (200,{'tag_name':'v0.5.0','draft':True,'prerelease':False},'invalid'),
    (200,{'tag_name':'v0.5.0','draft':False,'prerelease':True},'invalid'),
    (200,[],'invalid'),(200,{'tag_name':'v0.5.0'},'invalid'),
    (200,{'tag_name':'v01.4.1','draft':False,'prerelease':False},'invalid'),
    (200,{'tag_name':'v0.5.0+build','draft':False,'prerelease':False},'invalid'),
])
def test_failed_checks_keep_bundled_notes(app_ctx,monkeypatch,status,body,reason):
    transport(monkeypatch,lambda request:httpx.Response(status,json=body))
    data=call('app.release_info',check=True)
    assert data['status']=='failed' and data['reason']==reason
    assert data['current_notes']
    assert call('task.list')['total']==0


def test_network_failure_and_response_size_limit(app_ctx,monkeypatch):
    def fail(request):raise httpx.ConnectError('offline')
    transport(monkeypatch,fail)
    assert releases.release_info(True)['reason']=='network'
    # Restore factory before wrapping it with a new transport.
    monkeypatch.undo()
    transport(monkeypatch,lambda request:httpx.Response(200,content=b'x'*(releases.MAX_RESPONSE+1)))
    assert releases.release_info(True)['reason']=='invalid'


def test_trickling_response_stops_at_total_deadline(app_ctx,monkeypatch):
    transport(monkeypatch,lambda request:httpx.Response(200,content=b'{}'))
    times=iter([0,releases.TOTAL_TIMEOUT+1])
    monkeypatch.setattr(releases.time,'monotonic',lambda:next(times))
    data=call('app.release_info',check=True)
    assert data['status']=='failed' and data['reason']=='network'
    assert data['current_notes']


def test_release_check_is_local_only_even_for_full_remote_token(app_ctx,monkeypatch):
    token=call('access.token_create',name='full',permissions=list(access.PERMISSIONS),features=['*'])
    monkeypatch.setattr(releases,'_latest',lambda:pytest.fail('remote callers must not trigger release checks'))
    with TestClient(build_app(remote=True)) as client:
        response=client.post('/api/v1/app/release',json={'check':True},
            headers={'Authorization':'Bearer '+token['token']})
    assert response.status_code==403 and response.json()['error']['code']=='LOCAL_ONLY'


def test_cross_site_page_cannot_trigger_a_release_check(app_ctx,monkeypatch):
    monkeypatch.setattr(releases,'_latest',lambda:pytest.fail('cross-site request triggered network'))
    with TestClient(build_app()) as client:
        response=client.post('/api/v1/app/release',json={'check':True},headers={
            'Origin':'https://attacker.invalid','Sec-Fetch-Site':'cross-site',
            'Host':'127.0.0.1:19527'})
    assert response.status_code==403 and response.json()['error']['code']=='ORIGIN_DENIED'


def test_check_does_not_hold_task_operation_lock(app_ctx,monkeypatch):
    entered,finish=threading.Event(),threading.Event()
    def handler(request):
        entered.set()
        assert finish.wait(5)
        return httpx.Response(200,json={'tag_name':'v0.4.0','draft':False,'prerelease':False})
    transport(monkeypatch,handler)
    results=[]
    worker=threading.Thread(target=lambda:results.append(call('app.release_info',check=True)))
    worker.start()
    try:
        assert entered.wait(2)
        assert ctx.need_store().operation_lock.acquire(timeout=1)
        ctx.need_store().operation_lock.release()
        assert call('task.list')['total']==0
    finally:
        finish.set();worker.join(6)
    assert results[0]['status']=='current'
