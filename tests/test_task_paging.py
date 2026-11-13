import json
from datetime import date

import pytest

from qingdan.core import view
from qingdan.core.clock import Rev
from qingdan.core.models import Item
from qingdan.core.registry import QdError, call

TODAY = date(2026, 11, 13)


def seed(store, count=97):
    rows, items = [], []
    for i in range(count):
        data = {'title': ['周报', 'İstanbul', 'needle', 'a', 'Z'][i % 5] + str(i),
                'note': None if i % 13 == 0 else 'needle note' if i % 3 == 0 else '',
                'due_at': [None, '', '2026-11-12', '2026-11-13T23:30:00+08:00',
                           '2026-11-14', '2026-11-19', '2026-12-01', 'invalid'][i % 8],
                'created_at': f'2026-11-{1 + i % 9:02}T09:00:00',
                'done_at': '2026-11-13T12:00:00', 'priority': 1 + i % 4,
                'order': i % 7, 'status': ['todo', 'doing', 'done', 'cancelled'][i % 4],
                'list_id': [None, 'L1', 'L2'][i % 3],
                'tags': [['周报', 'other'], [], ['tag']][i % 3]}
        item = Item(kind='task', id=f'T{i:06}', rev=Rev(1000 + i % 5, i % 3, 'd' + str(i % 2)),
                    deleted=i % 11 == 0, deleted_at_ms=1000 + i, data=data)
        items.append(item)
        rows.append((item.id, item.kind, item.rev.ms, item.rev.seq, item.rev.dev,
                     item.deleted, item.deleted_at_ms, json.dumps(data), 1000))
    store.db.executemany('INSERT INTO item(id,kind,rev_ms,rev_seq,rev_dev,deleted,deleted_at_ms,data,updated_at) '
                         'VALUES(?,?,?,?,?,?,?,?,?)', rows)
    store.db.commit()
    return items


def ids(items):
    return [it['id'] if isinstance(it, dict) else it.id for it in items]


def test_sql_view_pages_match_pure_oracle_for_every_sort_and_view(app_ctx):
    pool = seed(app_ctx.store)
    for name in view.VIEW_NAMES:
        for sort in view.SORT_MODES:
            for reverse in (False, True):
                for include_done in (False, True):
                    expected = view.sort_items(view.apply_view(name, pool, TODAY, include_done), sort, reverse)
                    for offset in (0, 13, 39, 104):
                        result = call('view.query', name=name, today=TODAY.isoformat(), sort=sort,
                                      reverse=reverse, include_done=include_done, limit=13, offset=offset)
                        assert ids(result['items']) == ids(expected[offset:offset + 13]), (name, sort, reverse, include_done, offset)
                        assert result['total'] == len(expected)
                        assert result['has_more'] == (offset + len(result['items']) < len(expected))


def test_page_grouping_and_optional_filters_keep_the_same_items(app_ctx):
    pool = seed(app_ctx.store)
    selected = [it for it in pool if it.data['list_id'] == 'L1']
    expected = view.sort_items(view.apply_view('all', selected, TODAY), 'smart')[5:18]
    for group in view.GROUP_MODES:
        result = call('view.query', name='all', list_id='L1', group=group, today=TODAY.isoformat(), limit=13, offset=5)
        assert ids(result['items']) == ids(expected)
        buckets = view.group_items(expected, group, TODAY)
        assert [(b['key'], ids(b['items'])) for b in result['groups']] == [(b['key'], ids(b['items'])) for b in buckets]
    tagged = call('view.query', name='all', tag='周报', limit=13)
    assert all('周报' in it['data']['tags'] for it in tagged['items'])


def test_custom_filter_sql_matches_pure_predicates(app_ctx):
    pool = seed(app_ctx.store)
    criteria = [{'open_only': False}, {'open_only': True}, {'status': 'done'}, {'list_id': 'L1'}, {'group_id': 'L2'},
                {'no_group': True}, {'tag': '周报'}, {'tags_any': ['tag', 'other']},
                {'priority': 2}, {'priority_max': 2}, {'due_within_days': 7},
                {'due_within_days': 7, 'due_from_days': 1}, {'overdue': True}, {'has_due': True},
                {'q': 'İST'}, {'q': '周报'}, {'q': 'NEEDLE'}, {'q': 'note 周报'}, {'q': 'None'}]
    for crit in criteria:
        filter_ = call('filter.create', name='test', criteria=crit)
        expected = view.sort_items([it for it in pool if view.match_criteria(it, crit, TODAY)], 'title', True)
        result = call('filter.tasks', item_id=filter_['id'], today=TODAY.isoformat(), sort='title', reverse=True, limit=11, offset=3)
        assert result['total'] == len(expected), crit
        assert ids(result['items']) == ids(expected[3:14]), crit


def test_search_counts_and_pages_every_match(app_ctx):
    seed(app_ctx.store)
    expected = app_ctx.store.search_text('needle', kinds=['task'], limit=500)
    pages = [call('search.query', q='needle', kinds=['task'], limit=7, offset=offset)
             for offset in range(0, len(expected), 7)]
    assert ids([item for page in pages for item in page['items']]) == ids(expected)
    assert all(page['total'] == len(expected) for page in pages)


def test_ten_thousand_rows_only_decode_the_page_and_no_items_for_counts(app_ctx, monkeypatch):
    pool = seed(app_ctx.store, 10_000)
    decoded = []
    original = app_ctx.store._row_to_item
    def track(row):
        decoded.append(row['id'])
        return original(row)
    monkeypatch.setattr(app_ctx.store, '_row_to_item', track)
    result = call('view.query', name='all', limit=50, offset=500, today=TODAY.isoformat())
    assert len(result['items']) == len(decoded) == 50
    decoded.clear()
    counts = call('view.counts', today=TODAY.isoformat())
    stats = call('stats.today', today=TODAY.isoformat())
    assert not decoded
    assert counts['all'] == sum(not it.deleted for it in pool)
    assert stats['open_total'] == sum(view.is_open(it) for it in pool)
    for name in view.VIEW_NAMES:
        assert counts[name] == len(view.apply_view(name, pool, TODAY, include_done=name == 'done'))
    assert stats['overdue'] == sum(view.is_open(it) and (view.days_between(it.data['due_at'], TODAY) or 0) < 0 for it in pool)


@pytest.mark.parametrize('params', [{'limit': -1}, {'limit': 501}, {'offset': -1}])
def test_reject_invalid_page_parameters(params):
    with pytest.raises(QdError):
        call('view.query', name='all', **params)
