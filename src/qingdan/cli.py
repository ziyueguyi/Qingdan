"""CLI 壳：与 HTTP 壳同源，都由 registry 派生。`qingdan task create --title "买猫粮"`。"""
from __future__ import annotations

import argparse
import inspect
import io
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
from urllib.parse import quote
import httpx
import json
import sys
import types
import typing
from typing import Any, get_type_hints

from qingdan import __version__
import qingdan.bootstrap  # noqa: F401  （副作用：登记所有 @feature，缺它则 CLI 少一批命令）
from qingdan.core.context import ctx, init, resolve_data_dir
from qingdan.core import instance
from qingdan.core.registry import QdError, REGISTRY, Feature, call

# argparse 的记账字段，绝不能当成业务参数传给功能。
# 少列一个（比如 cmd）就会在 bind() 的严格校验下变成「不支持的参数」—— 这正是
# bind 拒绝静默丢弃的价值：它把这个 CLI 自己的 bug 暴露了出来。
_RESERVED = {"cmd", "func", "action", "data_dir", "output", "_feature", "extra", "feature_name"}


def _unwrap(hint: Any) -> Any:
    if hint is None:
        return str
    origin = getattr(hint, "__origin__", None)
    if origin is types.UnionType or origin is typing.Union:
        args = [a for a in getattr(hint, "__args__", ()) if a is not type(None)]
        return args[0] if args else str
    return hint


def _add_feature_args(ap: argparse.ArgumentParser, f: Feature) -> None:
    hints = get_type_hints(f.func)
    if any(p.kind is p.VAR_KEYWORD for p in inspect.signature(f.func).parameters.values()):
        ap.add_argument("--changes", dest="_changes", type=json.loads,
                        help="局部修改字段的 JSON 对象，例如 {\"title\":\"新标题\"}")
    for pname in f.params:
        h = _unwrap(hints.get(pname, str))
        opt = f"--{pname.replace('_', '-')}"
        if h is bool:
            ap.add_argument(opt, dest=pname, action="store_true", default=None)
            ap.add_argument(f"--no-{pname.replace('_', '-')}", dest=pname, action="store_false")
        elif h in (list, dict):
            ap.add_argument(opt, dest=pname, default=None, help="JSON 字面量")
        elif h in (int, float):
            ap.add_argument(opt, dest=pname, type=h, default=None)
        else:
            ap.add_argument(opt, dest=pname, type=str, default=None)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="qingdan", description="清单本地接口 CLI")
    p.add_argument("--version", action="version", version=__version__)
    p.add_argument("--output", help="将结果写入 UTF-8 文件（桌面程序的命令行代理使用）")
    p.add_argument("--data-dir", dest="data_dir", default=None, help="数据目录（默认为用户 LOCALAPPDATA/qingdan）")
    sub = p.add_subparsers(dest="cmd", required=True)

    by_group: dict[str, Any] = {}
    for f in REGISTRY.values():
        if f.group not in by_group:
            g = sub.add_parser(f.group, help=f"{f.group} 相关功能")
            by_group[f.group] = g.add_subparsers(dest="action", required=True)
        action = f.cli.split(" ", 1)[1]
        ap = by_group[f.group].add_parser(action, help=(f.doc.splitlines()[0] if f.doc else f.name))
        _add_feature_args(ap, f)
        ap.set_defaults(_feature=f.name)

    # 通用形态：qingdan call <feature.name> --参数 值
    cp = sub.add_parser("call", help="按功能名直接调用（脚本与自动化用）")
    cp.add_argument("feature_name", help="例如 task.create")
    cp.add_argument("extra", nargs=argparse.REMAINDER, help="--key value …")
    cp.set_defaults(_feature="__call__")
    return p


def _parse_extra(argv: list[str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    i = 0
    while i < len(argv):
        a = argv[i]
        if not a.startswith("--"):
            raise ValueError(f"无法识别的命令行参数：{a}")
        if "=" in a:
            key, val = a[2:].split("=", 1)
            key = key.replace("-", "_")
            try:
                out[key] = json.loads(val)
            except json.JSONDecodeError:
                out[key] = val
            i += 1
            continue
        key = a[2:].replace("-", "_")
        if key.startswith("no_"):
            out[key[3:]] = False
            i += 1
        elif i + 1 < len(argv) and not argv[i + 1].startswith("--"):
            val = argv[i + 1]
            try:
                out[key] = json.loads(val)
            except json.JSONDecodeError:
                out[key] = val
            i += 2
        else:
            out[key] = True
            i += 1
    return out


def _remote_call(name: str, raw: dict, status: dict, data_dir: Path):
    """已运行时只走原实例，绝不另开数据库或在请求超时后重试写入。"""
    if name not in REGISTRY:
        raise QdError("NOT_FOUND", f"未登记的功能：{name}", 404)
    port = status.get("port")
    if not isinstance(port, int) or not 1 <= port <= 65535:
        raise QdError("LOCAL_SERVICE_UNAVAILABLE", "清单正在运行，但本地服务尚未就绪，请稍后重试", 503)
    base = f"http://127.0.0.1:{port}"
    try:
        with httpx.Client(timeout=30, trust_env=False) as client:
            health = client.get(base + "/api/v1/health").json()
            directory = health.get("data", {}).get("data_dir")
            if not health.get("ok") or not directory or Path(directory).resolve() != data_dir.resolve():
                raise QdError("LOCAL_SERVICE_MISMATCH", "本地服务与指定数据目录不匹配，拒绝执行", 409)
            method, path = REGISTRY[name].http
            params = dict(raw)
            import re
            for key in re.findall(r"\{(\w+)\}", path):
                if key not in params or params[key] is None:
                    raise QdError("VALIDATION_ERROR", f"缺少必填参数 {key}", 422)
                path = path.replace("{" + key + "}", quote(str(params.pop(key)), safe=""))
            if method in {"GET", "DELETE"}:
                query = {key: json.dumps(value, ensure_ascii=False) if isinstance(value, (list, dict, bool))
                         else value for key, value in params.items() if value is not None}
                response = client.request(method, base + path, params=query)
            else:
                response = client.request(method, base + path, json=params)
            body = response.json()
            if not response.is_success or body.get("ok") is not True:
                error = body.get("error", {})
                raise QdError(error.get("code", "LOCAL_SERVICE_ERROR"),
                              error.get("message", f"HTTP {response.status_code}"),
                              response.status_code, error.get("detail"))
            return body["data"]
    except (httpx.HTTPError, ValueError) as e:
        raise QdError("LOCAL_SERVICE_UNAVAILABLE", f"本地服务请求失败，请查询当前状态后再决定是否重试：{e}", 503) from e


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    parser = build_parser()
    output = argv[argv.index("--output") + 1] if "--output" in argv and argv.index("--output") + 1 < len(argv) else None
    if output:
        captured = io.StringIO()
        try:
            with redirect_stdout(captured), redirect_stderr(captured):
                args = parser.parse_args(argv)
        except SystemExit as e:
            Path(output).write_text(captured.getvalue(), encoding="utf-8")
            return int(e.code or 0)
    else:
        args = parser.parse_args(argv)
    ns = vars(args)

    def emit(payload, error=False):
        body = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
        if output:
            Path(output).write_text(body + "\n", encoding="utf-8")
        else:
            print(body, file=sys.stderr if error else sys.stdout)

    if ns.get("cmd") == "call":
        name = ns["feature_name"]
        try:
            raw = _parse_extra(ns.get("extra") or [])
        except ValueError as e:
            emit({"ok": False, "error": {"code": "VALIDATION_ERROR", "message": str(e)}}, error=True)
            return 2
    else:
        name = ns["_feature"]
        raw = {k: v for k, v in ns.items() if k not in _RESERVED and v is not None}
        changes = raw.pop("_changes", None)
        if changes is not None:
            if not isinstance(changes, dict):
                parser.error("--changes 必须是 JSON 对象")
            if set(changes) & set(raw):
                parser.error("--changes 不得覆盖命令的显式参数")
            raw.update(changes)

    lock = instance.Instance(backend=instance.default_backend())
    local = False
    try:
        directory, _ = resolve_data_dir(ns.get("data_dir"))
        acquired = lock.acquire(directory)
        if acquired["held"]:
            local = True
            init(ns.get("data_dir"))
            result = call(name, **raw)
        else:
            result = _remote_call(name, raw, acquired.get("running") or {}, directory)
    except QdError as e:
        emit({"ok": False, "error": e.as_dict()}, error=True)
        return 2
    except Exception as e:  # noqa: BLE001
        emit({"ok": False, "error": {"code": "INTERNAL",
                                      "message": f"{type(e).__name__}: {e}"}}, error=True)
        return 3
    finally:
        if local and ctx.store is not None:
            ctx.store.close()
        lock.release()
    emit(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
