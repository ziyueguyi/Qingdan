"""凭据保护：Windows DPAPI（ctypes 直调，不引 pywin32）。

为什么不用 pywin32：多带一个编译扩展，和「体积小」直接冲突，而 DPAPI 本身
就是一个 Win32 函数，ctypes 十几行就够。

关键性质（必须在 UI 上告诉用户）：DPAPI 密文**绑定当前 Windows 用户 + 本机**。
换机器、重装系统、甚至换个用户登录，密文都解不开 —— 备份数据库带走不了密钥，
需要重新填写。这是设备本地凭据的应有行为，不是 bug。
"""
from __future__ import annotations

import base64
import ctypes
import ctypes.wintypes as wt

CRYPTPROTECT_UI_FORBIDDEN = 0x01
_DEFAULT_ENTROPY = b"qingdan.secrets.v1"


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wt.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


class SecretsUnavailable(RuntimeError):
    """DPAPI 不可用（非 Windows，或被组策略禁用）。"""


def _to_blob(raw: bytes) -> "_DataBlob":
    buf = ctypes.create_string_buffer(raw, len(raw))
    return _DataBlob(len(raw), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf


def _from_blob(blob: "_DataBlob") -> bytes:
    n = int(blob.cbData)
    return ctypes.string_at(blob.pbData, n)


def _crypt32():
    try:
        return ctypes.windll.crypt32          # type: ignore[attr-defined]
    except AttributeError:
        raise SecretsUnavailable("本机没有 crypt32（非 Windows？）") from None


def protect(plaintext: str, entropy: bytes = _DEFAULT_ENTROPY) -> str:
    """明文 → `dpapi:` 前缀的 base64 密文。空串原样返回空串。"""
    if plaintext == "":
        return ""
    api = _crypt32()
    in_blob, _keep1 = _to_blob(plaintext.encode("utf-8"))
    ent_blob, _keep2 = _to_blob(entropy)
    out_blob = _DataBlob()
    ok = api.CryptProtectData(ctypes.byref(in_blob), "qingdan",
                              ctypes.byref(ent_blob), None, None,
                              CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(out_blob))
    if not ok:
        raise SecretsUnavailable("CryptProtectData 调用失败")
    try:
        raw = _from_blob(out_blob)
    finally:
        ctypes.windll.kernel32.LocalFree(out_blob.pbData)
    return "dpapi:" + base64.b64encode(raw).decode("ascii")


def unprotect(token: str, entropy: bytes = _DEFAULT_ENTROPY) -> str:
    """`protect` 的逆操作。非 dpapi: 前缀视为明文（兼容手工调试写入的值）。"""
    if token == "":
        return ""
    if not token.startswith("dpapi:"):
        return token
    raw = base64.b64decode(token[len("dpapi:"):])
    api = _crypt32()
    in_blob, _keep1 = _to_blob(raw)
    ent_blob, _keep2 = _to_blob(entropy)
    out_blob = _DataBlob()
    ok = api.CryptUnprotectData(ctypes.byref(in_blob), None, ctypes.byref(ent_blob),
                                None, None, CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(out_blob))
    if not ok:
        raise SecretsUnavailable("CryptUnprotectData 失败：密文可能来自另一台机器或另一个用户")
    try:
        return _from_blob(out_blob).decode("utf-8")
    finally:
        ctypes.windll.kernel32.LocalFree(out_blob.pbData)


def is_secret_value(value: str | None) -> bool:
    return bool(value) and value.startswith("dpapi:")


def mask(value: str | None, keep: int = 0) -> str:
    """给 UI/日志用的脱敏形式。绝不返回明文，也不返回长度以外的信息。"""
    if not value:
        return ""
    if keep and len(value) > keep:
        return value[:keep] + "•" * 8
    return "•" * 10 + "（已设置）"
