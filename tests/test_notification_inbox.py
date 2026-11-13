import json
from dataclasses import replace
import pytest

from fastapi.testclient import TestClient

from qingdan.api.http import build_app
from qingdan.core import access, notification_inbox as inbox
from qingdan.core.alerting.base import AlertMessage
from qingdan.core.clock import Rev
from qingdan.core.registry import call
from qingdan.core.store import Store
from qingdan.shell.notifications import NotificationHost,notification_icon
from test_task_paging import seed


def message(item):
    return AlertMessage(item_id=item.id,kind=item.kind,title=item.data.get('title',''),
                        fire_at_ms=1000,revision=item.rev.to_json())


def test_capture_is_local_durable_and_duplicate_capture_does_not_invalidate(app_ctx):
    item=app_ctx.store.get(call('task.create',title='Reminder')['id'])
    before=item.to_bytes();task_token=app_ctx.store.task_change_token()
    assert inbox.capture([message(item)])==1
    version=inbox.token(app_ctx.store)
    assert inbox.capture([message(item)])==1 and inbox.token(app_ctx.store)==version
    assert app_ctx.store.get(item.id).to_bytes()==before
    assert app_ctx.store.task_change_token()==task_token
    other=Store(app_ctx.store.path,device_id=app_ctx.store.device_id)
    try:
        assert inbox.query(other)['items'][0]['expected_rev']==item.rev.to_json()
        assert inbox.token(other)==version
    finally:other.close()


def test_ten_thousand_inbox_targets_only_decode_current_page(app_ctx,monkeypatch):
    items=seed(app_ctx.store,10_000)
    inbox.capture([message(item) for item in items])
    decoded=[];original=app_ctx.store._row_to_item
    def decode(row):decoded.append(row['id']);return original(row)
    monkeypatch.setattr(app_ctx.store,'_row_to_item',decode)
    for offset in (0,5000,9950):
        decoded.clear()
        page=call('alert.notification_list',limit=50,offset=offset)
        assert page['total']==10_000 and len(page['items'])==len(decoded)==50
        assert page['has_more']==(offset<9950)
    assert len(call('alert.notification_list',limit=0)['items'])==50


def test_all_pages_preserve_order_without_duplicates(app_ctx):
    pool=seed(app_ctx.store,117)
    inbox.capture([message(item) for item in pool])
    pages=[call('alert.notification_list',limit=17,offset=n) for n in range(0,117,17)]
    ids=[item['id'] for page in pages for item in page['items']]
    assert ids==sorted(item.id for item in pool)
    assert len(set(ids))==117


def test_stale_dismiss_cannot_remove_a_newer_notification(app_ctx):
    item=app_ctx.store.get(call('task.create',title='Before')['id'])
    inbox.capture([message(item)])
    call('task.update',item_id=item.id,title='After')
    newer=app_ctx.store.get(item.id)
    inbox.capture([message(newer)])
    result=call('alert.notification_action',item_id=item.id,action='dismiss',expected_rev=item.rev.to_json())
    assert result['inbox_total']==1
    page=call('alert.notification_list')
    assert page['items'][0]['title']=='After' and page['items'][0]['status']=='open'
    call('alert.notification_action',item_id=item.id,action='complete',expected_rev=newer.rev.to_json())
    assert call('alert.notification_list')['total']==0


def test_completed_changed_deleted_and_missing_targets_are_explicit(app_ctx):
    items=[app_ctx.store.get(call('task.create',title=str(i))['id']) for i in range(4)]
    inbox.capture([message(item) for item in items])
    call('task.complete',item_id=items[0].id)
    call('task.update',item_id=items[1].id,title='Changed')
    call('task.delete',item_id=items[2].id)
    app_ctx.store.db.execute('DELETE FROM item WHERE id=?',(items[3].id,));app_ctx.store.db.commit()
    rows={item['id']:item for item in call('alert.notification_list')['items']}
    assert [rows[item.id]['status'] for item in items]==['inactive','stale','gone','gone']
    assert rows[items[3].id]['title']==''


def test_item_rows_counts_and_notification_revision_are_one_snapshot(app_ctx,monkeypatch):
    item=app_ctx.store.get(call('task.create',title='Before')['id'])
    inbox.capture([message(item)])
    other=Store(app_ctx.store.path,device_id='writer')
    previous=[app_ctx.store.task_change_token(),inbox.token(app_ctx.store)]
    original=app_ctx.store._row_to_item
    def decode(row):
        changed=replace(item,rev=Rev(item.rev.ms+1,0,'writer'),data={**item.data,'title':'After'})
        other.put(changed)
        inbox.capture([message(changed)],other)
        return original(row)
    monkeypatch.setattr(app_ctx.store,'_row_to_item',decode)
    try:
        first=call('alert.notification_list')
        assert first['change_token']==previous and first['items'][0]['title']=='Before'
        monkeypatch.setattr(app_ctx.store,'_row_to_item',original)
        second=call('alert.notification_list')
        assert second['change_token']!=previous and second['items'][0]['title']=='After'
    finally:other.close()


def test_inbox_is_not_exposed_to_remote_full_permission_token(app_ctx):
    token=call('access.token_create',name='full',permissions=list(access.PERMISSIONS),features=['*'])
    with TestClient(build_app(remote=True)) as client:
        reply=client.get('/api/v1/alert/notifications',headers={'Authorization':'Bearer '+token['token']})
    assert reply.status_code==403 and reply.json()['error']['code']=='LOCAL_ONLY'


def test_windows_balloon_click_queues_navigation_once_and_preserves_other_events():
    events=[]
    class Base:
        def _on_notify(self,wparam,lparam):events.append((wparam,lparam))
    class Window:
        def show(self):events.append('show')
        def restore(self):events.append('restore')
    host=NotificationHost();host._window=Window()
    icon=notification_icon(Base,host._open)()
    icon._on_notify(1,0x12340405)
    assert json.loads(host.takeNotification())=={'inbox':True}
    assert host.takeNotification() is None
    assert events==['show','restore']
    icon._on_notify(2,0x202)
    assert events[-1]==(2,0x202)


@pytest.mark.parametrize('accepted',[False,True,None])
def test_windows_tray_rejection_is_not_reported_as_success(app_ctx,monkeypatch,accepted):
    from qingdan.adapters import toast
    monkeypatch.setattr(toast,'_tray_notifier',lambda title,body:accepted)
    channel=toast.ToastChannel(lambda:{'master':True,'channels':{'toast':True}})
    result=channel.send([AlertMessage(item_id='test',kind='task',title='test',fire_at_ms=1)])
    assert result.ok==(accepted is not False)
    if accepted is False:assert result.code=='NOTIFY_FAILED'
