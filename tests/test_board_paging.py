import json

import pytest

from qingdan.core import view
from qingdan.core.registry import QdError, call
from test_task_paging import TODAY, seed


@pytest.mark.parametrize('by', ['status', 'priority', 'group'])
def test_board_column_card_pages_match_the_pure_board(by, app_ctx):
    pool = [item for item in seed(app_ctx.store, 203) if not item.deleted]
    expected = view.board_columns(pool, by, today=TODAY)
    result = call('view.board', group_by=by, today=TODAY.isoformat(), limit=7, columns_limit=5)
    assert result['total'] == len(pool)
    assert [(c['key'],c['total']) for c in result['columns']] == [(c['key'],c['total']) for c in expected]
    for column in expected:
        pages = [call('view.board', group_by=by, column=column['key'], today=TODAY.isoformat(), limit=7, offset=offset)
                 for offset in range(0, max(1,column['total']), 7)]
        assert [item['id'] for page in pages for item in page['items']] == [item['id'] for item in column['items']]
        assert all(page['total'] == column['total'] for page in pages)
        assert all(len(page['items'])<=7 for page in pages)


def test_ten_thousand_tasks_and_many_columns_stay_bounded(app_ctx, monkeypatch):
    store = app_ctx.store
    seed(store, 10_000)
    store.db.execute("UPDATE item SET data=json_set(data,'$.list_id','G'||(CAST(substr(id,2) AS INTEGER)%40)) WHERE kind='task'")
    store.db.executemany("INSERT INTO item(id,kind,rev_ms,rev_seq,rev_dev,data,updated_at) VALUES(?,'list',1,0,'d',?,1)",
                         [(f'G{i}',json.dumps({'name':f'Column {i}'})) for i in range(40)])
    store.db.commit()
    decoded = []
    original = store._row_to_item
    def track(row):
        decoded.append(row['id']); return original(row)
    monkeypatch.setattr(store, '_row_to_item', track)
    seen = []
    for offset in range(0,40,5):
        decoded.clear()
        result = call('view.board', group_by='group', limit=20, columns_limit=5, columns_offset=offset)
        assert len(result['columns']) == 5
        assert result['columns_total'] == 40
        assert len(decoded) == len(result['items']) == 100
        assert len(result['list_names']) == 5
        assert result['columns_has_more'] == (offset<35)
        seen.extend(c['key'] for c in result['columns'])
    assert len(set(seen)) == len(seen) == 40


def test_board_respects_views_tags_custom_filters_and_sort(app_ctx):
    pool = seed(app_ctx.store)
    filter_ = call('filter.create', name='needle', criteria={'q':'needle'})
    selected = [it for it in pool if view.match_criteria(it, {'q':'needle'}, TODAY) and '周报' in it.data['tags']]
    expected = view.sort_items(view.apply_view('next7', selected, TODAY), 'title', True)
    assert expected
    result = call('view.board', group_by='group', name='next7', tag='周报', filter_id=filter_['id'],
                  sort='title', reverse=True, today=TODAY.isoformat(), limit=500, columns_limit=5)
    assert result['total'] == len(expected)
    assert {it['id'] for it in result['items']} == {it.id for it in expected}
    for column in result['columns']:
        members = [it for it in expected if (it.data['list_id'] or 'inbox')==column['key']]
        assert [it['id'] for it in column['items']] == [it.id for it in members]


@pytest.mark.parametrize('params', [{'limit':-1}, {'columns_limit':21}, {'columns_offset':-1},
                                  {'group_by':'bad'}, {'column':'bad'}, {'sort':'bad'}])
def test_board_rejects_invalid_page_parameters(params):
    with pytest.raises(QdError):
        call('view.board', **params)
