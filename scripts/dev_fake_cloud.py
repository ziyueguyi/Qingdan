"""开发/演示用：把 Gitee 换成进程内假件，git 远端换成本地裸仓库。

为什么要这么个东西：同步是 R3 的主需求，但界面的"已登录"分支一直没法验证 ——
真令牌要留给用户、测试用的假件又只在 pytest 里生效。结果就是"接口测过了、界面没点过"，
而历史上出问题的恰恰全是界面这一层（备份按钮空操作、自启静默无反应都是这么发现的）。

用法：

    python -u scripts/dev_fake_cloud.py [数据目录]     # 默认 .devcloud

它会打印端口；浏览器或窗口壳连上去之后，用**任意非空令牌**（如 demo-token）登录，
仓库会自动"创建"，然后点 立即同步 就能看完整的推送/拉取/墓碑传播。

**这不是产品功能**：假件只在进程内，进程一退就没了，数据仍落在本机数据库里。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dulwich import porcelain                                  # noqa: E402

from qingdan.core import context, gitee                        # noqa: E402


class FakeCloud:
    """只覆盖 `_call` 用到的那部分协议：/user、/repos/{owner}/{repo}、/user/repos。

    `on` 用来在演示里改仓库可见性 —— 把 `on.public` 设 True 就能当场看到哨兵中止推送，
    这在真实 Gitee 上要手动去网页端点几下，演示成本太高。
    """

    def __init__(self, skew_ms: int = 0):
        self.repos: dict[str, dict] = {}
        self.skew_ms = skew_ms
        self.calls: list[str] = []

    def headers(self) -> dict:
        # 真实服务器每个响应都带 Date；时钟偏差靠 skew_ms 现场造
        when = datetime.now(timezone.utc) + timedelta_ms(self.skew_ms)
        return {"Date": format_datetime(when, usegmt=True)}

    def __call__(self, method, url, kwargs):
        path = url[len(gitee.BASE):]
        self.calls.append(f"{method} {path}")
        token = (kwargs.get("params") or {}).get("access_token")
        if not token or token == "bad-token":
            return 401, {"message": "unauthorized"}, self.headers()
        if path == "/user":
            return 200, {"id": 1, "login": "demo", "name": "本地演示"}, self.headers()
        if path.startswith("/repos/"):
            key = path[len("/repos/"):]
            if key in self.repos:
                return 200, self.repos[key], self.headers()
            return 404, {"message": "Not Found"}, self.headers()
        if path == "/user/repos" and method == "POST":
            name = (kwargs.get("data") or {}).get("name")
            info = {"full_name": f"demo/{name}", "private": True, "public": False,
                    "default_branch": "master"}
            self.repos[f"demo/{name}"] = info
            return 201, info, self.headers()
        return 404, {"message": f"unhandled {method} {path}"}, self.headers()


def timedelta_ms(ms: int):
    from datetime import timedelta

    return timedelta(milliseconds=ms)


def make_bare(dirs: Path) -> str:
    bare = dirs / "cloud.git"
    if bare.is_dir():
        shutil.rmtree(bare, ignore_errors=True)
    porcelain.init(str(bare), bare=True)
    return str(bare)


def main() -> int:
    data_dir = sys.argv[1] if len(sys.argv) > 1 else str(ROOT / ".devcloud")
    Path(data_dir).mkdir(parents=True, exist_ok=True)
    bare = make_bare(Path(data_dir))

    cloud = FakeCloud(skew_ms=int(os.environ.get("QINGDAN_DEMO_SKEW_MS", "0")))
    gitee.TRANSPORT = cloud                      # 进程内替换，绝不改生产代码的默认值

    # 用产品自己支持的口子：repo 配置里的 `remote_url` 允许覆盖 Gitee 的推送地址
    # （sync_api 明确保留它，"测试/自建 git 的覆盖不被建仓流程冲掉"）。
    # 所以这里只预置一条配置，不去 monkeypatch 任何功能函数 —— 演示走的必须是真路径。
    from qingdan.core import context
    from qingdan.core.sync_api import REPO_STATE

    context.init(data_dir)
    stored = context.ctx.store.state_get(REPO_STATE) or {}
    stored.update({"owner": "demo", "name": stored.get("name") or "qd-data", "remote_url": bare})
    context.ctx.store.state_set(REPO_STATE, stored)
    context.ctx.store.close()                     # 让 serve 自己重开，避免两套连接并存

    print(f"[dev-cloud] 假 Gitee 已就位，git 远端 = {bare}", flush=True)
    print("[dev-cloud] 界面里用任意非空令牌登录（例如 demo-token），坏令牌用 bad-token", flush=True)
    print("[dev-cloud] 想看「时钟偏差挡住推送」：把 cloud.skew_ms 改成 600000 再重启本脚本", flush=True)

    from qingdan.__main__ import serve

    return serve(data_dir)


if __name__ == "__main__":
    raise SystemExit(main())
