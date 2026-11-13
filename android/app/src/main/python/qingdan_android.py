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
            from qingdan.core.notification_inbox import capture
            total=capture(messages)
            if len(messages)>1:
                ok=bool(notifier.postSummary(total))
                return SendResult(ok=ok,sent_count=1 if ok else 0,covered=len(messages) if ok else 0,
                                  code='' if ok else 'permission_required')
            sent=0
            for message in messages:
                payload=json.dumps({'item_id':message.item_id,'kind':message.kind,
                                    'expected_rev':message.revision,'fire_at':message.fire_at_ms})
                if notifier.postItem(message.title,message.when_text,payload):sent+=1
            if total>1:notifier.postSummary(total)
            ok=sent==len(messages)
            return SendResult(ok=ok,sent_count=sent,covered=sent,code='' if ok else 'permission_required')
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
        elif name=='app.autostart_get':
            data={'enabled':False,'command':None,'expected':None,'frozen':False,'reason_key':'platform_unsupported'}
        elif name=='app.autostart_set':
            raise QdError('PLATFORM_UNSUPPORTED','安卓后台任务由系统管理',409)
        elif name=='meta.features':
            data={n:{'http':list(f.http),'cli':f.cli,'params':list(f.params),'needs_auth':f.needs_auth} for n,f in REGISTRY.items()}
        elif name.startswith('access.'):
            raise QdError('PLATFORM_UNSUPPORTED','安卓不开放桌面 API 监听器',409)
        else:data=call(name,**raw)
        return json.dumps({'ok':True,'data':data},ensure_ascii=False)
    except QdError as error:return json.dumps({'ok':False,'error':{'code':error.code,'message':error.message,'detail':error.detail}})
    except Exception as error:
        import traceback
        from java import jclass
        frames=' > '.join(f'{f.name}:{f.lineno}' for f in traceback.extract_tb(error.__traceback__))
        jclass('android.util.Log').e('Qingdan',f'{name}: {type(error).__name__} at {frames}')
        return json.dumps({'ok':False,'error':{'code':'ANDROID_ERROR','message':'操作失败，请检查配置或重启应用'}})
