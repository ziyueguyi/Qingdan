from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from qingdan.api.http import build_app
from qingdan.core import access, stamp
from qingdan.core.alerting import scheduler
from qingdan.core.registry import QdError, call


def revision(store,item):
    return store.get(item['id']).rev.to_json()


def test_old_notification_cannot_complete_the_next_occurrence(app_ctx):
    item = call('task.create',title='Repeat',due_at='2026-11-13',repeat='daily')
    old = revision(app_ctx.store,item)
    first = call('alert.notification_action',item_id=item['id'],action='complete',expected_rev=old)
    assert first['status']=='completed' and first['rolled']
    assert call('task.get',item_id=item['id'])['data']['due_at']=='2026-11-14'
    before = app_ctx.store.get(item['id']).to_bytes()
    for action in ('complete','snooze'):
        result = call('alert.notification_action',item_id=item['id'],action=action,expected_rev=old)
        assert result['status']=='stale'
        assert app_ctx.store.get(item['id']).to_bytes()==before
    assert call('task.occurrences',item_id=item['id'])['total']==1
    assert call('alert.notification_action',item_id=item['id'],action='open',expected_rev=old)['status']=='open'


def test_concurrent_duplicate_action_advances_once(app_ctx):
    item = call('task.create',title='Repeat',due_at='2026-11-13',repeat='daily')
    rev = revision(app_ctx.store,item)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _:call('alert.notification_action',item_id=item['id'],action='complete',expected_rev=rev),range(4)))
    assert sorted(r['status'] for r in results)==['completed','stale','stale','stale']
    assert call('task.occurrences',item_id=item['id'])['total']==1


def test_edit_or_sync_invalidates_mutation_but_open_finds_current_item(app_ctx):
    item = call('task.create',title='Before')
    rev = revision(app_ctx.store,item)
    call('task.update',item_id=item['id'],title='After')
    assert call('alert.notification_action',item_id=item['id'],action='complete',expected_rev=rev)['status']=='stale'
    assert call('alert.notification_action',item_id=item['id'])['status']=='open'
    assert call('task.get',item_id=item['id'])['data']['title']=='After'


@pytest.mark.parametrize('action',['open','complete','snooze'])
def test_removed_notification_target_is_a_safe_result(app_ctx,action):
    item = call('task.create',title='Removed')
    rev = revision(app_ctx.store,item)
    call('task.delete',item_id=item['id'])
    assert call('alert.notification_action',item_id=item['id'],action=action,expected_rev=rev)['status']=='gone'
    assert call('alert.notification_action',item_id='never-existed',action=action,expected_rev=rev)['status']=='gone'


def test_finished_and_countdown_targets_cannot_be_mutated(app_ctx):
    item = call('task.create',title='Done')
    rev = revision(app_ctx.store,item)
    call('task.complete',item_id=item['id'])
    assert call('alert.notification_action',item_id=item['id'],action='snooze',expected_rev=rev)['status']=='inactive'
    countdown = call('countdown.create',title='Day',target_date='2026-11-14')
    assert call('alert.notification_action',item_id=countdown['id'],action='complete',expected_rev=revision(app_ctx.store,countdown))['status']=='unsupported'


@pytest.mark.parametrize('expected',[None,{}, {'ms':1,'seq':False,'dev':'x'},{'ms':1,'seq':0,'dev':''}])
def test_mutations_require_a_complete_snapshot(app_ctx,expected):
    item = call('task.create',title='Protected')
    with pytest.raises(QdError):
        call('alert.notification_action',item_id=item['id'],action='complete',expected_rev=expected)


def test_notification_actions_are_local_only_even_for_full_remote_token(app_ctx):
    item = call('task.create',title='Protected')
    token = call('access.token_create',name='full',permissions=list(access.PERMISSIONS),features=['*'])
    assert access.policy('alert.notification_action') is None
    with TestClient(build_app(remote=True)) as client:
        response = client.post('/api/v1/alert/notification-action',json={'item_id':item['id'],'action':'open'},headers={'Authorization':'Bearer '+token['token']})
    assert response.status_code==403 and response.json()['error']['code']=='LOCAL_ONLY'


def test_snooze_binds_current_version_is_undoable_and_keeps_due(app_ctx):
    item = call('task.create',title='Later',due_at='2026-11-13')
    rev = revision(app_ctx.store,item)
    result = call('alert.notification_action',item_id=item['id'],action='snooze',expected_rev=rev,minutes=10)
    assert result['status']=='snoozed' and result['revision']!=rev
    data = call('task.get',item_id=item['id'])['data']
    assert data['due_at']=='2026-11-13' and data['snoozed_until']
    assert call('alert.notification_action',item_id=item['id'],action='snooze',expected_rev=rev)['status']=='stale'
    call('undo.last')
    assert not call('task.get',item_id=item['id'])['data'].get('snoozed_until')


def test_snooze_survives_rebuild_at_its_fire_time(app_ctx):
    item = call('task.create',title='Later',due_at='2026-11-01')
    old = app_ctx.store.get(item['id'])
    when = datetime.now()-timedelta(minutes=1)
    from dataclasses import replace
    app_ctx.store.put(replace(old,data={**old.data,'snoozed_until':when.isoformat(timespec='seconds')}))
    current = app_ctx.store.get(item['id'])
    assert scheduler.reminder_times(current)==[int(when.replace(microsecond=0).timestamp()*1000)]
    message = scheduler._to_message({'item_id':item['id'],'fire_at':stamp.now_ms()})
    assert message.revision==current.rev.to_json()
    call('task.complete',item_id=item['id'])
    assert scheduler._to_message({'item_id':item['id'],'fire_at':stamp.now_ms()}) is None


@pytest.mark.parametrize('feature,params',[
    ('task.update',{'due_at':'2026-11-14'}),
    ('task.set_due',{'due_at':'2026-11-14'}),
    ('task.set_repeat',{'repeat':'daily'}),
    ('task.set_reminder',{'remind_at':['2026-11-14T09:00:00']})])
def test_explicit_reschedule_clears_the_old_snooze(app_ctx,feature,params):
    item = call('task.create',title='Rescheduled',due_at='2026-11-13')
    call('task.snooze',item_id=item['id'],minutes=10)
    result = call(feature,item_id=item['id'],**params)
    assert 'snoozed_until' not in result['data']


def test_other_connection_cannot_change_the_occurrence_between_check_and_mutation(app_ctx,monkeypatch):
    import threading
    from qingdan.core import service
    from qingdan.core.store import Store
    item=call('task.create',title='Concurrent CLI',due_at='2026-11-13',repeat='daily')
    old=app_ctx.store.get(item['id']);expected=old.rev.to_json()
    other=Store(app_ctx.store.path,device_id=app_ctx.store.device_id)
    ready=threading.Event();allowed=threading.Event();started=threading.Event();finished=threading.Event();errors=[];blocked=[]
    def write():
        try:
            db=other.db
            ready.set();assert allowed.wait(3)
            started.set()
            db.execute("UPDATE item SET data=json_set(data,'$.due_at','2026-11-14'),rev_ms=rev_ms+1 WHERE id=?",(item['id'],))
            db.commit()
        except Exception as error:errors.append(error)
        finally:finished.set();other.close()
    original=service.task_complete
    thread=threading.Thread(target=write,daemon=True)
    thread.start();assert ready.wait(2)
    def intercepted(item_id):
        allowed.set();assert started.wait(2)
        blocked.append(not finished.wait(.15))
        return original(item_id)
    monkeypatch.setattr(service,'task_complete',intercepted)
    try:
        result=call('alert.notification_action',item_id=item['id'],action='complete',expected_rev=expected)
        thread.join(2)
        assert finished.is_set() and not errors
        assert blocked==[True]
        assert result['status']=='completed' and result['rolled']
        assert call('task.occurrences',item_id=item['id'])['occurrences'][0]['due_at']=='2026-11-13'
        assert call('task.get',item_id=item['id'])['data']['due_at']=='2026-11-14'
    finally:allowed.set();thread.join(2);other.close()


def test_atomic_write_defers_inner_commits_and_rolls_back(app_ctx):
    store=app_ctx.store
    item=call('task.create',title='Unchanged')
    before=store.get(item['id']).to_bytes()
    with pytest.raises(RuntimeError):
        with store.atomic_write():
            call('task.update',item_id=item['id'],title='Transient')
            assert store.db.in_transaction
            raise RuntimeError('rollback')
    assert not store.db.in_transaction and store.get(item['id']).to_bytes()==before


def test_atomic_write_does_not_commit_a_callers_transaction(app_ctx):
    store=app_ctx.store
    item=call('task.create',title='Original')
    before=store.get(item['id']).to_bytes()
    store.db.execute('BEGIN')
    with store.atomic_write():
        call('task.update',item_id=item['id'],title='Caller pending')
    assert store.db.in_transaction
    store.db.rollback()
    assert store.get(item['id']).to_bytes()==before


def test_notification_missing_calendar_does_not_consume_target_or_inbox(app_ctx):
    from qingdan.core.workdays import CALENDAR_ID
    from qingdan.core.notification_inbox import capture
    from qingdan.core.alerting.base import AlertMessage
    item=call('task.create',title='Unknown year',due_at='2027-12-31',repeat=f'FREQ=DAILY;X-QD-WORKDAY=SKIP;X-QD-CALENDAR={CALENDAR_ID}')
    old=app_ctx.store.get(item['id'])
    capture([AlertMessage(item_id=old.id,kind='task',title='Unknown year',fire_at_ms=1,revision=old.rev.to_json())])
    before=old.to_bytes();token=app_ctx.store.task_change_token()
    with pytest.raises(QdError):call('alert.notification_action',item_id=old.id,action='complete',expected_rev=old.rev.to_json())
    assert app_ctx.store.get(old.id).to_bytes()==before and app_ctx.store.task_change_token()==token
    assert call('alert.notification_list')['total']==1
    assert call('task.occurrences',item_id=old.id)['total']==0
