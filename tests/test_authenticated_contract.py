"""Authenticated operations must succeed through both shells, not merely return 401.

Gitee account metadata uses the shared fake transport. clone/fetch/push use a real
isolated local bare Git repository; no production account or messages are used.
"""
import json

import pytest
from fastapi.testclient import TestClient

from qingdan.api.http import build_app
from qingdan.cli import main
from qingdan.core.registry import REGISTRY, call
from test_m4 import bare, logged_in_only, wired, _conflict  # noqa: F401

AUTHENTICATED = [name for name, feature in REGISTRY.items() if feature.needs_auth]


@pytest.mark.parametrize('entry', ['http', 'cli'])
@pytest.mark.parametrize('name', AUTHENTICATED)
def test_authenticated_feature_succeeds(entry, name, wired, capsys):
    data_dir = str(wired.data_dir)
    call('task.create', title='Authenticated contract baseline')
    assert call('sync.run')['ok'] is True
    raw = {}
    if name in {'sync.conflicts', 'sync.conflict_resolve'}:
        target = _conflict(wired, title='Contract conflict',
                           local={'title':'Local choice'},remote={'title':'Remote choice'})
        if name == 'sync.conflict_resolve':
            raw = {'item_id':target,'choice':'local'}
    if name == 'sync.compact':
        raw = {'days':7}
    capsys.readouterr()

    if entry == 'cli':
        group, action = REGISTRY[name].cli.split(' ', 1)
        args = ['--data-dir', data_dir, group, action]
        for key, value in raw.items():
            args.extend(['--'+key.replace('_','-'), str(value)])
        result = main(args)
        output = capsys.readouterr()
        assert result == 0, (name, output.err)
        data = json.loads(output.out)
    else:
        method, path = REGISTRY[name].http
        body = dict(raw)
        for key in list(body):
            slot = '{'+key+'}'
            if slot in path:
                path = path.replace(slot,str(body.pop(key)))
        with TestClient(build_app()) as client:
            response = client.request(method,path,**({'params':body} if method in {'GET','DELETE'} else {'json':body}))
        assert response.status_code == 200, (name,response.text)
        assert response.json()['ok'] is True
        data = response.json()['data']

    assert isinstance(data,dict)
    if name in {'sync.run','sync.push'}:
        assert data['ok'] is True
    elif name == 'sync.pull':
        assert data['head']
    elif name == 'sync.history':
        assert data['items']
    elif name == 'sync.conflicts':
        assert data['total'] == 1
    elif name == 'sync.conflict_resolve':
        assert data['resolved_by'] == 'local'
        assert call('sync.conflicts')['total'] == 0
        assert call('task.get',item_id=target)['data']['title'] == 'Local choice'
