"""Catalog pagination must limit SQL results and include labels embedded in tasks."""
import pytest
from fastapi.testclient import TestClient
from qingdan.api.http import build_app
from qingdan.core.registry import call, QdError


@pytest.mark.parametrize('kind', ['group', 'tag'])
def test_catalog_pages_are_stable_and_five_rows(kind, app_ctx, monkeypatch):
    for i in range(17):
        call(kind+'.create', name=f'{kind}-{i:02}')
    def no_full_scan(*args, **kwargs):
        raise AssertionError('catalog endpoint must not deserialize the entire table')
    monkeypatch.setattr(app_ctx.store, 'list_items', no_full_scan)
    seen = []
    for offset in (0,5,10,15):
        page = call(kind+'.list', limit=5, offset=offset)
        assert page['total'] == 17
        assert len(page['items']) == min(5,17-offset)
        assert page['has_more'] == (offset < 15)
        seen += [x['data']['name'] for x in page['items']]
    assert seen == [f'{kind}-{i:02}' for i in range(17)]
    assert call(kind+'.list', limit=5, offset=0,q='-16')['total'] == 1


def test_task_labels_appear_without_separate_creation(app_ctx):
    task = call('task.create',title='Labels', tags=[' #工作 ', '工作', 'a/b', 'Second'])
    tags = call('tag.list',limit=5)['items']
    assert {x['data']['name'] for x in tags} == {'工作', 'a/b', 'Second'}
    assert all(x['task_count'] == 1 for x in tags)
    tag = next(x for x in tags if x['data']['name'] == 'a/b')
    assert call('tag.get',item_id=tag['id'])['data']['name'] == 'a/b'
    call('tag.rename', item_id=tag['id'], name='Renamed')
    assert call('task.get',item_id=task['id'])['data']['tags'] == ['工作','Renamed','Second']
    call('tag.delete',item_id=tag['id'])
    assert 'Renamed' not in call('task.get',item_id=task['id'])['data']['tags']
    assert 'Renamed' not in [x['data']['name'] for x in call('tag.list')['items']]


def test_explicit_tag_does_not_duplicate_embedded_tag(app_ctx):
    call('task.create',title='Task',tags=['Shared'])
    call('tag.create',name='Shared')
    result = call('tag.list',limit=5)
    assert result['total'] == 1
    assert result['items'][0]['task_count'] == 1


@pytest.mark.parametrize('kind', ['group','tag'])
@pytest.mark.parametrize('params', [{'limit':0},{'limit':101},{'offset':-1}])
def test_invalid_catalog_paging_rejected(kind, params, app_ctx):
    with TestClient(build_app()) as client:
        path = '/api/v1/groups' if kind=='group' else '/api/v1/tags'
        assert client.get(path,params=params).status_code == 422


def test_repeat_preview_does_not_create_or_modify(app_ctx):
    result = call('task.repeat_preview',repeat='FREQ=WEEKLY;BYDAY=MO,FR;COUNT=4',
                  due_at='2026-11-09T09:00:00')
    assert result['repeat'].startswith('FREQ=')
    assert result['upcoming']
    assert all(isinstance(value, str) for value in result['upcoming'])
    assert call('task.list')['total'] == 0
    with pytest.raises(QdError):
        call('task.repeat_preview',repeat='FREQ=HOURLY',due_at='2026-11-09')


def test_health_never_deserializes_catalog(app_ctx,monkeypatch):
    call('task.create',title='Stats',tags=['Tag'])
    def forbidden(*args,**kwargs):
        raise AssertionError('health must aggregate in SQL')
    monkeypatch.setattr(app_ctx.store,'snapshot',forbidden)
    monkeypatch.setattr(app_ctx.store,'list_items',forbidden)
    result=call('app.health')
    assert result['tasks']==1 and result['items']==1


def test_views_do_not_load_all_lists(app_ctx,monkeypatch):
    a=call('group.create',name='Visible')['id']
    call('group.create',name='Unrelated')
    call('task.create',title='Show me',list_id=a)
    original=app_ctx.store.list_items
    def guarded(*args,**kwargs):
        assert kwargs.get('kind')!='list','view must only resolve referenced list names'
        return original(*args,**kwargs)
    monkeypatch.setattr(app_ctx.store,'list_items',guarded)
    result=call('view.query',name='all')
    assert result['list_names']=={a:'Visible'}
