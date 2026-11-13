"""告警接口层（M3 / A7）。每个功能都有接口，R4。

注意：`alert.config_get` 返回的是**脱敏**配置，密钥只给「是否已设置 + 掩码」；
明文密钥只在适配器内部通过 `config.get_config()` 取，不出接口。
"""
from __future__ import annotations

import time
from datetime import datetime

from qingdan.core import stamp, view
from qingdan.core.alerting import config as acfg
from qingdan.core.alerting import scheduler
from qingdan.core.alerting.base import get_channel, CHANNELS
from qingdan.core.context import ctx
from qingdan.core.registry import feature, not_found, validation_error


def _need_channel(name: str):
    ch = get_channel(name)
    if ch is None:
        raise validation_error("未知渠道", allowed=sorted(CHANNELS), got=name)
    return ch


@feature(name='alert.notification_action', group='alert',
         http=('POST','/api/v1/alert/notification-action'), undoable=True)
def notification_action(item_id: str, action: str = 'open',
                        expected_rev: dict | None = None, minutes: int = 10) -> dict:
    """Local notification callback; stale versions can open, never mutate."""
    if action not in ('open','complete','snooze'):
        raise validation_error('action 只能是 open|complete|snooze')
    if action != 'open':
        if (not isinstance(expected_rev,dict) or set(expected_rev) != {'ms','seq','dev'}
                or type(expected_rev['ms']) is not int or type(expected_rev['seq']) is not int
                or not isinstance(expected_rev['dev'],str) or not expected_rev['dev']):
            raise validation_error('修改通知任务必须提供完整 expected_rev')
    if action == 'snooze' and not 1 <= minutes <= 1440:
        raise validation_error('minutes 需在 1..1440')
    item = ctx.need_store().get(item_id)
    if item is None or item.deleted or item.kind not in ('task','countdown'):
        return {'status':'gone','item_id':item_id}
    result = {'item_id':item_id,'kind':item.kind,'revision':item.rev.to_json()}
    if action == 'open':
        return {**result,'status':'open'}
    if item.kind != 'task':
        return {**result,'status':'unsupported'}
    if item.data.get('status') in ('done','cancelled'):
        return {**result,'status':'inactive'}
    if item.rev.to_json() != expected_rev:
        return {**result,'status':'stale'}
    # registry.call owns operation_lock across the check and the direct service
    # call. Do not nest registry.call: that would replace the undo preimage.
    from qingdan.core.service import task_complete, task_snooze
    changed = task_complete(item_id) if action == 'complete' else task_snooze(item_id,minutes)
    return {**result,'revision':ctx.need_store().get(item_id).rev.to_json(),
            'status':'completed' if action == 'complete' else 'snoozed',
            'rolled':bool((changed.get('repeat_roll') or {}).get('rolled'))}


@feature(name="alert.status", group="alert", http=("GET", "/api/v1/alert/status"), idempotent=True)
def alert_status() -> dict:
    """当前生效状态：待触发数、各渠道配置/开关情况、最近一次扫描结果。"""
    full = acfg.get_config()
    cfg = acfg.get_public_config()
    now_ms = stamp.now_ms()          # 与写入侧同源，否则 pending 数会随真实时钟漂
    channels = []
    for name in acfg.CHANNELS:
        ch = get_channel(name)
        channels.append({"name": name,
                         "registered": ch is not None,
                         "enabled": bool(cfg["channels"].get(name)),
                         "configured": bool(ch and ch.is_configured()),
                         "backend": getattr(ch, "backend", None)})
    return {"master": cfg["master"], "silence_until": cfg["silence_until"],
            "quiet_hours": cfg["dnd"], "pending": ctx.need_store().alert_pending_count(now_ms),
            "pending_total": ctx.need_store().alert_pending_count(),
            "last_scan": scheduler.scheduler.last, "channels": channels,
            # DPAPI 密文绑定本机+当前用户；换机后解不开，必须说清是「解不开」而不是「没填」
            "secret_errors": full.get("_secret_errors") or [],
            "synced": False}


@feature(name="alert.config_get", group="alert", http=("GET", "/api/v1/alert/config"),
         idempotent=True)
def alert_config_get() -> dict:
    return acfg.get_public_config()


@feature(name="alert.config_update", group="alert", http=("PATCH", "/api/v1/alert/config"),
         undoable=False)
def alert_config_update(config: dict) -> dict:
    if not isinstance(config, dict) or not config:
        raise validation_error("config 必须是非空对象")
    try:
        out = acfg.update_config(config)
    except ValueError as e:
        raise validation_error(str(e)) from e
    scheduler.rebuild_all()          # 渠道开关一变，作业集合就得重算
    return out


@feature(name="alert.channel_enable", group="alert",
         http=("POST", "/api/v1/alert/channels/{name}/enable"), undoable=False)
def alert_channel_enable(name: str, on: bool = True) -> dict:
    _need_channel(name)
    try:
        return acfg.set_channels({name: bool(on)})
    except ValueError as e:
        raise validation_error(str(e)) from e


@feature(name="alert.channel_test", group="alert",
         http=("POST", "/api/v1/alert/channels/{name}/test"), idempotent=False)
def alert_channel_test(name: str) -> dict:
    """每个渠道都必须能单独测一次。配置错了要等到期才发现，代价太大。"""
    ch = _need_channel(name)
    if not ch.is_configured():
        raise not_found(f"{name} 渠道还没配置完整")
    result = ch.test()
    # 日志明细用 ASCII：`测试：` 这种后端自产中文会跟着响应体进英文界面。
    # 渠道自己的原因（result.message，可能来自钉钉 errmsg）保留 —— 那是外部数据，不是我们的文案。
    ctx.need_store().alert_log_add(name, "info" if result.ok else "error",
                                   f"test {result.code or 'ok'} {result.message}".strip())
    return result.as_dict()


@feature(name="alert.log", group="alert", http=("GET", "/api/v1/alert/log"), idempotent=True)
def alert_log(limit: int = 100) -> dict:
    rows = ctx.need_store().alert_log_list(limit)
    return {"total": len(rows), "items": rows}


@feature(name="alert.silence", group="alert", http=("POST", "/api/v1/alert/silence"),
         undoable=True)
def alert_silence(minutes: int = 30, until: str | None = None) -> dict:
    """临时静音。minutes 与 until 二选一。"""
    if until:
        dt = view.parse_dt(until)
        if dt is None:
            raise validation_error("until 必须是 ISO8601", got=until)
        ts = int(dt.timestamp() * 1000)
    else:
        if not 1 <= int(minutes) <= 24 * 60:
            raise validation_error("minutes 需在 1..1440", got=minutes)
        ts = stamp.now_ms() + int(minutes) * 60_000
    acfg.update_config({"silence_until": ts})
    return {"silence_until": ts,
            "until": datetime.fromtimestamp(ts / 1000).isoformat(timespec="seconds")}


@feature(name="alert.preview", group="alert", http=("GET", "/api/v1/alert/preview"),
         idempotent=True)
def alert_preview(hours: int = 24) -> dict:
    """干跑：列出接下来会发什么，不真发。用户配完告警最需要知道的就是这个。"""
    if not 1 <= int(hours) <= 168:
        raise validation_error("hours 需在 1..168", got=hours)
    st = ctx.need_store()
    now_ms = stamp.now_ms()
    horizon = now_ms + int(hours) * 3_600_000
    rows = st.db.execute(
        "SELECT item_id, fire_at, channel FROM alert_job WHERE status='pending' "
        "AND fire_at>=? AND fire_at<=? ORDER BY fire_at LIMIT 200",
        (now_ms, horizon)).fetchall()
    items = []
    for r in rows:
        it = st.get(r["item_id"])
        items.append({"item_id": r["item_id"],
                      "title": (it.data.get("title") if it else None) or "(已删除)",
                      "fire_at": datetime.fromtimestamp(r["fire_at"] / 1000).isoformat(timespec="minutes"),
                      "channel": r["channel"]})
    return {"window_hours": int(hours), "total": len(items), "items": items}


@feature(name="alert.rebuild", group="alert", http=("POST", "/api/v1/alert/rebuild"))
def alert_rebuild() -> dict:
    return scheduler.rebuild_all()


@feature(name="alert.tick", group="alert", http=("POST", "/api/v1/alert/tick"))
def alert_tick() -> dict:
    """手动跑一轮扫描。测试与「立即检查」按钮用。"""
    return scheduler.scheduler.run_once()
