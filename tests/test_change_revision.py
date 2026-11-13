import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

from qingdan.core.registry import call
from qingdan.core.store import SCHEMA, Store


def test_tokens_are_shared_across_connections_and_metadata_is_separate(app_ctx):
    store = app_ctx.store
    other = Store(store.path, device_id='D-test')
    before = store.change_token()
    assert other.change_token() == before
    task = call('task.create', title='before')
    assert store.change_token() == other.change_token() != before
    items_revision = store.task_change_token()
    assert other.task_change_token() == items_revision
    before_pref = store.change_token()
    other.pref_set('sort', 'title')
    assert store.change_token() == other.change_token()
    assert store.change_token()[1] != before_pref[1]
    assert store.task_change_token() == items_revision
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert all(token == store.change_token() for token in pool.map(lambda _: store.change_token(), range(20)))
    # An older client knows no Python revision API, but its SQL still fires the native triggers.
    writer = sqlite3.connect(store.path)
    writer.execute("UPDATE item SET data=json_set(data,'$.title',?) WHERE id=?", ['old client edit', task['id']])
    writer.commit()
    assert store.task_change_token() == other.task_change_token() == items_revision + 1
    writer.close(); other.db.close()


def test_existing_database_migrates_without_rewriting_items_and_rollbacks_do_not_count(tmp_path):
    path = tmp_path / 'legacy.db'
    db = sqlite3.connect(path)
    db.executescript(SCHEMA)
    data = json.dumps({'title':'legacy', 'status':'todo'})
    db.execute("INSERT INTO item(id,kind,rev_ms,rev_seq,rev_dev,data,updated_at) VALUES('old','task',1,0,'old',?,1)", [data])
    db.commit(); db.close()
    store = Store(path)
    assert store.get('old').data['title'] == 'legacy'
    revision = store.task_change_token()
    store.db.execute("UPDATE item SET data=json_set(data,'$.title','rolled back') WHERE id='old'")
    store.db.rollback()
    assert store.task_change_token() == revision
    assert store.get('old').data['title'] == 'legacy'
    store.db.close()


def test_page_rows_total_and_revision_share_a_snapshot_during_external_write(app_ctx):
    task = call('task.create', title='before')
    store = app_ctx.store
    revision = store.task_change_token()
    writer = sqlite3.connect(store.path)
    fired = []
    def interleave(statement):
        if statement.startswith('SELECT i.*') and not fired:
            fired.append(True)
            writer.execute("UPDATE item SET data=json_set(data,'$.title','after') WHERE id=?", [task['id']])
            writer.commit()
    store.db.set_trace_callback(interleave)
    try:
        result = call('view.query', name='all', limit=50)
    finally:
        store.db.set_trace_callback(None)
        writer.close()
    assert fired
    assert result['total'] == 1
    assert result['items'][0]['data']['title'] == 'before'
    assert result['change_token'] == revision
    assert store.task_change_token() == revision + 1
    assert call('view.query', name='all', limit=50)['items'][0]['data']['title'] == 'after'


def test_page_read_preserves_a_callers_uncommitted_transaction(app_ctx):
    from qingdan.core import task_query
    task = call('task.create', title='before')
    store = app_ctx.store
    writer = sqlite3.connect(store.path)
    store.db.execute("UPDATE item SET data=json_set(data,'$.title','pending') WHERE id=?", [task['id']])
    try:
        items, _ = task_query.select(store, ["i.kind='task'"], [], limit=1)
        assert items[0].data['title'] == 'pending'
        assert store.db.in_transaction
        assert json.loads(writer.execute('SELECT data FROM item WHERE id=?', [task['id']]).fetchone()[0])['title'] == 'before'
    finally:
        store.db.rollback()
        writer.close()
    assert store.get(task['id']).data['title'] == 'before'


def test_filter_definition_and_results_use_the_same_revision(app_ctx):
    call('task.create', title='matches')
    filter_ = call('filter.create', name='filter', criteria={'q':'matches'})
    store = app_ctx.store
    revision = store.task_change_token()
    writer = sqlite3.connect(store.path)
    fired = []
    def interleave(statement):
        if statement.startswith('SELECT COUNT(*) FROM item i') and not fired:
            fired.append(True)
            writer.execute("UPDATE item SET data=json_set(data,'$.criteria.q','not found') WHERE id=?", [filter_['id']])
            writer.commit()
    store.db.set_trace_callback(interleave)
    try:
        result = call('filter.tasks', item_id=filter_['id'], limit=50)
    finally:
        store.db.set_trace_callback(None); writer.close()
    assert fired and result['total'] == 1
    assert result['change_token'] == revision
    assert call('filter.tasks', item_id=filter_['id'], limit=50)['total'] == 0


def test_group_names_share_the_task_page_snapshot(app_ctx):
    group = call('group.create', name='before')
    call('task.create', title='task', list_id=group['id'])
    store = app_ctx.store
    revision = store.task_change_token()
    writer = sqlite3.connect(store.path)
    fired = []
    def interleave(statement):
        if statement.startswith("SELECT id,json_extract(data,'$.name')") and not fired:
            fired.append(True)
            writer.execute("UPDATE item SET data=json_set(data,'$.name','after') WHERE id=?", [group['id']])
            writer.commit()
    store.db.set_trace_callback(interleave)
    try:
        result = call('view.query', name='all', group='group', limit=50)
    finally:
        store.db.set_trace_callback(None); writer.close()
    assert fired and result['list_names'][group['id']] == 'before'
    assert result['change_token'] == revision
    assert call('view.query', name='all', group='group', limit=50)['list_names'][group['id']] == 'after'
