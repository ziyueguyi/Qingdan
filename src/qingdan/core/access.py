"""Local access administration and per-feature remote API authorization.

Access credentials/config/audit live in access.sqlite, outside task snapshots.
Restoring a task backup must never resurrect a revoked API credential.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import ipaddress
import json
from pathlib import Path
import secrets
import sqlite3
import ssl
import time
import threading
from typing import Callable

from qingdan.core.context import ctx
from qingdan.core.registry import REGISTRY, QdError, feature, not_found, validation_error

PERMISSIONS = ('read', 'create', 'update', 'delete', 'execute')
DEFAULTS = {'enabled': False, 'host': '0.0.0.0', 'port': 19527,
            'transport': 'https', 'http_port':19528,'auto_tls':True,'tls_cert': '', 'tls_key': '',
            'requests_per_minute': 120, 'max_body_kb': 1024}
_RUNTIME_APPLY: Callable | None = None
_RUNTIME_STATUS: Callable | None = None
_SCHEMA_LOCK = threading.Lock()
SCHEMA = '''
CREATE TABLE IF NOT EXISTS config (k TEXT PRIMARY KEY, v TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS api_token (
 id TEXT PRIMARY KEY, digest TEXT NOT NULL, name TEXT NOT NULL,
 permissions TEXT NOT NULL, features TEXT NOT NULL, created_at INTEGER NOT NULL,
 expires_at INTEGER, revoked INTEGER NOT NULL DEFAULT 0, last_used INTEGER, last_client TEXT
);
CREATE TABLE IF NOT EXISTS api_audit (
 id INTEGER PRIMARY KEY AUTOINCREMENT, at INTEGER NOT NULL, token_id TEXT,
 token_name TEXT, client_ip TEXT, feature TEXT, method TEXT, status INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_api_audit_at ON api_audit(at,id);
'''


def now_ms() -> int:
    return int(time.time()*1000)


@contextmanager
def db():
    ctx.need_store()
    connection = sqlite3.connect(Path(ctx.data_dir)/'access.sqlite', timeout=10)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute('PRAGMA journal_mode=WAL')
        with _SCHEMA_LOCK:
            connection.executescript(SCHEMA)
            columns={r[1] for r in connection.execute('PRAGMA table_info(api_token)')}
            for name,definition in {'max_qps':'INTEGER NOT NULL DEFAULT 10','max_calls':'INTEGER','call_count':'INTEGER NOT NULL DEFAULT 0','note':"TEXT NOT NULL DEFAULT ''"}.items():
                if name not in columns:connection.execute(f'ALTER TABLE api_token ADD COLUMN {name} {definition}')
            connection.commit()
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def set_runtime(apply: Callable | None = None, status: Callable | None = None):
    global _RUNTIME_APPLY, _RUNTIME_STATUS
    _RUNTIME_APPLY, _RUNTIME_STATUS = apply, status


def get_config() -> dict:
    with db() as con:
        row = con.execute("SELECT v FROM config WHERE k='remote'").fetchone()
    saved=json.loads(row['v']) if row else {}
    if 'auto_tls' not in saved and saved.get('tls_cert'):saved['auto_tls']=False
    return {**DEFAULTS, **saved}


def validate_config(value: dict) -> dict:
    unknown = set(value)-set(DEFAULTS)
    if unknown:
        raise validation_error('不支持的访问配置', fields=sorted(unknown))
    cfg = {**get_config(), **value}
    if 'auto_tls' not in value and (value.get('tls_cert') or value.get('tls_key')):cfg['auto_tls']=False
    if type(cfg['enabled']) is not bool:
        raise validation_error('enabled 必须是布尔值')
    if type(cfg['auto_tls']) is not bool:raise validation_error('auto_tls 必须是布尔值')
    for key, low, high in (('port',1,65535),('http_port',1,65535),('requests_per_minute',10,6000),('max_body_kb',1,32768)):
        if type(cfg[key]) is not int or not low <= cfg[key] <= high:
            raise validation_error(f'{key} 需在 {low}..{high}')
    try:
        ipaddress.ip_address(cfg['host'])
    except (ValueError, TypeError):
        raise validation_error('监听地址必须是 IPv4 或 IPv6 地址') from None
    if not isinstance(cfg['transport'],str) or cfg['transport'] not in {'https','http','both'}:
        raise validation_error('transport 只能是 https|http|both')
    if cfg['transport']=='both' and cfg['port']==cfg['http_port']:raise validation_error('HTTP 和 HTTPS 端口不能相同')
    for key in ('tls_cert','tls_key'):
        if not isinstance(cfg[key],str):
            raise validation_error(f'{key} 必须是文件路径')
        cfg[key] = cfg[key].strip()
        if cfg[key]:
            path=Path(cfg[key]).expanduser()
            if not path.is_absolute(): path=Path(ctx.data_dir)/path
            cfg[key]=str(path.resolve())
    if cfg['enabled'] and cfg['transport']!='http' and not cfg['auto_tls']:
        if not cfg['tls_cert'] or not cfg['tls_key']:
            raise validation_error('启用 HTTPS 前需配置 PEM 证书和私钥')
        try:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(cfg['tls_cert'],cfg['tls_key'],password=lambda:'')
        except (OSError,ssl.SSLError) as e:
            raise validation_error(f'TLS 证书或私钥不可用：{e}') from e
    return cfg


def policy(name: str, raw: dict | None = None) -> set[str] | None:
    """None means local-only. Unknown POST actions fail closed until classified."""
    f = REGISTRY[name]
    group, action = name.split('.',1)
    if group in {'access','auth'} or name.startswith('app.autostart') or name=='app.instance_status':
        return None
    special = {
        'autosync.config_set': {'create','update','delete','execute'},
        'autosync.tick': {'create','update','delete','execute'},
        'data.import': {'create','update','delete'},
        'data.export': {'read','create'},
        'undo.last': {'create','update','delete'},
        'backup.create': {'read','create'},
        'backup.restore': {'read','create','update','delete','execute'},
        'backup.prune': {'delete'},
        'repo.ensure': {'create','execute'},
        'sync.run': {'create','update','delete','execute'},
        'sync.push': {'create','update','delete','execute'},
        'sync.pull': {'create','update','delete','execute'},
        'sync.compact': {'delete','execute'},
        'alert.channel_test': {'execute'}, 'alert.tick': {'execute'},
        'alert.rebuild': {'execute'}, 'task.repeat_preview': {'read'},
    }
    if name == 'task.quick_add':
        value = (raw or {}).get('create',False)
        if isinstance(value,str):
            value = value.strip().lower()
            if value not in {'true','false','1','0','yes','no','on','off'}:
                raise validation_error('create 必须是布尔值')
            value = value in {'true','1','yes','on'}
        elif type(value) is not bool:
            raise validation_error('create 必须是布尔值')
        return {'create'} if value else {'read'}
    if name in special:
        return special[name]
    method = f.http[0]
    if method == 'GET': return {'read'}
    if method == 'DELETE': return {'delete'}
    if method in {'PATCH','PUT'}: return {'update'}
    if action in {'create','append'}: return {'create'}
    if action in {'complete','skip','restore','archive','reopen','move','reorder','snooze',
                   'assign','unassign','rename','silence','channel_enable','conflict_resolve'} or action.startswith('set_'):
        return {'update'}
    return None


def can_use(principal: dict, name: str, raw: dict | None = None) -> bool:
    required = policy(name,raw)
    return (required is not None and required <= set(principal['permissions'])
            and ('*' in principal['features'] or name in principal['features']))


def require_permission(principal: dict, name: str, raw: dict | None = None):
    # Recheck after body upload: revocation/expiry/scope edits may have happened
    # while a slow remote caller was still sending its request.
    with db() as con:
        row=con.execute('SELECT * FROM api_token WHERE id=?',(principal['id'],)).fetchone()
        if row is None: raise QdError('API_TOKEN_INVALID','无效的 API Token',401)
        if row['revoked']: raise QdError('API_TOKEN_REVOKED','API Token 已撤销',401)
        if row['expires_at'] is not None and row['expires_at']<=now_ms():
            raise QdError('API_TOKEN_EXPIRED','API Token 已过期',401)
        principal=_public(row)
    if not can_use(principal,name,raw):
        code = 'LOCAL_ONLY' if policy(name,raw) is None else 'PERMISSION_DENIED'
        raise QdError(code,'此 Token 没有调用该接口的权限',403,{'feature':name})
    return principal


def _scopes(values: list | None) -> list[str]:
    values = ['read'] if values is None else values
    if not isinstance(values,list) or not values or any(not isinstance(x,str) for x in values):
        raise validation_error('permissions 必须是非空权限数组')
    values = sorted(set('create' if x=='write' else x for x in values))
    if set(values)-set(PERMISSIONS):
        raise validation_error('不支持的权限类型',allowed=list(PERMISSIONS))
    return values


def _features(values: list | None) -> list[str]:
    values = ['*'] if values is None else values
    if not isinstance(values,list) or not values or any(not isinstance(x,str) for x in values):
        raise validation_error('features 必须是非空接口名称数组')
    if '*' in values:
        if len(values)!=1: raise validation_error('全部接口范围不能同时指定单个接口')
        return ['*']
    unknown = [name for name in values if name not in REGISTRY or policy(name) is None]
    if unknown: raise validation_error('包含不存在或仅限本机的接口',features=unknown)
    return sorted(set(values))


def _expiry(value: str | None, never: bool = False) -> int | None:
    if never:
        if value: raise validation_error('永久有效与到期时间不能同时设置')
        return None
    if value is None: return now_ms()+30*86400000
    try:
        when = datetime.fromisoformat(value.replace('Z','+00:00'))
        result = int(when.timestamp()*1000)
    except (ValueError,TypeError,OverflowError,OSError):
        raise validation_error('到期时间必须是 ISO8601 日期时间') from None
    if result <= now_ms(): raise validation_error('到期时间必须在将来')
    return result


def _public(row) -> dict:
    return {'id':row['id'],'name':row['name'],
            'permissions':json.loads(row['permissions']),'features':json.loads(row['features']),
            'created_at_ms':row['created_at'],'expires_at_ms':row['expires_at'],
            'revoked':bool(row['revoked']),
            'expired':row['expires_at'] is not None and row['expires_at']<=now_ms(),
            'last_used_ms':row['last_used'],'last_client':row['last_client'],
            'max_qps':row['max_qps'],'max_calls':row['max_calls'],'call_count':row['call_count'],'note':row['note']}


def _limits(qps,quota,note):
    if type(qps) is not int or not 1<=qps<=1000:raise validation_error('QPS 需为 1..1000')
    if quota is not None and (type(quota) is not int or not 1<=quota<=1_000_000_000):raise validation_error('调用限制需为正整数或空值（无限制）')
    if not isinstance(note,str) or len(note)>500:raise validation_error('备注不能超过 500 个字符')


def reserve_call(token_id,client):
    with db() as con:
        updated=con.execute('UPDATE api_token SET call_count=call_count+1,last_used=?,last_client=? WHERE id=? AND revoked=0 AND (expires_at IS NULL OR expires_at>?) AND (max_calls IS NULL OR call_count<max_calls)',(now_ms(),client,token_id,now_ms()))
        if not updated.rowcount:raise QdError('TOKEN_QUOTA_EXHAUSTED','Token 调用次数已达上限或已失效',429)


def authenticate(raw: str, client: str = '') -> dict:
    # Only this explicit header credential is accepted, never cookies/query parameters.
    prefix = raw.split('.',1)[0]
    token_id = prefix[3:] if prefix.startswith('qd_') else ''
    with db() as con:
        row = con.execute('SELECT * FROM api_token WHERE id=?',(token_id,)).fetchone()
        digest = hashlib.sha256(raw.encode('utf-8')).hexdigest()
        expected = row['digest'] if row else '0'*64
        if not secrets.compare_digest(digest,expected) or row is None:
            raise QdError('API_TOKEN_INVALID','无效的 API Token',401)
        if row['revoked']: raise QdError('API_TOKEN_REVOKED','API Token 已撤销',401)
        if row['expires_at'] is not None and row['expires_at'] <= now_ms():
            raise QdError('API_TOKEN_EXPIRED','API Token 已过期',401)
        if row['last_used'] is None or now_ms()-row['last_used']>=60000:
            con.execute('UPDATE api_token SET last_used=?,last_client=? WHERE id=?',
                        (now_ms(),client,token_id))
        return _public(row)


def audit(principal: dict | None, client: str, name: str | None, method: str, status: int):
    with db() as con:
        con.execute('INSERT INTO api_audit(at,token_id,token_name,client_ip,feature,method,status) VALUES (?,?,?,?,?,?,?)',
                    (now_ms(),principal.get('id') if principal else None,
                     principal.get('name') if principal else None,client,name,method,status))
        con.execute('DELETE FROM api_audit WHERE id NOT IN (SELECT id FROM api_audit ORDER BY id DESC LIMIT 1000)')


@feature(name='access.config_get',group='access',http=('GET','/api/v1/access/config'),idempotent=True)
def config_get() -> dict:
    local_url = None
    try:
        status = json.loads((Path(ctx.data_dir)/'http.json').read_text(encoding='utf-8'))
        if type(status.get('port')) is int: local_url = f"http://127.0.0.1:{status['port']}"
    except (OSError,ValueError,AttributeError): pass
    return {'config':get_config(),'runtime':_RUNTIME_STATUS() if _RUNTIME_STATUS else {'running':False,'available':False},
            'local_url':local_url,'cli_local_only':True,'permissions':list(PERMISSIONS)}


@feature(name='access.config_set',group='access',http=('PATCH','/api/v1/access/config'))
def config_set(config: dict) -> dict:
    if not config: raise validation_error('访问配置不能为空')
    value = validate_config(config)
    previous = get_config()
    if _RUNTIME_APPLY: _RUNTIME_APPLY(value)
    try:
        with db() as con:
            con.execute("INSERT INTO config(k,v) VALUES ('remote',?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
                        (json.dumps(value,sort_keys=True),))
    except Exception:
        if _RUNTIME_APPLY: _RUNTIME_APPLY(previous)
        raise
    return config_get()


@feature(name='access.catalog',group='access',http=('GET','/api/v1/access/features'),idempotent=True)
def catalog() -> dict:
    items=[]
    for name,f in sorted(REGISTRY.items()):
        required=policy(name)
        values=sorted(required) if required is not None else []
        if name=='task.quick_add': values=['read','create']
        items.append({'name':name,'http':list(f.http),'cli':f.cli,'permissions':values,
                      'remote_allowed':required is not None,'conditional':name=='task.quick_add'})
    return {'items':items,'total':len(items),'permissions':list(PERMISSIONS)}


@feature(name='access.token_create',group='access',http=('POST','/api/v1/access/tokens'))
def token_create(name: str, permissions: list | None = None, features: list | None = None,
                 expires_at: str | None = None, never_expires: bool = False,
                 max_qps: int=10,max_calls: int | None=None,note: str='') -> dict:
    if not isinstance(name,str) or not name.strip() or len(name.strip())>100:
        raise validation_error('Token 名称需为 1..100 个字符')
    permissions,features = _scopes(permissions),_features(features)
    expiry = _expiry(expires_at,never_expires)
    _limits(max_qps,max_calls,note)
    token_id=secrets.token_hex(16)
    raw='qd_'+token_id+'.'+secrets.token_urlsafe(32)
    with db() as con:
        con.execute('INSERT INTO api_token(id,digest,name,permissions,features,created_at,expires_at) VALUES (?,?,?,?,?,?,?)',
                    (token_id,hashlib.sha256(raw.encode()).hexdigest(),name.strip(),
                     json.dumps(permissions),json.dumps(features),now_ms(),expiry))
        item=_public(con.execute('SELECT * FROM api_token WHERE id=?',(token_id,)).fetchone())
        con.execute('UPDATE api_token SET max_qps=?,max_calls=?,note=? WHERE id=?',(max_qps,max_calls,note,token_id))
        item=_public(con.execute('SELECT * FROM api_token WHERE id=?',(token_id,)).fetchone())
    return {'token':raw,'item':item,'one_time':True}


@feature(name='access.token_list',group='access',http=('GET','/api/v1/access/tokens'),idempotent=True)
def token_list(limit: int = 5, offset: int = 0, include_revoked: bool = False) -> dict:
    if not 1<=limit<=100 or offset<0: raise validation_error('limit 需在 1..100，offset 不能小于零')
    where='' if include_revoked else ' WHERE revoked=0'
    with db() as con:
        total=con.execute('SELECT COUNT(*) FROM api_token'+where).fetchone()[0]
        rows=con.execute('SELECT * FROM api_token'+where+' ORDER BY created_at DESC,id LIMIT ? OFFSET ?',
                         (limit,offset)).fetchall()
    return {'total':total,'items':[_public(r) for r in rows],'offset':offset,'limit':limit,
            'has_more':offset+len(rows)<total}


@feature(name='access.token_update',group='access',http=('PATCH','/api/v1/access/tokens/{token_id}'))
def token_update(token_id: str, name: str | None = None, permissions: list | None = None,
                 features: list | None = None, expires_at: str | None = None,
                 never_expires: bool | None = None,max_qps: int | None=None,max_calls: int | None=None,
                 unlimited_calls: bool=False,note: str | None=None) -> dict:
    with db() as con:
        row=con.execute('SELECT * FROM api_token WHERE id=?',(token_id,)).fetchone()
        if row is None: raise not_found('API Token')
        if name is not None and (not name.strip() or len(name.strip())>100):
            raise validation_error('Token 名称需为 1..100 个字符')
        p=_scopes(permissions) if permissions is not None else json.loads(row['permissions'])
        f=_features(features) if features is not None else json.loads(row['features'])
        expiry=_expiry(expires_at,bool(never_expires)) if expires_at is not None or never_expires is not None else row['expires_at']
        qps=row['max_qps'] if max_qps is None else max_qps
        quota=None if unlimited_calls else row['max_calls'] if max_calls is None else max_calls
        remark=row['note'] if note is None else note
        _limits(qps,quota,remark)
        con.execute('UPDATE api_token SET name=?,permissions=?,features=?,expires_at=? WHERE id=?',
                    (name.strip() if name is not None else row['name'],json.dumps(p),json.dumps(f),expiry,token_id))
        con.execute('UPDATE api_token SET max_qps=?,max_calls=?,note=? WHERE id=?',(qps,quota,remark,token_id))
        return {'item':_public(con.execute('SELECT * FROM api_token WHERE id=?',(token_id,)).fetchone())}


@feature(name='access.token_revoke',group='access',http=('DELETE','/api/v1/access/tokens/{token_id}'))
def token_revoke(token_id: str) -> dict:
    with db() as con:
        if con.execute('SELECT id FROM api_token WHERE id=?',(token_id,)).fetchone() is None:
            raise not_found('API Token')
        con.execute('UPDATE api_token SET revoked=1 WHERE id=?',(token_id,))
    return {'id':token_id,'revoked':True}


@feature(name='access.audit',group='access',http=('GET','/api/v1/access/audit'),idempotent=True)
def audit_list(limit: int = 20, offset: int = 0) -> dict:
    if not 1<=limit<=100 or offset<0: raise validation_error('limit 需在 1..100，offset 不能小于零')
    with db() as con:
        total=con.execute('SELECT COUNT(*) FROM api_audit').fetchone()[0]
        items=[dict(r) for r in con.execute('SELECT * FROM api_audit ORDER BY id DESC LIMIT ? OFFSET ?', (limit,offset))]
    return {'items':items,'total':total,'offset':offset,'has_more':offset+len(items)<total}
