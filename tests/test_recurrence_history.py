import calendar
from dataclasses import replace
from datetime import date,datetime,timedelta

import pytest

from qingdan.core import rrule
from qingdan.core.registry import call


@pytest.mark.parametrize('rule,anchor,due,expected',[
    ('daily','1900-01-01','2026-11-13','2026-11-14'),
    ('weekly','1900-01-01','2026-11-09','2026-11-16'),
    ('monthly','1500-01-31','2026-11-30','2026-12-31'),
    ('yearly','0004-02-29','9000-02-28','9001-02-28'),
])
def test_next_and_preview_seek_beyond_five_thousand_historical_cycles(rule,anchor,due,expected):
    assert rrule.next_after(due,rule,anchor).date().isoformat()==expected
    preview=rrule.upcoming(due,rule,3,anchor)
    assert preview[0].date().isoformat()==expected and len(preview)==3
    assert all(a<b for a,b in zip(preview,preview[1:]))


def test_weekday_intervals_seek_without_losing_alignment():
    anchor=date(1800,1,1);due=date(2026,11,13)
    monday=anchor-timedelta(days=anchor.weekday())
    expected=[due+timedelta(days=i) for i in range(1,50)
              if (due+timedelta(days=i)).weekday() in (0,2,6)
              and ((due+timedelta(days=i)-monday).days//7)%2==0][:5]
    actual=rrule.upcoming(due,'FREQ=WEEKLY;INTERVAL=2;BYDAY=MO,WE,SU',5,anchor)
    assert [d.date() for d in actual]==expected


@pytest.mark.parametrize('interval',[1,2,7,12,37])
def test_monthly_count_rank_matches_independent_gregorian_enumeration(interval):
    anchor=date(1500,1,31);due=date(2026,11,13)
    candidates=[]
    for year in range(anchor.year,2028):
        for month in range(1,13):
            if ((year-anchor.year)*12+month-anchor.month)%interval:continue
            length=calendar.monthrange(year,month)[1]
            for day in sorted({1,28,29,30,31,length}):
                if day>length:continue
                value=date(year,month,day)
                if value>=anchor:candidates.append(value)
    past=[d for d in candidates if d<=due]
    future=[d for d in candidates if d>due]
    text=f'FREQ=MONTHLY;INTERVAL={interval};BYMONTHDAY=1,28,29,30,31,-1;COUNT={len(past)+1}'
    assert [d.date() for d in rrule.upcoming(due,text,5,anchor)]==future[:1]
    assert rrule.next_after(future[0],text,anchor) is None
    until=text.split(';COUNT=')[0]+f';UNTIL={future[0].isoformat()}'
    assert [d.date() for d in rrule.upcoming(due,until,5,anchor)]==future[:1]


def test_exact_daily_count_boundary_is_not_an_iteration_budget():
    anchor=date(1900,1,1);due=date(2026,11,13)
    rank=(due-anchor).days+1
    assert rrule.next_after(due,f'FREQ=DAILY;COUNT={rank}',anchor) is None
    assert rrule.next_after(due,f'FREQ=DAILY;COUNT={rank+1}',anchor).date()==due+timedelta(days=1)


@pytest.mark.parametrize('interval',[1,2,4,100])
def test_yearly_leap_month_count_over_centuries(interval):
    anchor=date(4,2,29);due=date(9000,2,28)
    candidates=[]
    for year in range(anchor.year,9999,interval):
        length=calendar.monthrange(year,2)[1]
        for day in sorted({28,29,length}):
            if day<=length and date(year,2,day)>=anchor:candidates.append(date(year,2,day))
    past=[d for d in candidates if d<=due];future=[d for d in candidates if d>due]
    text=f'FREQ=YEARLY;INTERVAL={interval};BYMONTHDAY=28,29,-1;COUNT={len(past)+1}'
    assert [d.date() for d in rrule.upcoming(due,text,5,anchor)]==future[:1]
    assert rrule.next_after(future[0],text,anchor) is None


@pytest.mark.parametrize('text',['daily','weekly','monthly','yearly','FREQ=WEEKLY;BYDAY=MO,FR','FREQ=MONTHLY;BYMONTHDAY=31'])
def test_date_domain_end_is_clean_and_bounded(text):
    assert rrule.next_after('9999-12-31',text,'2026-01-01') is None
    assert rrule.upcoming('9999-12-30',text,5,'2026-01-01')==(
        [datetime(9999,12,31)] if text in ('daily','FREQ=WEEKLY;BYDAY=MO,FR','FREQ=MONTHLY;BYMONTHDAY=31') else [])


def test_imported_long_history_completes_once_then_honors_count(app_ctx):
    due=date(2026,11,13);anchor=date(1900,1,1)
    count=(due-anchor).days+2
    item=call('task.create',title='Long lived',due_at=str(due),repeat=f'FREQ=DAILY;COUNT={count}')
    old=app_ctx.store.get(item['id'])
    app_ctx.store.put(replace(old,data={**old.data,'repeat_start':str(anchor)}))
    first=call('task.complete',item_id=old.id)
    assert first['repeat_roll']['rolled'] and first['data']['due_at']=='2026-11-14'
    assert first['data']['repeat_start']=='1900-01-01'
    second=call('task.complete',item_id=old.id)
    assert second['repeat_roll']['repeat_ended'] and second['data']['status']=='done'
    assert [row['due_at'] for row in call('task.occurrences',item_id=old.id)['occurrences']]==['2026-11-14','2026-11-13']


def test_short_monthly_history_does_not_scan_four_centuries_for_each_task(monkeypatch):
    from qingdan.core.recurrence_window import _month_prefix
    _month_prefix.cache_clear()
    original=rrule._month_days
    calls=[]
    def month_days(*args):
        calls.append(1);return original(*args)
    monkeypatch.setattr(rrule,'_month_days',month_days)
    for count in range(1000,1100):
        rule=f'FREQ=MONTHLY;BYMONTHDAY=31,-1;COUNT={count}'
        assert rrule.next_after('2026-12-01',rule,'2026-01-31').date()==date(2026,12,31)
    assert len(calls)<2000
