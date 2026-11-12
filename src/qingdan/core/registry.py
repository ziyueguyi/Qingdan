"""功能契约层 —— 落实 R4「每做一个功能必须新增该功能接口」。

一个 `@feature` 装饰器同时生成三种入口：Python 函数、HTTP 路由、CLI 子命令。
**手写 FastAPI 路由一律禁止**：接口由 registry 单点派生，漏接口会在 tests/test_contract.py 直接红。

强约束（在注册时就检查，不靠事后测试）：
  · `http` 必须显式给出，否则注册失败 —— 想不出接口说明功能还没设计清楚；
  · 功能名必须是 `组.动作` 两段式，全局唯一。
"""
from __future__ import annotations

import inspect
from contextlib import nullcontext
import sys
from dataclasses import dataclass, field
from typing import Any, Callable, get_type_hints

# --------------------------------------------------------------------------- 异常


class QdError(Exception):
    """统一业务异常。错误码表见开发文档 §9.4。"""

    def __init__(self, code: str, message: str, http_status: int = 400, detail: dict | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status
        self.detail = detail or {}

    def as_dict(self) -> dict:
        return {"code": self.code, "message": self.message, "detail": self.detail}


def validation_error(message: str, **detail) -> QdError:
    return QdError("VALIDATION_ERROR", message, 422, detail)


def not_found(what: str) -> QdError:
    return QdError("NOT_FOUND", f"{what} 不存在或已彻底删除", 404)


def auth_required(message: str = "该功能需要登录 Gitee") -> QdError:
    return QdError("AUTH_REQUIRED", message, 401)


def conflict(message: str, **detail) -> QdError:
    return QdError("CONFLICT", message, 409, detail)


# --------------------------------------------------------------------------- 注册表


@dataclass(frozen=True)
class Feature:
    name: str                       # "task.create"
    group: str                      # "task"
    func: Callable[..., Any]
    http: tuple[str, str]           # ("POST", "/api/v1/tasks")
    cli: str                        # "task create"
    undoable: bool = False
    needs_auth: bool = False
    idempotent: bool = False
    doc: str = ""
    params: tuple[str, ...] = ()
    manages_lock: bool = False      # Network operations protect their own short mutations.

    @property
    def action(self) -> str:
        return self.name.split(".", 1)[1]


REGISTRY: dict[str, Feature] = {}


def feature(
    *,
    name: str,
    group: str,
    http: tuple[str, str] | None = None,
    undoable: bool = False,
    needs_auth: bool = False,
    idempotent: bool = False,
    manages_lock: bool = False,
) -> Callable:
    """把一个函数登记为「功能」。缺 http 直接报错，这是 R4 的制度保障。"""

    def deco(fn: Callable) -> Callable:
        if name.count(".") != 1:
            raise ValueError(f"功能名必须是 组.动作 两段式，当前：{name}")
        if name in REGISTRY:
            raise ValueError(f"功能名重复注册：{name}")
        if http is None:
            raise ValueError(f"功能 {name} 未声明 http 接口。R4 要求做一个功能就必须有它的接口。")
        method, path = http
        if method.upper() not in {"GET", "POST", "PATCH", "PUT", "DELETE"}:
            raise ValueError(f"功能 {name} 的 HTTP 方法非法：{method}")
        if not path.startswith("/api/v1/"):
            raise ValueError(f"功能 {name} 的路径必须以 /api/v1/ 开头：{path}")

        sig = inspect.signature(fn)
        for p in sig.parameters.values():
            if p.kind is inspect.Parameter.POSITIONAL_ONLY:
                raise ValueError(f"功能 {name} 的参数 {p.name} 不能是位置专用，请用关键字参数")

        REGISTRY[name] = Feature(
            name=name,
            group=group,
            func=fn,
            http=(method.upper(), path),
            cli=f"{group} {name.split('.', 1)[1]}",
            undoable=undoable,
            needs_auth=needs_auth,
            idempotent=idempotent,
            manages_lock=manages_lock,
            doc=(fn.__doc__ or "").strip(),
            params=tuple(
                p.name for p in sig.parameters.values()
                if p.kind in (p.POSITIONAL_OR_KEYWORD, p.KEYWORD_ONLY)
            ),
        )
        fn.feature_name = name          # type: ignore[attr-defined]
        return fn

    return deco


# --------------------------------------------------------------------------- 参数强制


def _unwrap(hint: Any) -> Any:
    """把 Optional[X] / X | None 还原成 X。"""
    import types
    if hint is None or hint is type(None):
        return str
    origin = getattr(hint, "__origin__", None)
    if origin is types.UnionType or origin is __import__("typing").Union:
        args = [a for a in getattr(hint, "__args__", ()) if a is not type(None)]
        return args[0] if args else str
    return hint


def _cast(value: Any, hint: Any) -> Any:
    hint = _unwrap(hint)
    if value is None or hint in (Any, None):
        return value
    if hint is bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"1", "true", "yes", "on"}:
                return True
            if normalized in {"0", "false", "no", "off"}:
                return False
            raise validation_error("期望布尔值", got=value)
        if value in (0, 1):
            return bool(value)
        raise validation_error("期望布尔值", got=repr(value))
    if hint is int:
        if isinstance(value, bool):
            raise validation_error("期望整数", got=repr(value))
        try:
            return int(value)
        except (TypeError, ValueError):
            raise validation_error("期望整数", got=repr(value)) from None
    if hint is float:
        try:
            return float(value)
        except (TypeError, ValueError):
            raise validation_error("期望数字", got=repr(value)) from None
    if hint in (list, dict):
        if isinstance(value, hint):
            return value
        if isinstance(value, str):
            import json
            try:
                parsed = json.loads(value)
            except json.JSONDecodeError:
                raise validation_error(f"期望 {hint.__name__} 的 JSON", got=value) from None
            if not isinstance(parsed, hint):
                raise validation_error(f"期望 {hint.__name__}", got=type(parsed).__name__)
            return parsed
        raise validation_error(f"期望 {hint.__name__}", got=repr(value))
    if hint is str:
        return value if isinstance(value, str) else str(value)
    return value


def bind(fn: Callable, raw: dict[str, Any]) -> dict[str, Any]:
    """把外部（HTTP body/query、CLI argv）给的原始字典按函数签名转成 kwargs。

    未知参数**必须报错**，不能静默丢弃：否则 UI 里一个拼错的字段名（比如把
    section_id 写成 section）会表现为「点了没反应」，极难定位。
    """
    hints = get_type_hints(fn)
    sig = inspect.signature(fn)
    kwargs: dict[str, Any] = {}
    accepts_extra = any(p.kind is p.VAR_KEYWORD for p in sig.parameters.values())

    for pname, p in sig.parameters.items():
        if p.kind is p.VAR_KEYWORD:
            continue
        if pname not in raw:
            if p.default is inspect.Parameter.empty:
                raise validation_error(f"缺少必填参数 {pname}")
            continue
        kwargs[pname] = _cast(raw[pname], hints.get(pname, str))

    if accepts_extra:
        for k, v in raw.items():
            kwargs.setdefault(k, v)
    else:
        unknown = sorted(set(raw) - set(kwargs))
        if unknown:
            raise validation_error("不支持的参数", unknown=unknown,
                                   allowed=sorted(sig.parameters))
    return kwargs


def call(feature_name: str, /, **raw: Any) -> Any:
    """统一调用入口。UI、CLI、HTTP 壳、测试全都走这里，保证「接口即实现」。

    `feature_name` 必须是**位置专用**参数（结尾的 `/`）：功能参数里真的有 `name`
    （group.create(name=...)），若不禁用关键字传参会和它撞车，报
    "got multiple values for argument 'name'"。
    """
    if feature_name not in REGISTRY and "qingdan.bootstrap" not in sys.modules:
        # 功能是靠 @feature 的导入副作用登记的。调用方忘了 import qingdan.bootstrap
        # 就会得到「未登记的功能」这种误导性报错，所以这里兜一次底。
        #
        # 触发条件必须是"这个功能不在"，而不是"注册表全空"——曾经用后者，结果
        # 只要有任何一个模块被单独导入过（backup 自己就注册 5 个），REGISTRY 非空
        # 就会跳过补救，照样报"未登记"。
        try:
            import qingdan.bootstrap  # noqa: F401
        except Exception as e:
            raise QdError("BOOTSTRAP_FAILED",
                          f"功能注册失败（导入 qingdan.bootstrap 出错）：{type(e).__name__}: {e}",
                          500) from e
    if feature_name not in REGISTRY:
        raise QdError("NOT_FOUND", f"未登记的功能：{feature_name}", 404)
    f = REGISTRY[feature_name]

    from qingdan.core.context import ctx
    if f.needs_auth and not ctx.authed:
        raise auth_required(f"{feature_name} 需要登录 Gitee")

    # Both listeners share one application. Serialize complete operations so two
    # partial updates cannot both load the same preimage and lose one another.
    # Access administration has its own database and must be able to stop the
    # remote server without holding the task-operation lock.
    st = ctx.store if f.group != 'access' else None
    with st.operation_lock if st is not None and not f.manages_lock else nullcontext():
        if st is not None:
            st.op_begin()
        try:
            result = f.func(**bind(f.func, raw))
        finally:
            if st is not None:
                st.op_flush(f.name if f.undoable else "")
    return result
