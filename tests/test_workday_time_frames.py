from datetime import datetime,timedelta,timezone

import pytest

from qingdan.core import rrule,service,workdays
from qingdan.core.registry import call,QdError


def local_parser(zone):
    def parse(value):
        try:
            result=value if isinstance(value,datetime) else datetime.fromisoformat(str(value).replace('Z','+00:00'))
            return result.astimezone(zone).replace(tzinfo=None) if result.tzinfo else result
        except (ValueError,TypeError):return None
    return parse


@pytest.mark.parametrize('origin',[timezone.utc,timezone(timedelta(hours=8))])
@pytest.mark.parametrize('policy',['SKIP','NEXT'])
def test_workday_count_and_reminders_match_after_sync_to_another_time_zone(app_ctx,monkeypatch,origin,policy):
    rule=f'FREQ=DAILY;COUNT=3;X-QD-WORKDAY={policy};X-QD-CALENDAR={workdays.CALENDAR_ID}'
    with monkeypatch.context() as m:
        m.setattr(service,'parse_dt',local_parser(origin));m.setattr(rrule,'_as_dt',local_parser(origin))
        items=[call('task.create',title='Across devices',due_at='2026-02-15T00:30:00+08:00',repeat=rule) for _ in range(2)]
        for item in items:
            effective=datetime.fromisoformat(item['data']['due_at']).replace(tzinfo=origin)
            reminder=(effective-timedelta(minutes=30)).astimezone(timezone(timedelta(hours=8))).isoformat()
            call('task.set_reminder',item_id=item['id'],remind_at=[reminder])
    states=[[],[]]
    for index,zone in enumerate((origin,timezone(timedelta(hours=8)) if origin==timezone.utc else timezone.utc)):
        with monkeypatch.context() as m:
            m.setattr(service,'parse_dt',local_parser(zone));m.setattr(rrule,'_as_dt',local_parser(zone))
            for _ in range(3):
                result=call('task.complete',item_id=items[index]['id'])
                due=local_parser(zone)(result['data']['due_at'])
                reminder=local_parser(zone)(result['data']['remind_at'][0])
                assert due-reminder==timedelta(minutes=30)
                states[index].append({key:result['data'].get(key) for key in ('due_at','repeat_start','remind_at','status')})
    assert states[0]==states[1]
    assert states[0][-1]['status']=='done'
    assert all(call('task.occurrences',item_id=item['id'])['total']==3 for item in items)


@pytest.mark.parametrize('reminder',['2026-11-13T09:30:00','2026-11-13 09:30:00'])
def test_all_day_repeat_keeps_an_explicit_reminder_time(app_ctx,reminder):
    item=call('task.create',title='All day',due_at='2026-11-13',repeat='daily')
    call('task.set_reminder',item_id=item['id'],remind_at=[reminder])
    result=call('task.complete',item_id=item['id'])
    assert result['data']['due_at']=='2026-11-14'
    assert result['data']['remind_at']==['2026-11-14T09:30:00']


def test_repeat_reminder_keeps_its_explicit_offset(app_ctx):
    item=call('task.create',title='Offset reminder',due_at='2026-11-13T10:00:00',repeat='daily')
    call('task.set_reminder',item_id=item['id'],remind_at=['2026-11-13T09:00:00+08:00'])
    result=call('task.complete',item_id=item['id'])
    assert result['data']['remind_at']==['2026-11-14T09:00:00+08:00']


@pytest.mark.parametrize('value',['2026-11-13T09:00:00',False,['invalid']])
def test_invalid_workday_reminder_update_preserves_the_task(app_ctx,value):
    rule=f'FREQ=DAILY;X-QD-WORKDAY=NEXT;X-QD-CALENDAR={workdays.CALENDAR_ID}'
    task=call('task.create',title='Protected',due_at='2026-11-13',repeat=rule)
    before=app_ctx.store.get(task['id']).to_bytes()
    with pytest.raises(QdError):call('task.update',item_id=task['id'],remind_at=value)
    assert app_ctx.store.get(task['id']).to_bytes()==before
