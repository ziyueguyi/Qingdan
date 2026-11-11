"""Remote API authentication, CRUD grants and local-management boundaries."""
import hashlib
import json
import socket
from concurrent.futures import ThreadPoolExecutor
import threading
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from qingdan.api.http import build_app
from qingdan.api.remote import RemoteServer
from qingdan.core import access, context, backup
from qingdan.core.registry import call, QdError


def token(permissions=None,features=None,**extra):
    return call('access.token_create',name='Integration',permissions=permissions,features=features,**extra)


def headers(value):
    return {'Authorization':'Bearer '+value['token']}


@pytest.mark.parametrize('path',['/api/v1/tasks','/api/v1/health','/api/v1/meta/features','/api/v1/access/tokens'])
def test_every_remote_route_requires_token(path,app_ctx):
    with TestClient(build_app(remote=True)) as client:
        response=client.get(path)
    assert response.status_code==401
    assert response.json()['error']['code']=='API_TOKEN_REQUIRED'
    assert response.headers['WWW-Authenticate']=='Bearer'


def test_read_does_not_grant_create_update_or_delete(app_ctx):
    item=call('task.create',title='Existing')
    key=token()
    with TestClient(build_app(remote=True)) as client:
        assert client.get('/api/v1/tasks',headers=headers(key)).status_code==200
        assert client.post('/api/v1/tasks',json={'title':'Denied'},headers=headers(key)).status_code==403
        assert client.patch('/api/v1/tasks/'+item['id'],json={'title':'Denied'},headers=headers(key)).status_code==403
        assert client.delete('/api/v1/tasks/'+item['id'],headers=headers(key)).status_code==403
    assert call('task.get',item_id=item['id'])['data']['title']=='Existing'
    assert call('task.list')['total']==1


@pytest.mark.parametrize('permission,allowed_method', [('create','POST'),('update','PATCH'),('delete','DELETE')])
def test_mutation_grants_are_separate(permission,allowed_method,app_ctx):
    item=call('task.create',title='Original')
    key=token([permission])
    with TestClient(build_app(remote=True)) as client:
        assert client.get('/api/v1/tasks',headers=headers(key)).status_code==403
        for method in ('POST','PATCH','DELETE'):
            path='/api/v1/tasks' if method=='POST' else '/api/v1/tasks/'+item['id']
            args={} if method=='DELETE' else {'json':{'title':'Changed'}}
            response=client.request(method,path,headers=headers(key),**args)
            assert response.status_code==(200 if method==allowed_method else 403)


def test_feature_allowlist_is_enforced_and_metadata_filtered(app_ctx):
    key=token(features=['task.list'])
    with TestClient(build_app(remote=True)) as client:
        assert client.get('/api/v1/tasks',headers=headers(key)).status_code==200
        assert client.get('/api/v1/groups',headers=headers(key)).status_code==403
        meta=client.get('/api/v1/meta/features',headers=headers(key)).json()['data']
        assert set(meta)=={'task.list'}


def test_quick_add_classifies_the_effect_not_the_http_verb(app_ctx):
    read=token(['read'])
    write=token(['create'])
    with TestClient(build_app(remote=True)) as client:
        path='/api/v1/tasks/quick-add'
        assert client.post(path,json={'text':'Preview'},headers=headers(read)).status_code==200
        for value in (True,'true','1'):
            assert client.post(path,json={'text':'Denied','create':value},headers=headers(read)).status_code==403
        assert client.post(path,json={'text':'Allowed','create':True},headers=headers(write)).status_code==200
    assert call('task.list')['total']==1


def test_compound_mutations_require_all_permissions(app_ctx):
    create=token(['create'])
    with TestClient(build_app(remote=True)) as client:
        assert client.post('/api/v1/import',json={'content':'{"product":"qingdan","items":[]}'},headers=headers(create)).status_code == 403
        assert access.can_use({'permissions':['create'],'features':['*']},'data.import') is False
        assert access.can_use({'permissions':['create','update','delete'],'features':['*']},'data.import') is True
        assert access.can_use({'permissions':['execute'],'features':['*']},'sync.pull') is False


@pytest.mark.parametrize('grant,create',[('read',False),('create',True)])
def test_quick_add_metadata_describes_conditional_permissions(grant,create,app_ctx):
    key=token([grant],['task.quick_add'])
    with TestClient(build_app(remote=True)) as client:
        meta=client.get('/api/v1/meta/features',headers=headers(key)).json()['data']
        assert set(meta)=={'task.quick_add'}
        assert meta['task.quick_add']['permissions']==['read','create']
        assert meta['task.quick_add']['conditional'] is True
        assert client.post('/api/v1/tasks/quick-add',json={'text':'Conditional','create':create},
                           headers=headers(key)).status_code==200
        assert client.post('/api/v1/tasks/quick-add',json={'text':'Denied','create':not create},
                           headers=headers(key)).status_code==403


def test_token_only_revealed_once_and_hash_only_on_disk(app_ctx):
    created=token()
    listed=call('access.token_list')
    assert 'token' not in listed['items'][0]
    assert 'digest' not in json.dumps(listed)
    with access.db() as con:
        row=con.execute('SELECT * FROM api_token').fetchone()
        assert row['digest']==hashlib.sha256(created['token'].encode()).hexdigest()
    for path in app_ctx.data_dir.glob('access.sqlite*'):
        assert created['token'].encode() not in path.read_bytes()


def test_revocation_and_scope_changes_take_effect_immediately(app_ctx):
    key=token()
    with TestClient(build_app(remote=True)) as client:
        assert client.get('/api/v1/tasks',headers=headers(key)).status_code==200
        call('access.token_update',token_id=key['item']['id'],permissions=['create'])
        assert client.get('/api/v1/tasks',headers=headers(key)).status_code==403
        assert client.post('/api/v1/tasks',headers=headers(key),json={'title':'Allowed'}).status_code==200
        call('access.token_revoke',token_id=key['item']['id'])
        assert client.post('/api/v1/tasks',headers=headers(key),json={'title':'Denied'}).status_code==401


def test_expired_token_is_rejected(app_ctx,monkeypatch):
    key=token()
    monkeypatch.setattr(access,'now_ms',lambda:key['item']['expires_at_ms']+1)
    with TestClient(build_app(remote=True)) as client:
        response=client.get('/api/v1/tasks',headers=headers(key))
        assert response.status_code==401 and response.json()['error']['code']=='API_TOKEN_EXPIRED'


def test_remote_admin_forbidden_even_with_all_permissions(app_ctx):
    key=token(list(access.PERMISSIONS))
    with TestClient(build_app(remote=True)) as client:
        assert client.get('/api/v1/access/config',headers=headers(key)).status_code==403
        assert client.post('/api/v1/access/tokens',headers=headers(key),json={'name':'Escalation'}).status_code==403
        assert client.post('/api/v1/auth/session',headers=headers(key),json={'token':'bad-token'}).status_code==403
        assert client.get('/',headers=headers(key)).status_code==404


def test_local_listener_rejects_remote_peer_and_forwarded_spoof(app_ctx):
    with TestClient(build_app(),client=('203.0.113.1',321)) as client:
        assert client.get('/api/v1/health',headers={'X-Forwarded-For':'127.0.0.1'}).status_code==403


def test_query_tokens_are_not_accepted_and_audit_does_not_log_secrets(app_ctx):
    key=token()
    with TestClient(build_app(remote=True)) as client:
        assert client.get('/api/v1/tasks',params={'token':key['token']}).status_code==401
        assert client.get('/api/v1/tasks',headers=headers(key)).status_code==200
    body=json.dumps(call('access.audit'))
    assert key['token'] not in body
    assert call('access.audit')['total']==2


def test_remote_request_rate_and_body_limit(app_ctx):
    call('access.config_set',config={'requests_per_minute':10,'max_body_kb':1})
    key=token(['read','create'])
    with TestClient(build_app(remote=True)) as client:
        response=client.post('/api/v1/tasks',headers=headers(key),json={'title':'X'*2000})
        assert response.status_code==413
        for _ in range(9): assert client.get('/api/v1/tasks',headers=headers(key)).status_code==200
        response=client.get('/api/v1/tasks',headers=headers(key))
        assert response.status_code==429 and response.headers['Retry-After']=='60'
    assert call('task.list')['total']==0


def test_access_settings_are_off_by_default_and_https_is_validated(app_ctx):
    assert call('access.config_get')['config']['enabled'] is False
    assert call('access.config_get')['config']['transport']=='https'
    with pytest.raises(QdError): call('access.config_set',config={'enabled':True,'auto_tls':False})
    with pytest.raises(QdError): call('access.token_create',name='No local admin grant',features=['access.token_create'])
    with pytest.raises(QdError): call('access.token_create',name='No unknown scope',permissions=['admin'])


def test_task_restore_cannot_resurrect_revoked_api_key(app_ctx):
    directory=app_ctx.data_dir
    snapshot=backup.create(force=True)
    key=token()
    call('access.token_revoke',token_id=key['item']['id'])
    backup.schedule_restore(snapshot['name'])
    context.shutdown(); context.init(directory)
    with pytest.raises(QdError) as error: access.authenticate(key['token'])
    assert error.value.code=='API_TOKEN_REVOKED'


def test_real_remote_listener_start_stop_and_port_conflict(app_ctx):
    with socket.socket() as probe:
        probe.bind(('127.0.0.1',0)); port=probe.getsockname()[1]
    manager=RemoteServer();access.set_runtime(manager.apply,manager.status)
    key=token()
    try:
        call('access.config_set',config={'enabled':True,'host':'127.0.0.1','port':port,'transport':'http'})
        url=f'http://127.0.0.1:{port}/api/v1/tasks'
        with httpx.Client(trust_env=False) as client:
            assert client.get(url).status_code==401
            assert client.get(url,headers=headers(key)).status_code==200
        assert call('access.config_get')['runtime']['running'] is True
        call('access.config_set',config={'enabled':False})
        assert manager.status()['running'] is False
        with socket.socket() as occupied:
            occupied.bind(('127.0.0.1',port));occupied.listen(1)
            with pytest.raises(QdError): call('access.config_set',config={'enabled':True})
        assert call('access.config_get')['config']['enabled'] is False
    finally:
        manager.close()


def test_two_listener_threads_do_not_lose_partial_updates(app_ctx,monkeypatch):
    from qingdan.core import service
    item=call('task.create',title='Original')
    original=service._load
    def slow_load(*args,**kwargs):
        value=original(*args,**kwargs);time.sleep(.02);return value
    monkeypatch.setattr(service,'_load',slow_load)
    start=threading.Barrier(2)
    def update(changes):
        start.wait();call('task.update',item_id=item['id'],**changes);app_ctx.store.close()
    with ThreadPoolExecutor(max_workers=2) as pool:
        a=pool.submit(update,{'title':'Title update'});b=pool.submit(update,{'note':'Note update'})
        a.result();b.result()
    data=call('task.get',item_id=item['id'])['data']
    assert data['title']=='Title update' and data['note']=='Note update'


def test_revocation_during_body_upload_prevents_mutation(app_ctx):
    key=token(['create'])
    def upload():
        yield b'{"title":"'
        call('access.token_revoke',token_id=key['item']['id'])
        yield b'Not created"}'
    with TestClient(build_app(remote=True)) as client:
        response=client.post('/api/v1/tasks',headers={**headers(key),'Content-Type':'application/json'},content=upload())
    assert response.status_code==401
    assert call('task.list')['total']==0


def test_remote_read_audit_does_not_trigger_task_refresh(app_ctx):
    key=token()
    before=call('app.health')['change_token']
    with TestClient(build_app(remote=True)) as client:
        assert client.get('/api/v1/tasks',headers=headers(key)).status_code==200
    assert call('access.audit')['total']==1
    assert call('app.health')['change_token']==before


def test_https_listener_with_real_certificate(app_ctx,tmp_path):
    from pathlib import Path
    import shutil
    import subprocess
    openssl=shutil.which('openssl') or str(Path('C:/Program Files/Git/usr/bin/openssl.exe'))
    if not Path(openssl).is_file(): pytest.skip('OpenSSL certificate fixture generator unavailable')
    cert,keyfile=tmp_path/'cert.pem',tmp_path/'key.pem'
    result=subprocess.run([openssl,'req','-x509','-newkey','rsa:2048','-nodes','-days','1',
                           '-keyout',str(keyfile),'-out',str(cert),'-subj','/CN=localhost',
                           '-addext','subjectAltName=IP:127.0.0.1,DNS:localhost'],capture_output=True)
    assert result.returncode==0,result.stderr
    with socket.socket() as probe:
        probe.bind(('127.0.0.1',0));port=probe.getsockname()[1]
    manager=RemoteServer();access.set_runtime(manager.apply,manager.status)
    credential=token()
    try:
        call('access.config_set',config={'enabled':True,'host':'127.0.0.1','port':port,'transport':'https',
                                         'tls_cert':str(cert),'tls_key':str(keyfile)})
        import ssl
        trust=ssl.create_default_context(cafile=str(cert))
        with httpx.Client(verify=trust,trust_env=False) as client:
            response=client.get(f'https://127.0.0.1:{port}/api/v1/tasks',headers=headers(credential))
            assert response.status_code==200
            assert client.get(f'https://127.0.0.1:{port}/api/v1/tasks').status_code==401
    finally:
        manager.close()


def test_unreadable_access_store_does_not_block_local_tasks(app_ctx):
    from qingdan.api.remote import install_remote
    (app_ctx.data_dir/'access.sqlite').write_bytes(b'not a database')
    manager=install_remote()
    try:
        assert manager.status()['running'] is False
        assert manager.status()['error_code']=='config_unavailable'
        assert call('task.create',title='Local app remains usable')['data']['title']=='Local app remains usable'
    finally: manager.close()
