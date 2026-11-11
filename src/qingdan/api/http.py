"""HTTP 壳：路由由 registry 单点派生，**禁止手写路由**。

这是 R4 的落地方式 —— 功能只要在 @feature 里声明了 http，路由就一定存在；
漏声明在注册阶段就报错，不会等到运行时才发现「这个功能没法用接口调」。

实现细节：用 Starlette 的 `Route` 而不是 `app.add_api_route`。原因是 add_api_route 会解析
endpoint 签名做依赖注入，把闭包捕获的参数（`_f=f`）当成查询参数，而 Feature 不是可注入类型。
Starlette 的 Route 直接 `endpoint(request)`，不看签名，所以用闭包捕获功能定义即可。
（踩过的坑：曾想用「单一 dispatcher + `scope["route"].name` 回查 registry」，
但 plain Route 不会往 scope 里写 `route`，实测拿到 None，全部 404。）

注意：这批 Route 不进 FastAPI 的 OpenAPI schema（已定案不做：接口清单的唯一真相是 REGISTRY，前端启动时拉 /api/v1/meta/features，静态 OpenAPI 只会过期；见开发文档 §22
add_api_route）。当前接口清单由 `/api/v1/meta/features` 提供，契约测试也读它。

只监听 127.0.0.1，随机端口，端口与一次性 token 写入 http.json（§9.2）。
"""
from __future__ import annotations

import json
import sys
import uuid
import ipaddress
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from qingdan import __version__
from qingdan.core.registry import QdError, REGISTRY, call
from qingdan.core import access
from qingdan.api.rate_limit import RateLimiter
_TOKEN_LIMITER = RateLimiter()

# 请求体里允许直接透传的键；其余保留键忽略
_RESERVED = {"_feature"}


def _browser_request_allowed(request: Request) -> bool:
    """本机脚本可直连；浏览器必须来自当前本机页面，拒绝跨站写入和域名重绑定。"""
    origin = request.headers.get("origin")
    fetch_site = request.headers.get("sec-fetch-site")
    if origin is None and fetch_site is None:
        return True
    host = request.headers.get("host", "")
    try:
        if urlsplit("//" + host).hostname not in {"127.0.0.1", "localhost", "::1"}:
            return False
    except ValueError:
        return False
    expected = f"{request.scope.get('scheme', 'http')}://{host}"
    if origin is not None and origin.rstrip("/").lower() != expected.lower():
        return False
    return fetch_site != "cross-site" or request.method in {"GET", "HEAD", "OPTIONS"}


def _ok(data: Any) -> JSONResponse:
    return JSONResponse({"ok": True, "data": data, "trace_id": uuid.uuid4().hex[:8]})


def _err(code: str, message: str, status: int, detail: dict | None = None) -> JSONResponse:
    return JSONResponse(
        {"ok": False, "error": {"code": code, "message": message, "detail": detail or {}},
         "trace_id": uuid.uuid4().hex[:8]},
        status_code=status)


async def _run(f, request: Request) -> JSONResponse:
    request.state.feature = f.name
    # 先收 query/body，再用路径参数覆盖：**路径参数必须有最终决定权**。
    # 反过来时，`/alert/channels/{name}/enable` 会被同名 query 劫持，
    # 而调用方完全看不出来（实测踩过）。
    raw: dict[str, Any] = {}
    if request.method in ("GET", "DELETE"):
        raw.update(dict(request.query_params))
    else:
        if request.app.state.remote_access:
            content = bytearray()
            maximum = request.state.access_config['max_body_kb'] * 1024
            async for chunk in request.stream():
                content.extend(chunk)
                if len(content)>maximum:
                    return _err('BODY_TOO_LARGE','请求体超过远程接口大小限制',413)
        else:
            content = await request.body()
        if content:
            try:
                body = json.loads(content)
            except (json.JSONDecodeError, UnicodeDecodeError):
                return _err("VALIDATION_ERROR", "请求体必须是有效的 JSON 对象", 422)
            if not isinstance(body, dict):
                return _err("VALIDATION_ERROR", "请求体必须是 JSON 对象", 422)
            raw.update({k: v for k, v in body.items() if k not in _RESERVED})
    raw.update(dict(request.path_params))

    try:
        if request.app.state.remote_access:
            request.state.api_principal = access.require_permission(request.state.api_principal, f.name, raw)
        return _ok(call(f.name, **raw))
    except QdError as e:
        return _err(e.code, e.message, e.http_status, e.detail)
    except ValueError as e:
        return _err("VALIDATION_ERROR", str(e), 422)
    except Exception as e:  # noqa: BLE001 —— 不吞类型，转成可诊断的错误码
        if request.app.state.remote_access:
            return _err('INTERNAL','服务器内部错误',500)
        return _err("INTERNAL", f"{type(e).__name__}: {e}", 500)


def _make_handler(f):
    """闭包捕获 Feature。

    Starlette 的 `Route` 不解析 endpoint 签名（不像 FastAPI 会做依赖注入），
    所以这里可以安全地用闭包，不必把 f 塞进函数参数里。
    """
    async def handler(request: Request) -> JSONResponse:
        return await _run(f, request)

    handler.__name__ = f.name.replace(".", "_")
    return handler


def web_root() -> Path:
    """前端资源目录，兼容源码运行与 PyInstaller 冻结运行。"""
    if getattr(sys, "frozen", False):                 # PyInstaller：datas 里的 qingdan/web
        return Path(getattr(sys, "_MEIPASS", ".")) / "qingdan" / "web"
    return Path(__file__).resolve().parents[1] / "web"


def _route_order(path: str) -> tuple[int, int, str]:
    """静态路径优先于参数化路径注册。

    Starlette 按注册顺序匹配，`/views/{name}` 若先注册就会把 `/views/counts` 吃掉
    （实测踩过）。靠「记得调整义顺序」不可靠，所以在挂载时按具体度排序。
    """
    return (path.count("{"), len(path), path)


def build_app(*, remote: bool = False) -> FastAPI:
    app = FastAPI(title="qingdan API", version=__version__, docs_url=None, redoc_url=None)
    app.state.remote_access = remote
    limiter = RateLimiter()

    @app.middleware("http")
    async def no_store(request, call_next):
        """所有响应 no-store。

        服务只监听 127.0.0.1，缓存省不了多少流量，却会稳定制造「改了前端文件但页面
        还是旧行为」这种最难查的问题（改 store.js 后浏览器仍跑旧版本，实测踩过）。
        """
        principal = None
        peer = request.client.host if request.client else ''
        if remote:
            try:
                cfg = access.get_config()
                request.state.access_config = cfg
                if not limiter.allow(peer,cfg['requests_per_minute']):
                    raise QdError('RATE_LIMITED','远程请求过于频繁，请稍后重试',429)
                authorization = request.headers.get('authorization','').split()
                if len(authorization)!=2 or authorization[0].lower()!='bearer':
                    raise QdError('API_TOKEN_REQUIRED','远程接口需要 Authorization: Bearer Token',401)
                principal = access.authenticate(authorization[1],peer)
                request.state.api_principal = principal
                if not _TOKEN_LIMITER.allow(principal['id'],principal['max_qps'],window=1):
                    raise QdError('TOKEN_RATE_LIMITED','Token 超过 QPS 限制',429)
                access.reserve_call(principal['id'],peer)
                response = await call_next(request)
            except QdError as e:
                response = _err(e.code,e.message,e.http_status,e.detail)
            if response.status_code==401:
                response.headers['WWW-Authenticate']='Bearer'
            if response.status_code==429:
                response.headers['Retry-After']='60'
            access.audit(getattr(request.state,'api_principal',principal),peer,getattr(request.state,'feature',None),request.method,response.status_code)
        else:
            try:
                local_peer = peer == 'testclient' or ipaddress.ip_address(peer).is_loopback
            except ValueError:
                local_peer = False
            if not local_peer:
                response = _err('LOCAL_ONLY','本机通道不接受远程连接',403)
            elif not _browser_request_allowed(request):
                response = _err("ORIGIN_DENIED", "仅允许本机页面调用此接口", 403)
            else:
                response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        return response

    for f in sorted(REGISTRY.values(), key=lambda x: _route_order(x.http[1])):
        method, path = f.http
        app.router.routes.append(Route(path, _make_handler(f), methods=[method], name=f.name))

    @app.get("/api/v1/meta/features")
    def features(request: Request) -> JSONResponse:
        """接口自检表：注册表 ↔ 接口的对照，供契约测试、前端路由推导与排障使用。"""
        items={}
        for name,f in sorted(REGISTRY.items()):
            if remote and not (access.can_use(request.state.api_principal,name)
                               or (name=='task.quick_add' and access.can_use(request.state.api_principal,name,{'create':True}))):
                continue
            required=access.policy(name)
            permissions=sorted(required) if required is not None else []
            if name=='task.quick_add': permissions=['read','create']
            items[name]={"http":list(f.http),"cli":f.cli,"needs_auth":f.needs_auth,
                         "undoable":f.undoable,"params":list(f.params),
                         'remote_allowed':required is not None,
                         'permissions':permissions,'conditional':name=='task.quick_add'}
        return _ok(items)

    # 静态资源必须**最后**挂载：Starlette 按注册顺序匹配，Mount("/") 在前会吃掉 /api/v1/*。
    root = web_root()
    if not remote and root.is_dir():
        app.router.routes.append(Mount("/", app=StaticFiles(directory=str(root), html=True)))
    return app
