import socket
import ssl
import httpx
from fastapi.testclient import TestClient
from qingdan.api.http import build_app
from qingdan.api.remote import RemoteGateway
from qingdan.core import access
from qingdan.core.registry import call


def port():
    with socket.socket() as probe:probe.bind(('127.0.0.1',0));return probe.getsockname()[1]


def test_token_quota_and_notes_are_enforced(app_ctx):
    issued=call('access.token_create',name='Quota',max_calls=2,max_qps=100,note='Integration')
    headers={'Authorization':'Bearer '+issued['token']}
    with TestClient(build_app(remote=True)) as client:
        assert client.get('/api/v1/tasks',headers=headers).status_code==200
        assert client.get('/api/v1/tasks',headers=headers).status_code==200
        response=client.get('/api/v1/tasks',headers=headers)
        assert response.status_code==429 and response.json()['error']['code']=='TOKEN_QUOTA_EXHAUSTED'
        item=call('access.token_list')['items'][0]
        assert item['call_count']==2 and item['note']=='Integration' and item['last_used_ms']
        call('access.token_update',token_id=issued['item']['id'],unlimited_calls=True)
        assert client.get('/api/v1/tasks',headers=headers).status_code==200


def test_token_qps_is_independent_of_global_limit(app_ctx):
    issued=call('access.token_create',name='QPS',max_qps=1)
    headers={'Authorization':'Bearer '+issued['token']}
    with TestClient(build_app(remote=True)) as client:
        assert client.get('/api/v1/tasks',headers=headers).status_code==200
        response=client.get('/api/v1/tasks',headers=headers)
        assert response.status_code==429 and response.json()['error']['code']=='TOKEN_RATE_LIMITED'
        assert call('access.token_list')['items'][0]['call_count']==1


def test_both_protocols_with_automatic_device_certificate(app_ctx):
    gateway=RemoteGateway();access.set_runtime(gateway.apply,gateway.status)
    secure,plain=port(),port()
    key=call('access.token_create',name='Both')
    try:
        cfg=call('access.config_set',config={'enabled':True,'host':'127.0.0.1','port':secure,'http_port':plain,'transport':'both','auto_tls':True})
        assert cfg['runtime']['running'] and len(cfg['runtime']['listeners'])==2
        assert cfg['config']['tls_cert']
        trust=ssl.create_default_context(cafile=cfg['config']['tls_cert'])
        headers={'Authorization':'Bearer '+key['token']}
        with httpx.Client(verify=trust,trust_env=False) as client:
            for url in [f'https://127.0.0.1:{secure}',f'http://127.0.0.1:{plain}']:
                assert client.get(url+'/api/v1/tasks').status_code==401
                assert client.get(url+'/api/v1/tasks',headers=headers).status_code==200
    finally:gateway.close()
