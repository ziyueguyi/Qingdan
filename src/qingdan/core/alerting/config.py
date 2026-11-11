"""告警配置（M3 / A1）。

配置分两类存放：
  · **密钥**（webhook 里的 access_token、加签 secret、SMTP 授权码）→ DPAPI 密文进 `secret` 表
  · 非密钥（服务商、发件地址、收件人、开关、免打扰时段）→ 普通 `settings` 表

两类都属**设备本地**数据，不进 git 同步（开发文档 §11.5）：A 机配的 webhook
不该同步到 B 机重复推送。
"""
from __future__ import annotations

import copy
from typing import Any

from qingdan.core import secrets
from qingdan.core.context import ctx

CFG_KEY = "alert_config"
CHANNELS = ("toast", "dingtalk", "email")

DEFAULTS: dict[str, Any] = {
    "master": True,                      # 全局总开关
    "channels": {c: (c == "toast") for c in CHANNELS},   # 只有本地通知默认开
    "dnd": {"enabled": True, "start": "23:00", "end": "07:30"},
    "digest": {"enabled": False, "time": "08:30"},
    "sticky_default": False,
    "silence_until": 0,                  # 临时静音到的绝对毫秒
    "smtp": {"preset": "qq", "host": "smtp.qq.com", "port": 465, "use_ssl": True,
             "from_addr": "", "to_addrs": []},
    "dingtalk": {"at_mobiles": [], "at_all": False},
    # 系统通知后端。Windows 对**非打包应用**（没有 MSIX/AUMID 快捷方式）发的 toast
    # 会静默丢弃且不报错，所以默认用对话框；M6 打好包注册 AUMID 后再切 "toast"。
    "toast": {"backend": "dialog"},
}

TOAST_BACKENDS = ("auto", "toast", "dialog")

SMTP_PRESETS = {
    "qq": {"host": "smtp.qq.com", "port": 465, "use_ssl": True},
    "163": {"host": "smtp.163.com", "port": 465, "use_ssl": True},
    "gmail": {"host": "smtp.gmail.com", "port": 465, "use_ssl": True},
}

SECRET_KEYS = {"dingtalk_webhook", "dingtalk_secret", "smtp_auth_code"}


def _merge(base: dict, over: dict) -> dict:
    """深拷贝 base 再合并。

    这里曾经是 `dict(base)` 浅拷贝 —— 当 `over` 为空时，返回的 `out["channels"]`
    和模块级 `DEFAULTS["channels"]` 是**同一个对象**，于是第一次关闭渠道就永久
    改写了进程内的默认值（后来新建的库也会继承错的默认开关）。
    """
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def get_config() -> dict:
    """完整配置，**密钥已解密**。只允许适配器内部调用，不得直接返回给接口层。

    曾经这里直接把 DPAPI 密文（`dpapi:...`）交出去，适配器拿它去判断
    `startswith("https://oapi.dingtalk.com")` 永远为 False —— 配好了也显示未配置。
    只有真发送才会暴露，单元测试用假配置全打不通。
    """
    stored = ctx.need_store().state_get(CFG_KEY) or {}
    cfg = _merge(DEFAULTS, stored)
    sec: dict[str, str] = {}
    broken: list[str] = []
    for k in SECRET_KEYS:
        raw = ctx.need_store().secret_get(k) or ""
        if not raw:
            sec[k] = ""
            continue
        try:
            sec[k] = secrets.unprotect(raw)
        except secrets.SecretsUnavailable:
            # 换机器/换 Windows 用户后 DPAPI 密文解不开：视为未配置，让 UI 提示重填
            sec[k] = ""
            broken.append(k)
    cfg["_secrets"] = sec
    cfg["_secret_errors"] = broken
    return cfg


def get_public_config() -> dict:
    """给接口层/UI 的配置 —— 密钥一律脱敏，绝不回传明文。"""
    cfg = get_config()
    sec = cfg.pop("_secrets")
    cfg["secrets_set"] = {k: bool(v) for k, v in sec.items()}
    cfg["secrets_mask"] = {k: secrets.mask(v) for k, v in sec.items()}
    return cfg


def set_channels(channels: dict[str, bool]) -> dict:
    st = ctx.need_store()
    cfg = _merge(DEFAULTS, st.state_get(CFG_KEY) or {})
    for name, on in (channels or {}).items():
        if name not in CHANNELS:
            raise ValueError(f"未知渠道：{name}（允许 {list(CHANNELS)}）")
        cfg["channels"][name] = bool(on)
    st.state_set(CFG_KEY, cfg)
    return get_public_config()


def update_config(patch: dict) -> dict:
    """部分更新。密钥走 `secrets` 子字典，单独加密存放。"""
    st = ctx.need_store()
    if not isinstance(patch, dict) or not patch:
        raise ValueError("配置不能为空")
    cfg = _merge(DEFAULTS, st.state_get(CFG_KEY) or {})

    new_secrets = patch.pop("secrets", None) or {}
    unknown_sec = set(new_secrets) - SECRET_KEYS
    if unknown_sec:
        raise ValueError(f"未知密钥位：{sorted(unknown_sec)}")
    for k, v in new_secrets.items():
        st.secret_set(k, secrets.protect(v or ""))

    unknown = set(patch) - set(DEFAULTS) - {"secrets"}
    if unknown:
        raise ValueError(f"不支持的配置项：{sorted(unknown)}")
    if "master" in patch:
        cfg["master"] = bool(patch["master"])
    if "sticky_default" in patch:
        cfg["sticky_default"] = bool(patch["sticky_default"])
    if "silence_until" in patch:
        cfg["silence_until"] = int(patch["silence_until"])
    if "channels" in patch:
        set_channels(patch["channels"])
        cfg = _merge(DEFAULTS, st.state_get(CFG_KEY) or {})
    for section in ("dnd", "digest", "smtp", "dingtalk", "toast"):
        if section in patch:
            cfg[section] = _merge(DEFAULTS[section], patch[section])
    if "toast" in patch and cfg["toast"].get("backend") not in TOAST_BACKENDS:
        raise ValueError(f"toast.backend 只能是 {list(TOAST_BACKENDS)}")
    if "smtp" in patch and patch["smtp"].get("preset"):
        cfg["smtp"] = _merge(cfg["smtp"], SMTP_PRESETS.get(patch["smtp"]["preset"], {}))
    _validate(cfg)
    st.state_set(CFG_KEY, cfg)
    return get_public_config()


def _validate(cfg: dict) -> None:
    for label in ("dnd",):
        for key in ("start", "end"):
            _hhmm(cfg[label][key], f"免打扰{key}")
    if cfg["digest"].get("time"):
        _hhmm(cfg["digest"]["time"], "摘要时间")
    if not isinstance(cfg["master"], bool):
        raise ValueError("master 必须是布尔")


def _hhmm(value: str, what: str) -> tuple[int, int]:
    parts = str(value).split(":")
    if len(parts) != 2 or not all(p.isdigit() for p in parts):
        raise ValueError(f"{what} 必须是 HH:MM，收到 {value!r}")
    h, m = int(parts[0]), int(parts[1])
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise ValueError(f"{what} 超出范围：{value}")
    return h, m


def in_quiet_hours(when_minutes: int, start: str, end: str) -> bool:
    """免打扰区间判定，支持跨零点（23:00 → 07:30）。"""
    sh, sm = _hhmm(start, "开始")
    eh, em = _hhmm(end, "结束")
    s, e = sh * 60 + sm, eh * 60 + em
    return s <= when_minutes < e if s <= e else (when_minutes >= s or when_minutes < e)


def is_globally_muted(now_ms: int) -> bool:
    cfg = get_config()
    return (not cfg["master"]) or now_ms < int(cfg.get("silence_until") or 0)
