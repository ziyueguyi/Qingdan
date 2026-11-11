"""钉钉群机器人渠道（M3 / A4）。

三个必须处理的现实：

1. **限流 20 条/分钟，超限封禁 10 分钟** —— 所以同一次触发窗口内的多条提醒
   合并成一条 markdown，并用令牌桶兜底。
2. **业务错误返回 HTTP 200**，真因在 body 的 `errcode/errmsg`。只看状态码会把
   「关键词不匹配 310000」当成发送成功，用户永远不知道为什么没收到。
3. **加签**：`sign = urlencode(base64(hmac_sha256(secret, f"{ts}\\n{secret}")))`，
   有固定测试向量，不联网也能自证正确。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import urllib.parse
from typing import Any, Callable

from qingdan.core.alerting.base import AlertMessage, SendResult

# 命中「自定义关键词」安全设置的兜底标题。机器人若配了别的关键词，
# 用户可在配置里覆盖 title_prefix。
DEFAULT_TITLE = "清单提醒"
RATE_LIMIT_PER_MIN = 20
BUCKET_MARGIN = 0.8          # 留 20% 余量：机器人可能被别的应用共用


class DingTalkChannel:
    name = "dingtalk"

    def __init__(self, config_getter: Callable[[], dict],
                 transport: Callable[[str, dict], tuple[int, Any]] | None = None,
                 now: Callable[[], float] = time.time):
        self._config = config_getter
        self._transport = transport
        self._now = now
        self._sent: list[float] = []

    # -- 配置 --------------------------------------------------------------
    def _cfg(self) -> dict:
        c = self._config()
        return {"webhook": c["_secrets"].get("dingtalk_webhook") or "",
                "secret": c["_secrets"].get("dingtalk_secret") or "",
                "at_mobiles": (c.get("dingtalk") or {}).get("at_mobiles") or [],
                "at_all": bool((c.get("dingtalk") or {}).get("at_all")),
                "enabled": bool((c.get("channels") or {}).get(self.name)),
                "master": bool(c.get("master")),
                "title": (c.get("dingtalk") or {}).get("title_prefix") or DEFAULT_TITLE}

    def is_configured(self) -> bool:
        return self._cfg()["webhook"].startswith("https://oapi.dingtalk.com/robot/send")

    def enabled(self) -> bool:
        c = self._cfg()
        return bool(c["enabled"] and c["master"])

    # -- 加签 --------------------------------------------------------------
    @staticmethod
    def compute_sign(secret: str, timestamp_ms: int) -> str:
        raw = f"{timestamp_ms}\n{secret}".encode("utf-8")
        digest = hmac.new(secret.encode("utf-8"), raw, hashlib.sha256).digest()
        return urllib.parse.quote_plus(base64.b64encode(digest))

    def signed_url(self, webhook: str, secret: str, timestamp_ms: int) -> str:
        if not secret:
            return webhook
        sep = "&" if "?" in webhook else "?"
        return f"{webhook}{sep}timestamp={timestamp_ms}&sign={self.compute_sign(secret, timestamp_ms)}"

    # -- 限流 --------------------------------------------------------------
    def _allow(self) -> bool:
        cutoff = self._now() - 60.0
        self._sent = [t for t in self._sent if t > cutoff]
        limit = int(RATE_LIMIT_PER_MIN * BUCKET_MARGIN)
        if len(self._sent) >= limit:
            return False
        self._sent.append(self._now())
        return True

    # -- 消息 --------------------------------------------------------------
    def build_payload(self, msgs: list[AlertMessage]) -> dict:
        c = self._cfg()
        if len(msgs) == 1:
            text = msgs[0].line().lstrip("- ")
        else:
            text = f"**{len(msgs)} 条提醒**\n" + "\n".join(m.line() for m in msgs)
        return {
            "msgtype": "markdown",
            "markdown": {"title": c["title"], "text": f"### {c['title']}\n\n{text}"},
            "at": {"atMobiles": c["at_mobiles"], "isAtAll": c["at_all"]},
        }

    # -- 发送 --------------------------------------------------------------
    def _post(self, url: str, payload: dict) -> tuple[int, Any]:
        if self._transport:
            return self._transport(url, payload)
        import httpx
        r = httpx.post(url, json=payload, timeout=10,
                       headers={"Content-Type": "application/json"})
        try:
            body: Any = r.json()
        except (json.JSONDecodeError, ValueError):
            body = r.text
        return r.status_code, body

    def send(self, msgs: list[AlertMessage]) -> SendResult:
        if not msgs:
            return SendResult(ok=True, sent_count=0, covered=0)
        c = self._cfg()
        if not self.is_configured():
            return SendResult(ok=False, code="CHANNEL_NOT_CONFIGURED", message="钉钉 webhook 未配置或地址不对")
        if not self._allow():
            # 不丢弃：返回失败让调度器退避重试，比静默少发一条好
            return SendResult(ok=False, code="RATE_LIMITED",
                              message=f"钉钉机器人限流（{RATE_LIMIT_PER_MIN} 条/分钟），本次推迟重试",
                              covered=len(msgs))
        ts = int(self._now() * 1000)
        url = self.signed_url(c["webhook"], c["secret"], ts)
        try:
            status, body = self._post(url, self.build_payload(msgs))
        except Exception as e:                       # 网络异常不外抛，转成可展示的原因
            return SendResult(ok=False, code="NETWORK", message=f"{type(e).__name__}: {e}",
                              covered=len(msgs))
        errcode = body.get("errcode") if isinstance(body, dict) else None
        if status == 200 and errcode == 0:
            return SendResult(ok=True, sent_count=1, covered=len(msgs),
                              detail={"errmsg": body.get("errmsg", "ok")})
        # HTTP 200 但 errcode != 0 是钉钉最常见的坑，必须把真因带出来
        return SendResult(ok=False,
                          code=f"DING_ERRCODE_{errcode}" if errcode is not None else f"HTTP_{status}",
                          message=(body.get("errmsg") if isinstance(body, dict) else str(body))[:200]
                                  or f"HTTP {status}",
                          detail={"status": status, "body": _safe(body)},
                          covered=len(msgs))

    def test(self) -> SendResult:
        msg = AlertMessage(item_id="__test__", kind="task", title="这是一条测试提醒",
                           fire_at_ms=int(self._now() * 1000), when_text="现在")
        r = self.send([msg])
        r.detail["test"] = True
        return r


def _safe(body: Any) -> Any:
    """进日志/响应前去掉可能含 token 的字段。"""
    if isinstance(body, dict):
        return {k: v for k, v in body.items() if k not in ("webhook", "url", "access_token")}
    return str(body)[:200]
