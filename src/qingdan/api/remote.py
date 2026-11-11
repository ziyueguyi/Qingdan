"""Optional second listener. The local desktop/CLI listener stays loopback-only."""
from __future__ import annotations
import ipaddress
import socket
import threading
import time

import uvicorn

from qingdan.api.http import build_app
from qingdan.core import access
from qingdan.core.context import ctx
from qingdan.core.registry import QdError


class RemoteServer:
    def __init__(self):
        self.server = None
        self.thread = None
        self.config = None
        self.error = None
        self._lock = threading.Lock()

    def status(self) -> dict:
        running = bool(self.server and self.server.started and self.thread and self.thread.is_alive()
                       and not self.server.should_exit)
        return {'available':True,'running':running,'error_code':self.error,
                'host':self.config.get('host') if self.config else None,
                'port':self.config.get('port') if self.config else None,
                'transport':self.config.get('transport') if self.config else None}

    def _start(self, value: dict):
        family = socket.AF_INET6 if ipaddress.ip_address(value['host']).version==6 else socket.AF_INET
        listener = socket.socket(family,socket.SOCK_STREAM)
        try:
            if hasattr(socket,'SO_EXCLUSIVEADDRUSE'):
                listener.setsockopt(socket.SOL_SOCKET,socket.SO_EXCLUSIVEADDRUSE,1)
            else:
                listener.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
            listener.bind((value['host'],value['port']))
            config = uvicorn.Config(build_app(remote=True),host=value['host'],port=value['port'],
                                    access_log=False,log_config=None,log_level='warning',
                                    proxy_headers=False,lifespan='off',timeout_graceful_shutdown=2,
                                    ssl_certfile=value['tls_cert'] if value['transport']=='https' else None,
                                    ssl_keyfile=value['tls_key'] if value['transport']=='https' else None,
                                    ssl_keyfile_password='')
            server = uvicorn.Server(config)
            self.server = server
            def run():
                try:
                    server.run(sockets=[listener])
                except BaseException:
                    self.error = 'start_failed'
                finally:
                    listener.close()
                    if ctx.store is not None: ctx.store.close()
            self.thread = threading.Thread(target=run,daemon=True,name='qingdan-remote-api')
            self.thread.start()
            deadline = time.monotonic()+5
            while not server.started:
                if not self.thread.is_alive() or time.monotonic()>deadline:
                    server.should_exit=True
                    raise RuntimeError('remote listener did not start')
                time.sleep(.02)
        except BaseException:
            if not self.thread or not self.thread.is_alive(): listener.close()
            raise

    def _stop(self):
        if self.server: self.server.should_exit=True
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=5)
            if self.thread.is_alive():
                self.server.should_exit=False
                raise RuntimeError('remote listener is still processing a request')
        self.server = None
        self.thread = None

    def apply(self, value: dict) -> dict:
        with self._lock:
            previous = self.config
            try:
                self._stop()
                if value['enabled']: self._start(value)
                self.config = dict(value)
                self.error = None
            except Exception as error:
                self.error = 'start_failed'
                if previous and previous['enabled'] and not (self.thread and self.thread.is_alive()):
                    try:
                        self._start(previous)
                        self.config = previous
                    except Exception:
                        self.error = 'rollback_failed'
                raise QdError('REMOTE_START_FAILED',f'远程 API 启动或切换失败：{error}',409) from error
            return self.status()

    def close(self):
        with self._lock:
            try:
                self._stop()
            except Exception:
                if self.server: self.server.force_exit=True
        access.set_runtime()


class RemoteGateway:
    """One or two authenticated listeners, switched together with rollback."""
    def __init__(self):self.members=[];self.config=None;self.error=None;self.lock=threading.Lock()
    def status(self):
        states=[s.status() for s in self.members]
        return {'available':True,'running':bool(states) and all(s['running'] for s in states),'error_code':self.error,'listeners':states}
    def _start(self,value):
        self.members=[]
        if not value['enabled']:return
        if value['transport']!='http' and value['auto_tls']:
            from qingdan.api.tls import ensure_certificate
            value['tls_cert'],value['tls_key']=ensure_certificate(value['host'])
        transports=['https','http'] if value['transport']=='both' else [value['transport']]
        try:
            for mode in transports:
                member=RemoteServer();self.members.append(member)
                member.apply({**value,'transport':mode,'port':value['http_port'] if mode=='http' and value['transport']=='both' else value['port']})
        except Exception:
            self._stop();raise
    def _stop(self):
        for member in self.members:
            with member._lock:member._stop()
        self.members=[]
    def apply(self,value):
        with self.lock:
            previous=self.config
            try:
                self._stop();self._start(value);self.config=dict(value);self.error=None
            except Exception as error:
                self.error='start_failed'
                if previous:
                    try:self._stop();self._start(dict(previous))
                    except Exception:self.error='rollback_failed'
                raise QdError('REMOTE_START_FAILED',f'远程监听切换失败：{error}',409) from error
        return self.status()
    def close(self):
        with self.lock:
            for member in self.members:member.close()
            self.members=[]
        access.set_runtime()


def install_remote() -> RemoteGateway:
    manager=RemoteGateway()
    access.set_runtime(manager.apply,manager.status)
    try:
        manager.apply(access.get_config())
    except Exception:
        # An invalid port/certificate never prevents opening the local settings UI.
        if manager.error is None: manager.error='config_unavailable'
    return manager
