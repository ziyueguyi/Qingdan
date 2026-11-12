import threading
from qingdan.core.alerting.scheduler import Scheduler


def test_restart_does_not_revive_a_stopped_loop(app_ctx, monkeypatch):
    scheduler=Scheduler(interval_s=0.01)
    entered,release,new_tick=threading.Event(),threading.Event(),threading.Event()
    calls=0
    gate=threading.Lock()

    def blocked_tick():
        nonlocal calls
        with gate:
            calls+=1
            first=calls==1
        if first:
            entered.set()
            assert release.wait(5)
        else:new_tick.set()

    monkeypatch.setattr(scheduler,'run_once',blocked_tick)
    scheduler.start(skip_stale=False)
    old=scheduler._thread
    new=None
    try:
        assert entered.wait(2)
        scheduler.stop()
        scheduler.start(skip_stale=False)
        new=scheduler._thread
        assert new is not old and new_tick.wait(2)
        release.set()
        old.join(timeout=2)
        assert not old.is_alive(), 'Restart must not clear the old loop stop event'
        assert new.is_alive()
    finally:
        scheduler.stop();release.set();old.join(timeout=2)
        if new:new.join(timeout=2)


def test_background_delivery_does_not_hold_the_task_operation_lock(app_ctx, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from qingdan.core.registry import call
    entered,release=threading.Event(),threading.Event()
    scheduler=Scheduler()
    def delivery(now):
        entered.set();assert release.wait(5)
        return {'sent':1}
    monkeypatch.setattr('qingdan.core.alerting.scheduler.dispatch',delivery)
    def edit():
        try:return call('task.create',title='Editing during reminder delivery')
        finally:app_ctx.store.close()
    with ThreadPoolExecutor(max_workers=2) as pool:
        sending=pool.submit(scheduler.run_once)
        try:
            assert entered.wait(2)
            assert pool.submit(edit).result(timeout=2)['data']['title']=='Editing during reminder delivery'
        finally:release.set()
        assert sending.result(timeout=2)['sent']==1
