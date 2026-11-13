from datetime import date, timedelta

import pytest

from qingdan.core import recurrence_window, rrule, view
from qingdan.core.registry import call, QdError
from test_task_paging import seed


@pytest.mark.parametrize('text', [
    'daily','weekly','monthly','yearly',
    'FREQ=DAILY;INTERVAL=3;COUNT=100',
    'FREQ=WEEKLY;INTERVAL=2;BYDAY=MO,WE,SU;COUNT=40',
    'FREQ=MONTHLY;BYMONTHDAY=1,28,29,30,31,-1;COUNT=180',
    'FREQ=YEARLY;BYMONTHDAY=1,28,29,-1;COUNT=20',
    'FREQ=MONTHLY;INTERVAL=7;BYMONTHDAY=31,-1;COUNT=80'])
@pytest.mark.parametrize('anchor', [date(2024,2,29),date(2025,1,31),date(2026,11,12)])
def test_seek_matches_full_sequence(text,anchor):
    rule = rrule.parse(text)
    for distance in [0,20,370,3000]:
        start = anchor+timedelta(days=distance)
        end = start+timedelta(days=100)
        expected = [d for d in rrule.occurrences(anchor,rule) if max(start,anchor+timedelta(days=1))<=d<=end]
        assert recurrence_window.dates(anchor,rule,anchor,start,end) == expected


def test_seek_distant_history_and_count():
    anchor, start, end = date(1900,1,1),date(2026,11,1),date(2026,11,30)
    assert len(recurrence_window.dates(anchor,rrule.parse('daily'),anchor,start,end)) == 30
    assert recurrence_window.dates(anchor,rrule.parse('FREQ=DAILY;COUNT=5000'),anchor,start,end) == []
    # 31 and -1 collide in long months; Gregorian COUNT prefix must deduplicate.
    rule = rrule.parse('FREQ=MONTHLY;BYMONTHDAY=31,-1;COUNT=1523')
    assert recurrence_window.dates(anchor,rule,anchor,start,end) == [date(2026,11,30)]
    rule = rrule.parse('FREQ=MONTHLY;BYMONTHDAY=31,-1;COUNT=1522')
    assert recurrence_window.dates(anchor,rule,anchor,start,end) == []


def test_calendar_pages_and_counts_match_pure_real_events(app_ctx):
    pool = seed(app_ctx.store,203)
    start, today = date(2026,11,1),date(2026,11,13)
    expected = view.calendar_days(pool,start,42,today,expand=False)
    result = call('view.calendar',start=str(start),today=str(today),limit=3)
    assert [c['count'] for c in result['cells']] == [c['count'] for c in expected]
    for cell,oracle in zip(result['cells'],expected):
        assert cell['events'] == oracle['events'][:3]
        pages = [call('view.calendar',start=cell['date'],days=1,today=str(today),limit=7,offset=offset)
                 for offset in range(0,max(1,cell['count']),7)]
        assert [e for p in pages for e in p['items']] == oracle['events']
        assert all(p['change_token']==result['change_token'] for p in pages)


def test_dense_recurrences_are_counted_and_fully_pageable(app_ctx,monkeypatch):
    seed(app_ctx.store,10_000)
    app_ctx.store.db.execute("UPDATE item SET deleted=0,data=json_set(data,'$.status','open','$.due_at','1900-01-01','$.repeat','daily')")
    app_ctx.store.db.commit()
    decoded = []
    original = app_ctx.store._row_to_item
    def track(row):
        decoded.append(row['id']); return original(row)
    monkeypatch.setattr(app_ctx.store,'_row_to_item',track)
    month = call('view.calendar',start='2026-11-01',today='2026-11-01',limit=3)
    assert all(c['count']==10_000 and c['recurring']==10_000 and len(c['events'])==3 for c in month['cells'])
    assert len(decoded)==3  # shared recurrence instances decode each Item once
    decoded.clear()
    page = call('view.calendar',start='2026-11-01',days=1,today='2026-11-01',limit=50,offset=9950)
    assert page['total']==10_000 and len(page['items'])==50 and not page['has_more']
    assert len(decoded)==50 and page['items'][0]['id']=='T009950'


def test_mixed_real_and_virtual_pages_and_scope(app_ctx):
    for i in range(11):
        call('task.create',title=f'virtual{i}',due_at='2026-11-01',repeat='daily',tags=['x'])
    for i in range(8):
        call('task.create',title=f'real{i}',due_at='2026-11-13',tags=['x'])
    call('task.create',title='other',due_at='2026-11-13')
    full = call('view.calendar',start='2026-11-13',days=1,today='2026-11-13',tag='x')
    pages = [call('view.calendar',start='2026-11-13',days=1,today='2026-11-13',tag='x',limit=5,offset=n) for n in range(0,20,5)]
    assert full['total']==19
    assert [e for p in pages for e in p['items']]==full['items']
    assert [e['occurrence'] for e in full['items']]==[False]*8+[True]*11


def test_calendar_rejects_invalid_page(app_ctx):
    with pytest.raises(QdError):
        call('view.calendar',limit=3,offset=3)


def test_workday_window_warns_and_preserves_known_dates(app_ctx):
    from qingdan.core.workdays import CALENDAR_ID
    rule = f'FREQ=DAILY;X-QD-WORKDAY=NEXT;X-QD-CALENDAR={CALENDAR_ID}'
    call('task.create',title='workday',due_at='2027-12-30',repeat=rule)
    result = call('view.calendar',start='2027-12-30',days=5,today='2027-12-30',limit=3)
    assert result['cells'][1]['count']==1
    assert result['warnings']==[{'key':'calendar_missing_year','calendar_id':CALENDAR_ID,'year':2028}]


def test_countdown_filter_and_deleted_scopes(app_ctx):
    tagged = call('task.create',title='tagged',due_at='2026-11-13',tags=['x'])
    call('task.create',title='other',due_at='2026-11-13')
    call('countdown.create',title='day',target_date='2026-11-13')
    filter_ = call('filter.create',name='tagged',criteria={'tag':'x'})
    result = call('view.calendar',start='2026-11-13',days=1,filter_id=filter_['id'],limit=3)
    assert [e['id'] for e in result['items']]==[tagged['id']]
    full = call('view.calendar',start='2026-11-13',days=1,limit=3)
    assert full['total']==3 and any(e['kind']=='countdown' for e in full['items'])
    call('task.delete',item_id=tagged['id'])
    assert call('view.calendar',start='2026-11-13',days=1,limit=3)['total']==2
    trash = call('view.calendar',start='2026-11-13',days=1,limit=3,name='trash')
    assert [e['id'] for e in trash['items']]==[tagged['id']]
