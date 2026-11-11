"""Regression coverage for list, recurrence and external control workflows."""
import json

import pytest
from fastapi.testclient import TestClient

from qingdan.api.http import build_app
from qingdan.cli import main
from qingdan.core.registry import QdError, call


@pytest.mark.parametrize('body', ['{broken', '[]', 'null', '"value"'])
def test_malformed_body_never_runs_backup(app_ctx, body):
    with TestClient(build_app()) as client:
        response = client.post('/api/v1/backups', content=body,
                               headers={'Content-Type': 'application/json'})
        assert response.status_code == 422
        assert response.json()['error']['code'] == 'VALIDATION_ERROR'
    assert call('backup.list')['total'] == 0


def test_cli_update_accepts_changes(app_ctx, capsys):
    data_dir = str(app_ctx.data_dir)
    task = call('task.create', title='before')
    assert main(['--data-dir', data_dir, 'task', 'update', '--item-id', task['id'],
                 '--changes', json.dumps({'title': 'after', 'repeat': 'weekly',
                                          'due_at': '2026-11-12'})]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['data']['title'] == 'after'
    assert result['data']['repeat'] == 'weekly'


def test_quick_add_in_list(app_ctx):
    group = call('group.create', name='Work')
    task = call('task.quick_add', text='Write report', create=True, list_id=group['id'])
    assert task['created']['data']['list_id'] == group['id']


def test_move_clears_old_section_and_rejects_wrong_section(app_ctx):
    a = call('group.create', name='A')['id']
    b = call('group.create', name='B')['id']
    section = call('section.create', list_id=a, name='A section')['id']
    task = call('task.create', title='Move me', list_id=a, section_id=section)['id']
    with pytest.raises(QdError):
        call('task.move', item_id=task, list_id=b, section_id=section)
    moved = call('task.move', item_id=task, list_id=b)
    assert moved['data']['list_id'] == b
    assert moved['data']['section_id'] is None


def test_move_rejects_parent_cycle(app_ctx):
    parent = call('task.create', title='Parent')['id']
    child = call('task.create', title='Child', parent_id=parent)['id']
    with pytest.raises(QdError):
        call('task.move', item_id=parent, parent_id=child)
    assert call('task.get', item_id=parent)['data']['parent_id'] is None


def test_move_rejects_deleted_destination(app_ctx):
    group = call('group.create', name='Gone')['id']
    call('group.delete', item_id=group)
    task = call('task.create', title='Stay here')['id']
    with pytest.raises(QdError):
        call('task.move', item_id=task, list_id=group)

@pytest.mark.parametrize('changes', [{'title': '  '}, {'due_at': 'not-a-date'},
                                     {'list_id': 'missing'}, {'section_id': 'missing'}])
def test_invalid_update_preserves_original(app_ctx, changes):
    task = call('task.create', title='Original')
    with pytest.raises(QdError):
        call('task.update', item_id=task['id'], **changes)
    assert call('task.get', item_id=task['id'])['rev'] == task['rev']


def test_update_list_clears_section(app_ctx):
    group = call('group.create', name='A')['id']
    section = call('section.create', list_id=group, name='Section')['id']
    task = call('task.create', title='Move via update', list_id=group, section_id=section)
    moved = call('task.update', item_id=task['id'], list_id=None)
    assert moved['data']['section_id'] is None


@pytest.mark.parametrize('feature,params', [
    ('task.list', {'include_deleted': 'nonsense'}),
    ('task.create', {'title': 'Bad tags', 'tags': {}}),
    ('app.pref_set', {'prefs': []}),
])
def test_invalid_argument_types_return_validation_error(app_ctx, feature, params):
    with pytest.raises(QdError) as exc:
        call(feature, **params)
    assert exc.value.code == 'VALIDATION_ERROR'


def test_calendar_filters_by_list(app_ctx):
    a = call('group.create', name='A')['id']
    b = call('group.create', name='B')['id']
    task = call('task.create', title='A task', list_id=a, due_at='2026-11-12')
    call('task.create', title='B task', list_id=b, due_at='2026-11-12')
    result = call('view.calendar', start='2026-11-01', days=42, list_id=a)
    events = [e for c in result['cells'] for e in c['events']]
    assert [e['id'] for e in events] == [task['id']]


def test_cli_file_output(app_ctx,tmp_path):
    from qingdan.cli import main
    import json
    result = tmp_path/'result.json'
    assert main(['--output',str(result),'--data-dir',str(app_ctx.data_dir),
                 'task','create','--title','中文 CLI']) == 0
    assert json.loads(result.read_text(encoding='utf-8'))['data']['title'] == '中文 CLI'
    assert main(['--output',str(result),'--help']) == 0
    assert 'qingdan' in result.read_text(encoding='utf-8')
    assert main(['--output',str(result),'--data-dir',str(app_ctx.data_dir),
                 'task','create','--title','']) == 2
    assert json.loads(result.read_text(encoding='utf-8'))['error']['code'] == 'VALIDATION_ERROR'
