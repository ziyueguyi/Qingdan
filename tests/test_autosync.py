from datetime import datetime
import pytest
from qingdan.core import autosync
from qingdan.core.registry import call,QdError
from test_m4 import bare,logged_in_only,wired  # noqa: F401


def test_daily_schedule_wraps_and_sorts(app_ctx):
    cfg={'mode':'daily','times':['18:00','09:00']}
    assert autosync.next_time(cfg,datetime(2026,11,11,10))==datetime(2026,11,11,18)
    assert autosync.next_time(cfg,datetime(2026,11,11,19))==datetime(2026,11,12,9)


def test_disabled_and_not_due_do_not_sync(app_ctx,monkeypatch):
    monkeypatch.setattr(autosync,'call',lambda *a,**k:pytest.fail('must not invoke sync'))
    assert autosync.tick()['reason']=='disabled'
    autosync.config_set(enabled=True,interval_minutes=30)
    assert autosync.tick()['reason']=='not_due'


@pytest.mark.parametrize('raw',[{'mode':'bad'},{'times':['25:00']},{'times':[]},{'interval_minutes':0}])
def test_bad_schedule_preserves_existing_config(app_ctx,raw):
    before=call('autosync.config_get')
    with pytest.raises(QdError):call('autosync.config_set',**raw)
    assert call('autosync.config_get')==before


def test_due_sync_records_failure_and_keeps_pending_data(app_ctx):
    call('task.create',title='Offline change')
    call('autosync.config_set',enabled=True)
    app_ctx.store.state_set(autosync.STATUS,{'next_at':1})
    result=call('autosync.tick')
    assert result['error_code']=='AUTH_REQUIRED'
    assert app_ctx.store.dirty_count()>0
    status=call('autosync.config_get')['status']
    assert status['paused'] and status['next_at'] is None
    assert call('autosync.tick')['reason']=='action_required'


def test_scheduled_sync_pushes_every_incremental_kind(wired):
    group=call('group.create',name='List')
    folder=call('folder.create',name='Folder')
    section=call('section.create',name='Section',list_id=group['id'])
    task=call('task.create',title='Task',list_id=group['id'],section_id=section['id'])
    call('tag.create',name='Tag');call('filter.create',name='Filter',criteria={'overdue':True})
    call('note.append',item_id=task['id'],text='Note')
    call('countdown.create',title='Countdown',target_date='2027-01-01')
    call('autosync.config_set',enabled=True,interval_minutes=20)
    wired.store.state_set(autosync.STATUS,{'next_at':1})
    result=call('autosync.tick')
    assert result['ok'] is True and wired.store.dirty_count()==0
    assert call('autosync.config_get')['status']['last_success_at']
    assert call('autosync.tick')['reason']=='not_due'


def test_network_failures_back_off_and_new_credentials_resume_auth_failure(app_ctx, monkeypatch):
    def unavailable(*args, **kwargs):raise QdError('NETWORK','offline',503)
    monkeypatch.setattr(autosync,'call',unavailable)
    call('autosync.config_set',enabled=True)
    for failures in (1,2):
        status=app_ctx.store.state_get(autosync.STATUS);status['next_at']=1
        app_ctx.store.state_set(autosync.STATUS,status)
        assert call('autosync.tick')['retryable']
        status=call('autosync.config_get')['status']
        assert status['consecutive_failures']==failures and not status['paused']
        assert status['next_at']-status['last_finished_at']==(2**(failures-1))*60000
    def unauthenticated(*args, **kwargs):raise QdError('AUTH_REQUIRED','login',401)
    monkeypatch.setattr(autosync,'call',unauthenticated)
    status['next_at']=1;app_ctx.store.state_set(autosync.STATUS,status)
    assert call('autosync.tick')['retryable'] is False
    call('auth.login',token='good-token')
    assert not call('autosync.config_get')['status']['paused']


@pytest.mark.parametrize('enabled',[True,False])
def test_schedule_saved_during_network_is_preserved(app_ctx, monkeypatch, enabled):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    entered,release=threading.Event(),threading.Event()
    def slow(*args,**kwargs):
        entered.set();assert release.wait(10)
        raise QdError('NETWORK','offline',503)
    monkeypatch.setattr(autosync,'call',slow)
    call('autosync.config_set',enabled=True)
    app_ctx.store.state_set(autosync.STATUS,{'next_at':1})
    def run_tick():
        try:return call('autosync.tick')
        finally:app_ctx.store.close()
    with ThreadPoolExecutor(max_workers=1) as pool:
        tick=pool.submit(run_tick)
        try:
            assert entered.wait(5)
            assert call('autosync.tick')['reason']=='running'
            saved=call('autosync.config_set',enabled=enabled,mode='daily',times=['10:30'])
        finally:release.set()
        assert tick.result(timeout=10)['ok'] is False
    actual=call('autosync.config_get')
    assert actual['config']==saved['config']
    assert actual['status']['next_at']==saved['status']['next_at']
    assert not actual['status']['paused']


def test_unfinished_attempt_from_previous_process_is_retried(app_ctx, monkeypatch):
    monkeypatch.setattr(autosync,'call',lambda *args,**kwargs:{'ok':True})
    call('autosync.config_set',enabled=True,interval_minutes=60)
    status=app_ctx.store.state_get(autosync.STATUS)
    status['attempt_running']=True
    app_ctx.store.state_set(autosync.STATUS,status)
    assert call('autosync.tick')['ok']
    assert not call('autosync.config_get')['status']['attempt_running']
