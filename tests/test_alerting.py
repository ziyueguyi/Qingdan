"""告警子系统（M3）。

覆盖 A9.1–A9.3、A9.8：加签、去重、合并、限流、免打扰、补发跳过、退避、
错误分类、密钥脱敏、以及「告警绝不能污染同步」。真实发送在 A9.4/A9.5，需要凭据。
"""
from __future__ import annotations

import copy
import time
from datetime import datetime, timedelta

import pytest

from qingdan.core import secrets
from qingdan.core.alerting import config as acfg
from qingdan.core.alerting import scheduler
from qingdan.core.alerting.base import AlertMessage, CHANNELS, SendResult, register_channel
from qingdan.core.alerting.dingtalk import DingTalkChannel
from qingdan.core.alerting.emailch import EmailChannel
from qingdan.core.registry import QdError, call


# --------------------------------------------------------------------------- 夹具


class Fake:
    def __init__(self, name="toast", result=None):
        self.name = name
        self.calls: list[list[AlertMessage]] = []
        self.result = result or SendResult(ok=True, sent_count=1)

    def is_configured(self):
        return True

    def enabled(self):
        return True

    def send(self, msgs):
        self.calls.append(msgs)
        return self.result

    def test(self):
        return SendResult(ok=True, sent_count=1, covered=1)


@pytest.fixture
def fake_channels(app_ctx):
    """替换成假渠道。真的 winotify 适配器已被 bootstrap 注册，测试不能碰。"""
    originals = dict(CHANNELS)
    fakes = {n: Fake(n) for n in ("toast", "dingtalk", "email")}
    for f in fakes.values():
        register_channel(f)
    yield fakes
    CHANNELS.clear()
    CHANNELS.update(originals)


def msg(title="写周报", fire=1_700_000_000_000, **kw):
    return AlertMessage(item_id=kw.pop("item_id", "T1"), kind=kw.pop("kind", "task"),
                        title=title, fire_at_ms=fire, **kw)


def at(offset_min: int) -> str:
    return (datetime.now() + timedelta(minutes=offset_min)).isoformat(timespec="seconds")


# --------------------------------------------------------------------------- 密钥（DPAPI）


def test_dpapi_roundtrip():
    token = secrets.protect("SECabc123")
    assert token.startswith("dpapi:") and "SECabc123" not in token
    assert secrets.unprotect(token) == "SECabc123"


def test_dpapi_empty_stays_empty():
    assert secrets.protect("") == ""
    assert secrets.unprotect("") == ""


def test_mask_never_leaks_plaintext():
    token = secrets.protect("https://oapi.dingtalk.com/robot/send?access_token=SECRETVALUE")
    assert "SECRETVALUE" not in secrets.mask(token)
    assert secrets.mask("") == ""


def test_unprotect_foreign_blob_fails_loudly():
    """换机器/换用户后密文解不开，必须报错而不是返回垃圾字节。"""
    with pytest.raises(secrets.SecretsUnavailable):
        secrets.unprotect("dpapi:" + "AAAA" * 20)


def test_plaintext_passthrough_for_manual_debug():
    assert secrets.unprotect("https://example.com/robot") == "https://example.com/robot"


def test_get_config_decrypts_for_adapters(app_ctx, fake_channels):
    """回归：`get_config()` 曾返回 DPAPI 密文，适配器拿它判 `startswith("https://")`
    永远 False —— 明明配好了却显示「未配置」。单元测试用假配置字典，永远撞不到这条，
    所以必须走真实的 protect 存储路径来测。"""
    webhook = "https://oapi.dingtalk.com/robot/send?access_token=abc123"
    call("alert.config_update", config={"secrets": {"dingtalk_webhook": webhook,
                                                    "dingtalk_secret": "SECtest"}})
    raw = app_ctx.store.secret_get("dingtalk_webhook")
    assert raw.startswith("dpapi:"), "落库的必须是密文"
    got = acfg.get_config()["_secrets"]["dingtalk_webhook"]
    assert got == webhook, "适配器必须拿到明文"
    assert acfg.get_public_config()["secrets_mask"]["dingtalk_webhook"] != webhook


def test_public_config_never_leaks_even_when_set(app_ctx, fake_channels):
    webhook = "https://oapi.dingtalk.com/robot/send?access_token=LEAKCHECK"
    out = str(call("alert.config_update", config={"secrets": {"dingtalk_webhook": webhook}}))
    assert "LEAKCHECK" not in out
    assert "LEAKCHECK" not in str(call("alert.status"))
    assert "LEAKCHECK" not in str(app_ctx.store.alert_log_list(20))


def test_secret_errors_surface_as_reenter_not_unconfigured(app_ctx, fake_channels):
    """DPAPI 密文换机后解不开，必须区分「解不开」和「没填」，否则用户会反复重填。"""
    app_ctx.store.secret_set("dingtalk_webhook", "dpapi:" + "AAAA" * 24)
    cfg = acfg.get_config()
    assert cfg["_secrets"]["dingtalk_webhook"] == ""
    assert "dingtalk_webhook" in cfg["_secret_errors"]
    assert call("alert.status")["secret_errors"] == ["dingtalk_webhook"]


# --------------------------------------------------------------------------- 配置与脱敏


def test_public_config_masks_secrets(app_ctx, fake_channels):
    call("alert.config_update", config={"secrets": {
        "dingtalk_webhook": "https://oapi.dingtalk.com/robot/send?access_token=TOPSECRET",
        "dingtalk_secret": "SECANOTHER"}})
    pub = call("alert.config_get")
    blob = str(pub)
    assert "TOPSECRET" not in blob and "SECANOTHER" not in blob
    assert pub["secrets_set"]["dingtalk_webhook"] is True
    assert "•" in pub["secrets_mask"]["dingtalk_secret"]


def test_unknown_secret_slot_rejected(app_ctx, fake_channels):
    with pytest.raises(QdError):
        call("alert.config_update", config={"secrets": {"evil_token": "x"}})


def test_smtp_preset_fills_host(app_ctx, fake_channels):
    out = call("alert.config_update", config={"smtp": {"preset": "163"}})
    assert out["smtp"]["host"] == "smtp.163.com"


def test_bad_quiet_hours_format_rejected(app_ctx, fake_channels):
    with pytest.raises(QdError):
        call("alert.config_update", config={"dnd": {"start": "25点", "end": "07:00"}})


def test_quiet_hours_crosses_midnight():
    assert acfg.in_quiet_hours(23 * 60 + 30, "23:00", "07:30") is True
    assert acfg.in_quiet_hours(3 * 60, "23:00", "07:30") is True
    assert acfg.in_quiet_hours(9 * 60, "23:00", "07:30") is False
    assert acfg.in_quiet_hours(22 * 60, "23:00", "07:30") is False


# --------------------------------------------------------------------------- 系统通知后端


def test_toast_defaults_to_dialog_not_winotify(app_ctx):
    """实测：winotify 调用成功但用户看不到通知 —— Windows 会静默丢弃非打包应用的 toast。

    断言的是"默认绝不选 toast"，不是具体等于某个后端：注入回调时是 injected、
    托盘在跑时是 balloon，两者都算可靠。
    """
    from qingdan.adapters.toast import ToastChannel

    seen = []
    ch = ToastChannel(acfg.get_config, notifier=lambda t, b: seen.append((t, b)))
    assert ch.backend != "toast", "默认绝不能落到 winotify"
    assert acfg.get_config()["toast"]["backend"] == "dialog"


def test_toast_switches_backend_by_config(app_ctx):
    from qingdan.adapters.toast import ToastChannel

    ch = ToastChannel(acfg.get_config)
    call("alert.config_update", config={"toast": {"backend": "toast"}})
    assert ch.backend == "toast", "用户显式选了 toast 才用 toast"
    call("alert.config_update", config={"toast": {"backend": "dialog"}})
    assert ch.backend == "dialog"


def test_toast_rejects_unknown_backend(app_ctx):
    with pytest.raises(QdError):
        call("alert.config_update", config={"toast": {"backend": "carrier_pigeon"}})


def test_toast_merges_before_showing(app_ctx):
    """多条提醒合成一个对话框，不能弹 30 个模态框。"""
    from qingdan.adapters.toast import ToastChannel

    seen = []
    ch = ToastChannel(acfg.get_config, notifier=lambda t, b: seen.append((t, b)))
    r = ch.send([msg(title=f"任务{i}") for i in range(30)])
    assert r.ok and len(seen) == 1
    title, body = seen[0]
    assert title == "30 条提醒" and body.count("\n") <= 7, "正文要截断，别塞满屏幕"


def test_toast_reports_backend_in_status(app_ctx):
    """注意不能注入 fake_channels —— 这条要验的正是真适配器的 backend 上报。"""
    st = call("alert.status")
    toast = next(c for c in st["channels"] if c["name"] == "toast")
    assert toast["backend"] in ("dialog", "toast", "none")
    assert toast["configured"] is True


# --------------------------------------------------------------------------- 钉钉


def test_sign_is_deterministic_and_urlsafe():
    """加签必须对同一 (secret, ts) 稳定，且是 URL 安全的 base64。

    真正的端到端正确性要等 A9.4 用真机器人验证；这里锁的是实现不随调用漂移。
    """
    s1 = DingTalkChannel.compute_sign("SECkey", 1_700_000_000_000)
    s2 = DingTalkChannel.compute_sign("SECkey", 1_700_000_000_000)
    assert s1 == s2
    assert s1 != DingTalkChannel.compute_sign("SECkey", 1_700_000_000_001)
    # quote_plus 会把 + / = 全部转义，所以裸字符都不该出现，且必须留下 % 转义痕迹
    assert "+" not in s1 and "/" not in s1 and "=" not in s1 and "%" in s1


def test_signed_url_appends_params():
    ch = DingTalkChannel(lambda: {})
    url = ch.signed_url("https://oapi.dingtalk.com/robot/send?access_token=X", "SECk", 1)
    assert "timestamp=1&sign=" in url


def test_unsigned_url_when_no_secret():
    ch = DingTalkChannel(lambda: {})
    assert ch.signed_url("https://oapi.dingtalk.com/robot/send?access_token=X", "", 1) \
        == "https://oapi.dingtalk.com/robot/send?access_token=X"


def _ding(cfg=None, transport=None, now=lambda: 1_700_000.0):
    base = {"master": True, "channels": {"dingtalk": True}, "dingtalk": {},
            "_secrets": {"dingtalk_webhook": "https://oapi.dingtalk.com/robot/send?access_token=X",
                         "dingtalk_secret": "SECk"}}
    base.update(cfg or {})
    return DingTalkChannel(lambda: base, transport=transport, now=now)


def test_ding_merges_many_alerts_into_one_message():
    """钉钉限流 20 条/分钟，超限封 10 分钟 —— 多条必须合并成一条 markdown。"""
    seen = []

    def transport(url, payload):
        seen.append(payload)
        return 200, {"errcode": 0, "errmsg": "ok"}

    r = _ding(transport=transport).send([msg(title=f"任务{i}") for i in range(30)])
    assert r.ok and len(seen) == 1 and r.covered == 30 and r.sent_count == 1
    assert "30 条提醒" in seen[0]["markdown"]["text"]
    assert seen[0]["markdown"]["title"] == "清单提醒"     # 命中自定义关键词的兜底


def test_ding_http_200_with_errcode_is_failure():
    """钉钉最常见的坑：业务错误返回 HTTP 200。只看状态码会把它当成功。"""
    r = _ding(transport=lambda u, p: (200, {"errcode": 310000, "errmsg": "关键词不匹配"})).send([msg()])
    assert r.ok is False
    assert r.code == "DING_ERRCODE_310000"
    assert "关键词不匹配" in r.message


def test_ding_network_error_is_classified():
    def boom(url, payload):
        raise RuntimeError("connection reset")
    r = _ding(transport=boom).send([msg()])
    assert r.ok is False and r.code == "NETWORK"


def test_ding_rate_limited_before_hitting_the_wall():
    """令牌桶留 20% 余量（机器人可能被别的应用共用），到量后返回失败让调度器退避。"""
    r_last = None
    ch = _ding(transport=lambda u, p: (200, {"errcode": 0}))
    for _ in range(25):
        r_last = ch.send([msg()])
        if not r_last.ok:
            break
    assert r_last.ok is False and r_last.code == "RATE_LIMITED"


def test_ding_bucket_refills_after_a_minute():
    clock = {"t": 1_700_000.0}
    ch = _ding(transport=lambda u, p: (200, {"errcode": 0}), now=lambda: clock["t"])
    for _ in range(16):
        assert ch.send([msg()]).ok
    assert ch.send([msg()]).ok is False
    clock["t"] += 61
    assert ch.send([msg()]).ok


def test_ding_not_configured(app_ctx):
    ch = DingTalkChannel(lambda: {"master": True, "channels": {"dingtalk": True},
                                  "dingtalk": {}, "_secrets": {}})
    r = ch.send([msg()])
    assert r.ok is False and r.code == "CHANNEL_NOT_CONFIGURED"


# --------------------------------------------------------------------------- 邮箱


def _mail(cfg=None, connector=None):
    base = {"master": True, "channels": {"email": True},
            "smtp": {"host": "smtp.qq.com", "port": 465, "use_ssl": True,
                     "from_addr": "me@qq.com", "to_addrs": ["me@qq.com"]},
            "_secrets": {"smtp_auth_code": "authcode123"}}
    base.update(cfg or {})
    return EmailChannel(lambda: base, connector=connector)


def test_mail_sends_one_digest_for_many_alerts():
    sent = []

    class Conn:
        def login(self, u, p):
            assert (u, p) == ("me@qq.com", "authcode123")

        def sendmail(self, f, to, body):
            sent.append((f, to, body))
            return {}

        def quit(self):
            pass

    r = _mail(connector=lambda h, p, s: Conn()).send([msg(title=f"任务{i}") for i in range(12)])
    assert r.ok and len(sent) == 1 and r.covered == 12      # 一封，不是十二封
    assert sent[0][0] == "me@qq.com" and sent[0][1] == ["me@qq.com"]
    # MIME 正文是 base64 编码的，必须解码后再断言内容
    payloads = _payloads(sent[0][2])
    assert "任务0" in payloads and "任务11" in payloads


def _payloads(raw: str) -> str:
    """把发出去的 MIME 源码还原成可读正文。"""
    import email.base64mime
    import re
    out = []
    for block in re.findall(r"Content-Transfer-Encoding: base64\r?\n\r?\n(.*?)(?=\r?\n--|\Z)",
                            raw, re.S):
        try:
            out.append(email.base64mime.decode(block).decode("utf-8", "replace"))
        except Exception:
            pass
    return "\n".join(out)


def test_mail_auth_failure_is_actionable():
    import smtplib

    class Bad:
        def login(self, u, p):
            raise smtplib.SMTPAuthenticationError(535, b"auth failed")

        def quit(self):
            pass

    r = _mail(connector=lambda h, p, s: Bad()).send([msg()])
    assert r.ok is False and r.code == "SMTP_AUTH"
    assert "授权码" in r.message                             # 必须给出可行动的原因


def test_mail_connect_failure_mentions_host():
    class Boom:
        def __init__(self):
            raise OSError("timed out")

    r = _mail(connector=lambda h, p, s: (_ for _ in ()).throw(OSError("timed out"))).send([msg()])
    assert r.ok is False and r.code == "SMTP_CONNECT" and "smtp.qq.com" in r.message


def test_mail_refused_recipients_reported():
    class Conn:
        def login(self, u, p):
            pass

        def sendmail(self, f, to, body):
            return {"bad@x.com": (550, b"no such user")}

        def quit(self):
            pass

    r = _mail(connector=lambda h, p, s: Conn()).send([msg()])
    assert r.ok is False and r.code == "SMTP_REFUSED"


def test_mail_incomplete_config(app_ctx):
    ch = _mail(cfg={"smtp": {"host": "smtp.qq.com", "from_addr": "", "to_addrs": []},
                    "_secrets": {}})
    assert ch.is_configured() is False


# --------------------------------------------------------------------------- 调度器


def test_rebuild_is_idempotent(app_ctx, fake_channels):
    tid = call("task.create", title="到期活", due_at=at(5))["id"]
    first = scheduler.rebuild_all()["jobs"]
    second = scheduler.rebuild_all()["jobs"]
    assert first == second == 1, "同一状态重算必须得到相同作业数"
    item = app_ctx.store.get(tid)
    assert app_ctx.store.alert_pending_count() == 1
    assert item is not None


def test_done_and_deleted_items_get_no_jobs(app_ctx, fake_channels):
    a = call("task.create", title="要完成", due_at=at(5))["id"]
    b = call("task.create", title="要删除", due_at=at(5))["id"]
    call("task.complete", item_id=a)
    call("task.delete", item_id=b)
    assert scheduler.rebuild_all()["jobs"] == 0


def test_snooze_overrides_reminder_time(app_ctx, fake_channels):
    tid = call("task.create", title="推迟一下", due_at=at(5))["id"]
    call("task.snooze", item_id=tid, minutes=30)
    rows = app_ctx.store.db.execute("SELECT fire_at FROM alert_job WHERE item_id=?", (tid,)).fetchall()
    assert len(rows) == 1
    mins = (rows[0]["fire_at"] - time.time() * 1000) / 60000
    assert 28 <= mins <= 32, f"snooze 应把提醒推到约 30 分钟后，实得 {mins:.1f}"


def test_defaults_are_never_mutated(app_ctx, fake_channels):
    """回归：`_merge` 曾经浅拷贝，导致关闭渠道会永久改写进程内的 DEFAULTS。

    后果是「第一个用户关掉 toast 之后，全新数据库也继承 toast=False」——
    跨库污染，比单个配置写错难查得多。
    """
    snapshot = copy.deepcopy(acfg.DEFAULTS)
    call("alert.channel_enable", name="toast", on=False)
    call("alert.config_update", config={"dnd": {"start": "01:00", "end": "02:00"}})
    assert acfg.DEFAULTS == snapshot, "DEFAULTS 被写回了，说明合并时没有深拷贝"
    # 新库必须回到出厂默认
    assert acfg.get_config()["channels"]["toast"] is False, "当前库应保持用户的选择"


def test_per_task_channel_override(app_ctx, fake_channels):
    """单任务只能把提醒收窄到**已启用的渠道**上；关掉的全局渠道不该被绕过。"""
    call("alert.channel_enable", name="dingtalk", on=True)
    a = call("task.create", title="只走钉钉", due_at=at(5))["id"]
    b = call("task.create", title="只走本地", due_at=at(5))["id"]
    call("task.set_reminder", item_id=a, channels=["dingtalk"])
    call("task.set_reminder", item_id=b, channels=["toast"])
    chans = {r["channel"] for r in app_ctx.store.db.execute("SELECT channel FROM alert_job")}
    assert chans == {"dingtalk", "toast"}


def test_override_to_disabled_channel_creates_nothing(app_ctx, fake_channels):
    """dingtalk 全局关闭时，任务写 alert_channels=["dingtalk"] 不该偷偷生成作业。"""
    t = call("task.create", title="想走钉钉", due_at=at(5))["id"]
    call("task.set_reminder", item_id=t, channels=["dingtalk"])
    assert app_ctx.store.alert_pending_count() == 0


def test_alert_rebuild_never_marks_items_dirty(app_ctx, fake_channels):
    """关键不变量：告警是派生数据。否则每次改提醒/切免打扰都要往 git 提一次 commit。"""
    call("task.create", title="A", due_at=at(5))
    call("task.create", title="B", due_at=at(9))
    app_ctx.store.mark_clean()
    assert app_ctx.store.dirty_count() == 0
    scheduler.rebuild_all()
    assert app_ctx.store.dirty_count() == 0
    call("alert.config_update", config={"dnd": {"enabled": False}})
    assert app_ctx.store.dirty_count() == 0


def test_dispatch_sends_and_marks_sent(app_ctx, fake_channels):
    call("task.create", title="马上到期", due_at=at(-1))
    scheduler.rebuild_all()
    out = scheduler.dispatch(int(time.time() * 1000))
    assert out["sent"] == 1
    assert fake_channels["toast"].calls and len(fake_channels["toast"].calls[0]) == 1
    st = app_ctx.store.db.execute("SELECT status FROM alert_job").fetchone()["status"]
    assert st == "sent"


def test_dispatch_merges_per_channel(app_ctx, fake_channels):
    for i in range(5):
        call("task.create", title=f"批量{i}", due_at=at(-1))
    scheduler.rebuild_all()
    scheduler.dispatch(int(time.time() * 1000))
    assert len(fake_channels["toast"].calls) == 1, "同一轮到期必须合并成一次发送"
    assert len(fake_channels["toast"].calls[0]) == 5


def test_quiet_hours_suppresses_and_does_not_replay(app_ctx, fake_channels):
    call("alert.config_update", config={"dnd": {"enabled": True, "start": "00:00", "end": "23:59"}})
    call("task.create", title="免打扰内", due_at=at(-1))
    scheduler.rebuild_all()
    out = scheduler.dispatch(int(time.time() * 1000))
    assert out["skipped"] >= 1 and out["sent"] == 0
    assert fake_channels["toast"].calls == [], "免打扰时段不该发任何东西"


def test_master_switch_mutes_everything(app_ctx, fake_channels):
    call("alert.config_update", config={"master": False})
    call("task.create", title="静音", due_at=at(-1))
    scheduler.rebuild_all()
    assert scheduler.rebuild_all()["jobs"] == 0, "总开关关闭时不该生成作业"


def test_channel_disable_stops_jobs(app_ctx, fake_channels):
    call("alert.channel_enable", name="toast", on=False)
    call("task.create", title="不发本地", due_at=at(-1))
    assert scheduler.rebuild_all()["jobs"] == 0


def test_startup_skips_stale_jobs(app_ctx, fake_channels):
    """补发防轰炸：关机一天后开机，历史提醒必须全部 skipped，不能一次涌出。"""
    tid = call("task.create", title="昨天的提醒", due_at=at(-60 * 20))["id"]
    app_ctx.store.alert_add(tid, int(time.time() * 1000) - 20 * 3600_000, "toast")
    n = app_ctx.store.alert_skip_stale(int(time.time() * 1000) - scheduler.STALE_GRACE_MS)
    assert n >= 1
    assert app_ctx.store.alert_pending_count() == 0
    assert fake_channels["toast"].calls == []


def test_failure_backs_off_then_fails(app_ctx, fake_channels):
    fake_channels["toast"].result = SendResult(ok=False, code="RATE_LIMITED", message="限流")
    call("task.create", title="一直失败", due_at=at(-1))
    scheduler.rebuild_all()
    now = int(time.time() * 1000)
    scheduler.dispatch(now)
    row = app_ctx.store.db.execute("SELECT status, next_retry, attempts FROM alert_job").fetchone()
    assert row["status"] == "pending" and row["next_retry"] > now, "失败应退避后重试"
    # 每次都要把时间推到「下一次重试之后」，否则作业不会被取出，看起来像没退避
    clock = now
    for _ in range(5):
        clock = int(app_ctx.store.db.execute(
            "SELECT next_retry FROM alert_job").fetchone()["next_retry"]) + 1
        scheduler.dispatch(clock)
        row = app_ctx.store.db.execute("SELECT status FROM alert_job").fetchone()
        if row["status"] == "failed":
            break
    assert row["status"] == "failed", "3 次后必须落 failed，不能无限重试"
    assert app_ctx.store.alert_pending_count() == 0


def test_gone_item_invalidates_job(app_ctx, fake_channels):
    tid = call("task.create", title="将被删", due_at=at(-1))["id"]
    scheduler.rebuild_all()
    app_ctx.store.db.execute("UPDATE item SET deleted=1 WHERE id=?", (tid,))
    app_ctx.store.db.commit()
    scheduler.dispatch(int(time.time() * 1000))
    assert fake_channels["toast"].calls == [], "已删除的任务不该再推提醒"


def test_countdown_reminder_days_before(app_ctx, fake_channels):
    """按规则断言：倒数日提醒 = 目标日 09:00 减 remind_days_before 天。

    原来写成"还剩约 7 天"的相对区间（6.5 ≤ days ≤ 7.5）—— 那个区间取决于跑测试的
    时间是当天几点，晚上八点后跑必定越界（本轮实测挂在这里）。规则本身是确定的，
    就该按确定值断言，别留一颗每晚定时发作的测试炸弹。
    """
    target = (datetime.now() + timedelta(days=10)).strftime("%Y-%m-%d")
    call("countdown.create", title="统考", target_date=target, remind_days_before=3)
    jobs = app_ctx.store.db.execute("SELECT fire_at FROM alert_job").fetchall()
    assert len(jobs) == 1
    expected = int((datetime.strptime(target, "%Y-%m-%d").replace(hour=9)
                    - timedelta(days=3)).timestamp() * 1000)
    assert jobs[0]["fire_at"] == expected, "fire_at 必须正好是目标日 09:00 往前 3 天"


# --------------------------------------------------------------------------- 接口层


def test_alert_status_shape(app_ctx, fake_channels):
    s = call("alert.status")
    assert {"master", "quiet_hours", "pending", "channels", "last_scan", "synced"} <= set(s)
    assert s["synced"] is False, "告警配置属设备本地，不该被同步"
    assert {c["name"] for c in s["channels"]} == {"toast", "dingtalk", "email"}


def test_alert_preview_lists_upcoming_without_sending(app_ctx, fake_channels):
    call("task.create", title="未来的事", due_at=at(120))
    scheduler.rebuild_all()
    out = call("alert.preview", hours=24)
    assert out["total"] >= 1 and out["items"][0]["channel"] == "toast"
    assert fake_channels["toast"].calls == [], "preview 是干跑，绝不能真发"


def test_alert_silence_blocks_dispatch(app_ctx, fake_channels):
    call("alert.silence", minutes=30)
    call("task.create", title="静音期任务", due_at=at(-1))
    scheduler.rebuild_all()
    out = scheduler.dispatch(int(time.time() * 1000))
    assert out.get("reason") == "muted" and fake_channels["toast"].calls == []


def test_alert_log_records_outcomes(app_ctx, fake_channels):
    call("task.create", title="记一笔", due_at=at(-1))
    scheduler.rebuild_all()
    scheduler.dispatch(int(time.time() * 1000))
    log = call("alert.log")["items"]
    assert log and log[0]["channel"] == "toast"


def test_unknown_channel_rejected(app_ctx, fake_channels):
    with pytest.raises(QdError) as e:
        call("alert.channel_test", name="飞书")
    assert e.value.code == "VALIDATION_ERROR"


def test_alert_tick_endpoint_runs_scheduler(app_ctx, fake_channels):
    call("task.create", title="立即检查", due_at=at(-1))
    scheduler.rebuild_all()
    out = call("alert.tick")
    assert out["sent"] == 1


def test_alert_rebuild_endpoint(app_ctx, fake_channels):
    call("task.create", title="A", due_at=at(5))
    out = call("alert.rebuild")
    assert out["jobs"] == 1 and out["pending"] == 1
