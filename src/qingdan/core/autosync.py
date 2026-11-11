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
    status['next_at']=int(next_time(cfg,stamp.now_dt()).timestamp()*1000) if enabled else None
    st.state_set(STATUS,status)
    return config_get()


@feature(name='autosync.tick',group='autosync',http=('POST','/api/v1/sync/schedule/tick'))
def tick() -> dict:
    st=ctx.need_store();info=config_get();cfg=info['config'];status=info['status'];now=stamp.now_dt();millis=int(now.timestamp()*1000)
    if not cfg['enabled']:return {'ran':False,'reason':'disabled'}
    if status.get('next_at') and millis<status['next_at']:return {'ran':False,'reason':'not_due'}
    status.update(last_attempt_at=millis,next_at=int(next_time(cfg,now).timestamp()*1000))
    st.state_set(STATUS,status)
    try:
        report=call('sync.run')
        if report.get('ok') is False:raise QdError('SYNC_INCOMPLETE','同步未完成',409)
        status.update(last_success_at=millis,error_code=None)
        result={'ran':True,'ok':True,'report':report}
    except Exception as error:
        status.update(error_code=error.code if isinstance(error,QdError) else 'SYNC_FAILED',
                      next_at=millis+min(5,cfg['interval_minutes'])*60000)
        result={'ran':True,'ok':False,'error_code':status['error_code']}
    st.state_set(STATUS,status)
    return result


class AutoSyncLoop:
    def __init__(self):self.stop_event=threading.Event();self.thread=None
    def start(self):
        if self.thread and self.thread.is_alive():return
        self.stop_event.clear()
        def run():
            try:
                while not self.stop_event.wait(5):
                    try:call('autosync.tick')
                    except Exception:pass
            finally:
                if ctx.store:ctx.store.close()
        self.thread=threading.Thread(target=run,daemon=True,name='qingdan-auto-sync');self.thread.start()
    def stop(self):
        self.stop_event.set()
        if self.thread:self.thread.join(timeout=2)


loop=AutoSyncLoop()
