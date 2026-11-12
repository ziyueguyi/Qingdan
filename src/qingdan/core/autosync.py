"""Device-local scheduling for the existing incremental bidirectional sync engine."""
from datetime import datetime, timedelta
import re
import threading

from qingdan.core.context import ctx
from qingdan.core.registry import feature, call, QdError, validation_error
from qingdan.core import stamp

KEY='auto_sync_config'
STATUS='auto_sync_status'
DEFAULT={'enabled':False,'mode':'interval','interval_minutes':15,'times':['09:00','18:00']}
_minimum=1
ACTION_REQUIRED = {'AUTH_REQUIRED','TOKEN_INVALID','TOKEN_EXPIRED','SCOPE_MISSING',
                   'REPO_PUBLIC','REPO_NOT_PRIVATE','REPO_NOT_READY','VALIDATION_ERROR',
                   'clock_skew','CLOCK_SKEW'}


def set_minimum_interval(minutes):
    global _minimum
    _minimum=minutes


def next_time(config,now):
    if config['mode']=='interval':return now+timedelta(minutes=config['interval_minutes'])
    candidates=[]
    for value in config['times']:
        hour,minute=map(int,value.split(':'))
        day=now.replace(hour=hour,minute=minute,second=0,microsecond=0)
        candidates.append(day if day>now else day+timedelta(days=1))
    return min(candidates)


@feature(name='autosync.config_get',group='autosync',http=('GET','/api/v1/sync/schedule'),idempotent=True)
def config_get() -> dict:
    st=ctx.need_store()
    return {'config':{**DEFAULT,**(st.state_get(KEY) or {})},'status':st.state_get(STATUS) or {},
            'minimum_interval_minutes':_minimum}


@feature(name='autosync.config_set',group='autosync',http=('PATCH','/api/v1/sync/schedule'))
def config_set(enabled: bool=False,mode: str='interval',interval_minutes: int=15,times: list | None=None) -> dict:
    if mode not in {'interval','daily'}:raise validation_error('mode 必须是 interval|daily')
    if not _minimum<=interval_minutes<=10080:raise validation_error(f'间隔需在 {_minimum}..10080 分钟')
    times=DEFAULT['times'] if times is None else times
    if not times or len(times)>24 or any(not isinstance(t,str) or not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d',t) for t in times):
        raise validation_error('每天时间需为 HH:mm，至少一个，最多 24 个')
    cfg={'enabled':enabled,'mode':mode,'interval_minutes':interval_minutes,'times':sorted(set(times))}
    st=ctx.need_store();st.state_set(KEY,cfg)
    status=st.state_get(STATUS) or {}
    status.update(next_at=int(next_time(cfg,stamp.now_dt()).timestamp()*1000) if enabled else None,
                  paused=False, error_code=None, consecutive_failures=0,
                  schedule_revision=status.get('schedule_revision',0)+1)
    st.state_set(STATUS,status)
    return config_get()


@feature(name='autosync.tick',group='autosync',http=('POST','/api/v1/sync/schedule/tick'),manages_lock=True)
def tick() -> dict:
    st=ctx.need_store()
    if not st.autosync_lock.acquire(blocking=False):return {'ran':False,'reason':'running'}
    try:
        return _tick_once(st)
    finally:
        st.autosync_lock.release()


def _tick_once(st):
    now=stamp.now_dt();millis=int(now.timestamp()*1000)
    with st.operation_lock:
        info=config_get();cfg=info['config'];status=info['status']
        if not cfg['enabled']:return {'ran':False,'reason':'disabled'}
        if status.get('paused'):return {'ran':False,'reason':'action_required','error_code':status.get('error_code')}
        # Owning autosync_lock means no attempt is alive in this process. Recover a crash claim.
        if status.get('attempt_running'):status['next_at']=millis
        if status.get('next_at') and millis<status['next_at']:return {'ran':False,'reason':'not_due'}
        revision=status.get('schedule_revision',0)
        status.update(last_attempt_at=millis,attempt_running=True,next_at=int(next_time(cfg,now).timestamp()*1000))
        st.state_set(STATUS,status)
    report=None
    try:
        report=call('sync.run')
        if report.get('ok') is False:raise QdError(report.get('error_key') or 'SYNC_INCOMPLETE','同步未完成',409)
        error_code=None
    except Exception as error:
        error_code=getattr(error,'code',None) or 'SYNC_FAILED'
    finished_at=stamp.now_ms()
    retryable=error_code is not None and error_code not in ACTION_REQUIRED
    with st.operation_lock:
        status=st.state_get(STATUS) or {}
        status.update(last_finished_at=finished_at,attempt_running=False,error_code=error_code)
        if error_code is None:
            status.update(last_success_at=finished_at,consecutive_failures=0)
        elif status.get('schedule_revision',0)==revision:
            failures=status.get('consecutive_failures',0)+1
            delay=min(30,2**min(failures-1,5))
            status.update(consecutive_failures=failures,paused=not retryable,
                          next_at=finished_at+delay*60000 if retryable else None)
        st.state_set(STATUS,status)
    if error_code:return {'ran':True,'ok':False,'error_code':error_code,'retryable':retryable}
    return {'ran':True,'ok':True,'report':report}


def resume_after_auth():
    """New credentials re-enable a schedule paused by authentication failure."""
    st=ctx.need_store()
    with st.operation_lock:
        status=st.state_get(STATUS) or {}
        if status.get('paused') and status.get('error_code') in {'AUTH_REQUIRED','TOKEN_INVALID','TOKEN_EXPIRED','SCOPE_MISSING'}:
            status.update(paused=False,error_code=None,consecutive_failures=0,next_at=stamp.now_ms())
            st.state_set(STATUS,status)


class AutoSyncLoop:
    def __init__(self):self.stop_event=threading.Event();self.thread=None;self.lifecycle_lock=threading.Lock()
    def start(self):
        with self.lifecycle_lock:
            if self.thread and self.thread.is_alive() and not self.stop_event.is_set():return
            self.stop_event=threading.Event()
            stop_event=self.stop_event
            def run():
                try:
                    while not stop_event.wait(5):
                        try:call('autosync.tick')
                        except Exception:pass
                finally:
                    if ctx.store:ctx.store.close()
            self.thread=threading.Thread(target=run,daemon=True,name='qingdan-auto-sync');self.thread.start()
    def stop(self):
        with self.lifecycle_lock:
            self.stop_event.set();thread=self.thread
        if thread:thread.join(timeout=2)


loop=AutoSyncLoop()
