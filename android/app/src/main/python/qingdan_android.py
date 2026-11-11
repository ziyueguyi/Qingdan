"""Android host adapter: no desktop UI, HTTP listener, or Windows credentials."""
import json
from qingdan.core import secrets
from qingdan.core.context import init
from qingdan.core.registry import call,REGISTRY,QdError


def initialize(directory):
    from java import jclass
    crypto=jclass('com.ziyueguyi.qingdan.SecureStore')
    class Keystore:
        def protect(self,value):return str(crypto.encrypt(value))
        def unprotect(self,value):return str(crypto.decrypt(value))
    secrets.set_backend(Keystore())
    init(directory)
    import qingdan.bootstrap
    from qingdan.core import autosync
    autosync.set_minimum_interval(15)
    from qingdan.core.backup import daily_backup
    daily_backup()
    from qingdan.core.alerting.base import register_channel,SendResult
    notifier=jclass('com.ziyueguyi.qingdan.NotificationBridge')
    class Notifications:
        name='toast';backend='android'
        def is_configured(self):return True
        def enabled(self):return True
        def send(self,messages):
            ok=bool(notifier.post('清单提醒','\n'.join(m.title for m in messages)))
            return SendResult(ok=ok,sent_count=len(messages) if ok else 0,code=None if ok else 'permission_required')
        def test(self):
            ok=bool(notifier.post('清单','通知测试'))
            return SendResult(ok=ok,sent_count=1 if ok else 0)
    register_channel(Notifications())


def dispatch(name,params):
    try:
        raw=json.loads(params)
        if not isinstance(raw,dict):raise QdError('VALIDATION_ERROR','参数必须是对象',422)
        if name=='host.foreground':
            from qingdan.core.autosync import loop
            from qingdan.core.alerting.scheduler import scheduler
            if raw.get('active'):loop.start();scheduler.start()
            else:loop.stop();scheduler.stop()
            data={'active':bool(raw.get('active'))}
        elif name=='meta.features':
            data={n:{'http':list(f.http),'cli':f.cli,'params':list(f.params),'needs_auth':f.needs_auth} for n,f in REGISTRY.items()}
        elif name.startswith('access.'):
            raise QdError('PLATFORM_UNSUPPORTED','安卓不开放桌面 API 监听器',409)
        else:data=call(name,**raw)
        return json.dumps({'ok':True,'data':data},ensure_ascii=False)
    except QdError as error:return json.dumps({'ok':False,'error':{'code':error.code,'message':error.message,'detail':error.detail}})
    except Exception:return json.dumps({'ok':False,'error':{'code':'ANDROID_ERROR','message':'操作失败，请检查配置或重启应用'}})
