from concurrent.futures import ThreadPoolExecutor
import json
import threading

import pytest

from qingdan.core.gitrepo import GitRepo, GitError
from qingdan.core.registry import call, QdError
from test_m4 import bare, logged_in_only, wired  # noqa: F401


def invoke(store, name, **kwargs):
    try:
        return call(name, **kwargs)
    finally:
        store.close()  # Release only this worker's thread-local connection.


def test_first_fetch_allows_edit_and_progress_without_losing_new_task(wired, monkeypatch, tmp_path):
    entered, release = threading.Event(), threading.Event()
    original = GitRepo.fetch

    def slow_fetch(self, *args, **kwargs):
        entered.set()
        assert release.wait(10), 'test did not release fetch'
        return original(self, *args, **kwargs)

    monkeypatch.setattr(GitRepo, 'fetch', slow_fetch)
    with ThreadPoolExecutor(max_workers=3) as pool:
        sync = pool.submit(invoke, wired.store, 'sync.run')
        try:
            assert entered.wait(10)
            progress = pool.submit(invoke, wired.store, 'sync.progress').result(timeout=2)
            assert progress['running'] and progress['phase'] == 'fetching'
            created = pool.submit(invoke, wired.store, 'task.create', title='Edited during fetch').result(timeout=2)
            with pytest.raises(QdError) as error:
                pool.submit(invoke, wired.store, 'sync.run').result(timeout=2)
            assert error.value.code == 'SYNC_BUSY'
        finally:
            release.set()
        report = sync.result(timeout=20)
    assert report['ok'] and report['mode'] == 'first_merge'
    assert wired.store.get(created['id']).data['title'] == 'Edited during fetch'
    assert wired.store.dirty_count() == 0
    check = GitRepo(tmp_path/'check')
    check.clone(wired.store.state_get('repo')['remote_url'])
    assert json.loads(check.read_tree()[f"items/task/{created['id']}.json"])['data']['title'] == 'Edited during fetch'
    assert call('sync.progress')['phase'] == 'completed'


def test_edit_during_push_stays_dirty_until_its_own_version_is_pushed(wired, monkeypatch, tmp_path):
    assert call('sync.run')['ok']
    task = call('task.create', title='Version being sent')
    entered, release = threading.Event(), threading.Event()
    original = GitRepo.commit_and_push

    def slow_push(self, files, *args, **kwargs):
        if any(p.startswith('items/') for p in files) and not entered.is_set():
            entered.set()
            assert release.wait(10)
        return original(self, files, *args, **kwargs)

    monkeypatch.setattr(GitRepo, 'commit_and_push', slow_push)
    with ThreadPoolExecutor(max_workers=2) as pool:
        sync = pool.submit(invoke, wired.store, 'sync.run')
        try:
            assert entered.wait(10)
            edit = pool.submit(invoke, wired.store, 'task.update', item_id=task['id'],
                               title='New edit during push').result(timeout=2)
        finally:
            release.set()
        assert sync.result(timeout=20)['ok']
    assert wired.store.get(task['id']).data['title'] == 'New edit during push'
    assert wired.store.dirty_count() == 1
    assert wired.store.base_of(task['id']).rev != wired.store.get(task['id']).rev
    assert call('sync.run')['ok']
    assert wired.store.dirty_count() == 0
    check = GitRepo(tmp_path/'check')
    check.clone(wired.store.state_get('repo')['remote_url'])
    assert json.loads(check.read_tree()[f"items/task/{task['id']}.json"])['data']['title'] == edit['data']['title']


def test_failed_push_does_not_advance_successful_base(wired, monkeypatch):
    call('task.create', title='Previously synchronized')
    assert call('sync.run')['ok']
    before = wired.store.state_get('base_commit')
    call('task.create', title='Unsent edit')

    def fail(*args, **kwargs):
        raise GitError('network unavailable')

    monkeypatch.setattr('qingdan.core.gitrepo.porcelain.push', fail)
    assert call('sync.run')['ok'] is False
    assert wired.store.state_get('base_commit') == before
    assert wired.store.dirty_count() == 1
    assert call('sync.progress')['phase'] == 'failed'


def test_progress_from_previous_process_is_not_reported_as_still_running(app_ctx):
    app_ctx.store.state_set('sync_progress', {'running':True,'phase':'fetching'})
    assert call('sync.progress')['phase'] == 'interrupted'
    assert call('sync.progress')['running'] is False


def test_logout_waits_for_sync_without_blocking_local_edits(wired, monkeypatch):
    entered,release,logout_started=threading.Event(),threading.Event(),threading.Event()
    original=GitRepo.fetch
    def slow_fetch(self,*args,**kwargs):
        entered.set();assert release.wait(10)
        return original(self,*args,**kwargs)
    monkeypatch.setattr(GitRepo,'fetch',slow_fetch)
    def logout():
        logout_started.set()
        return invoke(wired.store,'auth.logout')
    with ThreadPoolExecutor(max_workers=3) as pool:
        sync=pool.submit(invoke,wired.store,'sync.run')
        try:
            assert entered.wait(10)
            pending_logout=pool.submit(logout)
            assert logout_started.wait(2)
            created=pool.submit(invoke,wired.store,'task.create',title='Local edit while logout waits').result(timeout=2)
            assert not pending_logout.done()
        finally:release.set()
        assert sync.result(timeout=20)['ok']
        assert pending_logout.result(timeout=5)=={'authed':False}
    assert wired.store.get(created['id']).data['title']=='Local edit while logout waits'


def test_http_event_loop_serves_edit_and_progress_during_slow_sync(wired, monkeypatch):
    import asyncio
    import httpx
    from qingdan.api.http import build_app
    entered,release=threading.Event(),threading.Event()
    original=GitRepo.fetch
    def slow_fetch(self,*args,**kwargs):
        entered.set();assert release.wait(10)
        return original(self,*args,**kwargs)
    monkeypatch.setattr(GitRepo,'fetch',slow_fetch)

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=build_app()),base_url='http://127.0.0.1') as client:
            sync=asyncio.create_task(client.post('/api/v1/sync/run'))
            try:
                assert await asyncio.to_thread(entered.wait,10)
                assert not sync.done(), 'A synchronous endpoint blocked the ASGI event loop'
                progress=await asyncio.wait_for(client.get('/api/v1/sync/progress'),2)
                assert progress.json()['data']['running']
                created=await asyncio.wait_for(client.post('/api/v1/tasks',json={'title':'Edited through HTTP during sync'}),2)
                assert created.status_code==200,created.text
            finally:release.set()
            assert (await asyncio.wait_for(sync,20)).json()['data']['ok']
            return created.json()['data']
    created=asyncio.run(scenario())
    assert wired.store.get(created['id']).data['title']=='Edited through HTTP during sync'
