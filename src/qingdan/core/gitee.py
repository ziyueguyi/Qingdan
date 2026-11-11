"""Gitee REST v5 客户端（M4）。

只负责三件事：验令牌、查仓库、建仓库。**同步通道走 git（dulwich），不走 API** ——
Gitee 没有批量提交接口，逐项调 contents API 会产生 N 次请求且拿不到 merge base。

安全约束：
  · 令牌从不在返回值里出现，也不进异常信息；
  · 传输层可注入，测试不打真实网络。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Callable

BASE = "https://gitee.com/api/v5"

#: 测试钩子：设了它，所有 GiteeClient 都走这里，**不会出网**。
#: 契约测试会挨个调用每个功能接口，其中包括 auth.login —— 没有这个钩子就会拿
#: 假令牌去打真实 Gitee API。
TRANSPORT: Callable | None = None


class GiteeError(RuntimeError):
    def __init__(self, code: str, message: str, status: int = 0, detail: dict | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.detail = detail or {}


class GiteeClient:
    def __init__(self, token_getter: Callable[[], str],
                 transport: Callable[[str, str, dict], tuple[int, Any, dict]] | None = None,
                 on_offset: Callable[[int], None] | None = None):
        """transport(method, url, kwargs) -> (status, parsed_body, headers)

        `on_offset` 在**每次收到响应**时立刻拿到算好的偏差。不能等调用方事后再来读
        `last_offset_ms`：上层每次都是新建的 client（`_client()`），新对象的初值是 0，
        于是"事后读一下"的写法永远读到 0，校准静默失效（实测就是这样漏掉的）。
        """
        self._token = token_getter
        self._transport = transport or TRANSPORT or self._default_transport
        self.on_offset = on_offset
        self.last_offset_ms = 0

    @staticmethod
    def _default_transport(method, url, kwargs):
        import httpx
        r = httpx.request(method, url, timeout=20, **kwargs)
        try:
            body: Any = r.json()
        except (json.JSONDecodeError, ValueError):
            body = r.text
        return r.status_code, body, dict(r.headers)

    def _call(self, method: str, path: str, params: dict | None = None,
              data: dict | None = None) -> Any:
        token = self._token()
        if not token:
            raise GiteeError("AUTH_REQUIRED", "尚未配置 Gitee 令牌", 401)
        url = f"{BASE}{path}"
        query = {"access_token": token, **(params or {})}
        kwargs: dict[str, Any] = {"params": query}
        if data is not None:
            kwargs["data"] = data
        status, body, headers = self._transport(method, url, kwargs)
        self._note_clock(headers)
        if status in (401, 403):
            raise GiteeError("TOKEN_INVALID",
                             "令牌无效或权限不足（需要 projects scope）。"
                             "常见原因：改过密码、开启两步验证、或勾的 scope 不对", status)
        if status == 404:
            return None
        if status >= 400:
            msg = body.get("message") if isinstance(body, dict) else str(body)
            raise GiteeError("GITEE_ERROR", f"Gitee 返回 {status}：{msg}", status)
        return body

    def _note_clock(self, headers: dict) -> None:
        """用响应的 Date 头估算本机时钟偏差，供 HLC 校正。"""
        raw = headers.get("Date") or headers.get("date")
        if not raw:
            return
        try:
            server = datetime.strptime(raw, "%a, %d %b %Y %H:%M:%S GMT").replace(tzinfo=timezone.utc)
        except ValueError:
            return
        self.last_offset_ms = int((server - datetime.now(timezone.utc)).total_seconds() * 1000)
        if self.on_offset is not None:
            self.on_offset(self.last_offset_ms)

    # -- 身份 --------------------------------------------------------------
    def whoami(self) -> dict:
        me = self._call("GET", "/user")
        if not isinstance(me, dict) or "login" not in me:
            raise GiteeError("TOKEN_INVALID", "令牌无法读取用户信息", 401)
        return {"login": me["login"], "id": me.get("id"), "name": me.get("name")}

    # -- 仓库 --------------------------------------------------------------
    @staticmethod
    def _normalize(info: dict) -> dict:
        return {
            "full_name": info.get("full_name"),
            "private": bool(info.get("private")),
            "public": bool(info.get("public")),
            "default_branch": info.get("default_branch") or "master",
            "size_kb": info.get("size"),
        }

    def repo(self, owner: str, name: str) -> dict | None:
        """返回仓库信息；不存在返回 None（Gitee 对私有仓的匿名/无权访问也是 404）。"""
        info = self._call("GET", f"/repos/{owner}/{name}")
        return self._normalize(info) if isinstance(info, dict) else None

    def create_repo(self, name: str, description: str = "", private: bool = True) -> dict:
        body = self._call("POST", "/user/repos", data={
            "name": name,
            "private": "true" if private else "false",
            "description": description,
            "has_issues": "false",
            "has_wiki": "false",
        })
        if not isinstance(body, dict):
            raise GiteeError("GITEE_ERROR", "建仓返回异常")
        # 必须走同一个 _normalize：曾经这里只挑了 full_name/private，
        # 把 default_branch 丢了，调用方紧接着读它就 KeyError。
        return self._normalize(body)

    def push_url(self, owner: str, name: str) -> str:
        """带凭据的 git 远端地址。

        形式经 M4 spike 验证：`https://{login}:{token}@gitee.com/{owner}/{name}.git`。
        **调用方不得把这个返回值写进日志** —— dulwich 自己会打印它（已知泄漏点）。
        """
        return f"https://{owner}:{self._token()}@gitee.com/{owner}/{name}.git"
