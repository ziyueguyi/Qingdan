from __future__ import annotations

import pytest

import qingdan.bootstrap  # noqa: F401
from qingdan.core import context, gitee


class FakeGitee:
    """假 Gitee API。契约测试会挨个调用每个功能接口，其中包括 auth.login ——
    没有这层替换就会拿假令牌去打真实 Gitee。测试必须不出网。"""

    def __init__(self):
        self.repos: dict[str, dict] = {}
        self.calls: list[tuple[str, str]] = []
        #: 假服务器比本机快多少毫秒。默认 0 = 一台时钟健康的服务器。
        #: 以前这里回的是一个写死的 `Thu, 05 Nov 2026 06:00:00 GMT` ——
        #: 那等于把"今天"钉在 11 月 5 日，日历一走，学出来的 offset 就是一天，
        #: 于是所有走 offset 守卫的推送测试在没人改代码的情况下集体变红。
        #: 需要造时钟偏差的测试请显式改这个字段，别靠日期巧合。
        self.skew_ms = 0

    def _date_header(self) -> str:
        from email.utils import format_datetime
        from datetime import datetime, timedelta, timezone

        when = datetime.now(timezone.utc) + timedelta(milliseconds=self.skew_ms)
        return format_datetime(when, usegmt=True)

    def _resp(self, status, body, extra: dict | None = None):
        """真实 HTTP 服务每个响应都带 Date，所以校准在**任何一次**调用上都会发生。

        原来只在 `/user` 上给 Date 头，其余响应没有 —— 于是"随便调一个接口就该重新
        校准"这种行为在这儿根本测不到，测试还是一片绿。假件偷懒的地方，就是覆盖率的洞。
        """
        headers = {"Date": self._date_header()}
        headers.update(extra or {})
        return status, body, headers

    def __call__(self, method, url, kwargs):
        self.calls.append((method, url))
        path = url[len(gitee.BASE):]
        params = kwargs.get("params") or {}
        if params.get("access_token") in (None, "", "bad-token"):
            return self._resp(401, {"message": "unauthorized"})
        if path == "/user":
            return self._resp(200, {"id": 1, "login": "tester", "name": "Tester"})
        if path.startswith("/repos/"):
            key = path[len("/repos/"):]
            return self._resp(200, self.repos[key]) if key in self.repos \
                else self._resp(404, {"message": "Not Found"})
        if path == "/user/repos" and method == "POST":
            data = kwargs.get("data") or {}
            name = data.get("name")
            info = {"full_name": f"tester/{name}", "private": data.get("private") == "true",
                    "public": data.get("private") != "true", "default_branch": "master"}
            self.repos[f"tester/{name}"] = info
            return self._resp(201, info)
        return self._resp(404, {"message": f"unhandled {method} {path}"})


@pytest.fixture(autouse=True)
def fake_gitee():
    """所有测试默认用假 Gitee；需要断言建仓/哨兵行为时直接用它的 .repos。"""
    fake = FakeGitee()
    old = gitee.TRANSPORT
    gitee.TRANSPORT = fake
    yield fake
    gitee.TRANSPORT = old


@pytest.fixture(autouse=True)
def app_ctx(tmp_path):
    """每个测试一套独立临时库。

    autouse 是刻意的：早期版本要求测试显式声明这个 fixture，新加的测试文件
    忘了写就报「上下文未初始化」，一次挂了 57 个。隔离应当是默认行为，不是 opt-in。
    """
    c = context.init(tmp_path / "data", device_id="D-test")
    yield c
    context.shutdown()


@pytest.fixture
def store(app_ctx):
    assert app_ctx.store is not None
    return app_ctx.store


@pytest.fixture
def logged_in(app_ctx, fake_gitee):
    """已登录 + 仓库就绪的状态，供同步类测试使用。"""
    from qingdan.core.registry import call
    call("auth.login", token="good-token")
    call("repo.ensure", name="qd-data")
    return app_ctx
