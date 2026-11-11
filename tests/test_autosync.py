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
    assert call('autosync.config_get')['status']['next_at']>1


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
