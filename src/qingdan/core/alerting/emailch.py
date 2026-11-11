"""邮箱 SMTP 渠道（M3 / A5）。

要点：
  · 用**授权码**不是登录密码（QQ/163 都是这样，写错就是一直认证失败）
  · 一次连接发**一封汇总**，不一条一封 —— 授权码发信也有频率风控
  · 错误要分类（认证失败 / 连接不通 / 收件人被拒），否则用户只会看到「发送失败」
"""
from __future__ import annotations

import smtplib
import ssl
import time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr
from typing import Any, Callable

from qingdan.core.alerting.base import AlertMessage, SendResult

TIMEOUT = 15


class EmailChannel:
    name = "email"

    def __init__(self, config_getter: Callable[[], dict],
                 connector: Callable[[str, int, bool], Any] | None = None,
                 now: Callable[[], float] = time.time):
        self._config = config_getter
        self._connector = connector
        self._now = now

    def _cfg(self) -> dict:
        c = self._config()
        smtp = c.get("smtp") or {}
        return {"host": smtp.get("host") or "", "port": int(smtp.get("port") or 465),
                "use_ssl": bool(smtp.get("use_ssl", True)),
                "from_addr": smtp.get("from_addr") or "",
                "to_addrs": [a for a in (smtp.get("to_addrs") or []) if a],
                "auth_code": c["_secrets"].get("smtp_auth_code") or "",
                "enabled": bool((c.get("channels") or {}).get(self.name)),
                "master": bool(c.get("master"))}

    def is_configured(self) -> bool:
        c = self._cfg()
        return bool(c["host"] and c["from_addr"] and c["auth_code"] and c["to_addrs"])

    def enabled(self) -> bool:
        c = self._cfg()
        return bool(c["enabled"] and c["master"])

    # -- 组信 --------------------------------------------------------------
    def build(self, msgs: list[AlertMessage], subject: str) -> MIMEMultipart:
        c = self._cfg()
        mail = MIMEMultipart("alternative")
        mail["Subject"] = subject
        mail["From"] = formataddr(("清单提醒", c["from_addr"]))
        mail["To"] = ", ".join(c["to_addrs"])
        plain = "\n".join(m.line().lstrip("- ") for m in msgs)
        rows = "".join(
            f"<tr><td style='padding:4px 8px'>{_esc(m.line().lstrip('- '))}</td></tr>" for m in msgs)
        mail.attach(MIMEText(f"<p>共 {len(msgs)} 条提醒：</p><table>{rows}</table>", "html", "utf-8"))
        mail.attach(MIMEText(plain, "plain", "utf-8"))
        return mail

    # -- 连接 --------------------------------------------------------------
    def _connect(self, host: str, port: int, use_ssl: bool):
        if self._connector:
            return self._connector(host, port, use_ssl)
        if use_ssl:
            return smtplib.SMTP_SSL(host, port, timeout=TIMEOUT,
                                    context=ssl.create_default_context())
        s = smtplib.SMTP(host, port, timeout=TIMEOUT)
        s.starttls(context=ssl.create_default_context())
        return s

    def send(self, msgs: list[AlertMessage]) -> SendResult:
        if not msgs:
            return SendResult(ok=True, sent_count=0, covered=0)
        c = self._cfg()
        if not self.is_configured():
            return SendResult(ok=False, code="CHANNEL_NOT_CONFIGURED",
                              message="邮箱未配置完整（服务商/发件地址/授权码/收件人缺一不可）")
        subject = f"清单提醒：{msgs[0].title}" if len(msgs) == 1 else f"清单提醒（{len(msgs)} 条）"
        conn = None
        try:
            conn = self._connect(c["host"], c["port"], c["use_ssl"])
            conn.login(c["from_addr"], c["auth_code"])
            refused = conn.sendmail(c["from_addr"], c["to_addrs"],
                                    self.build(msgs, subject).as_string())
            if refused:
                return SendResult(ok=False, code="SMTP_REFUSED",
                                  message=f"部分收件人被拒收：{refused}", covered=len(msgs))
            return SendResult(ok=True, sent_count=1, covered=len(msgs),
                              detail={"host": c["host"], "port": c["port"]})
        except smtplib.SMTPAuthenticationError as e:
            return SendResult(ok=False, code="SMTP_AUTH", covered=len(msgs),
                              message="认证失败：请确认填的是**授权码**而不是登录密码，"
                                      "且已开启 SMTP 服务")
        except (smtplib.SMTPConnectError, OSError) as e:
            return SendResult(ok=False, code="SMTP_CONNECT", covered=len(msgs),
                              message=f"连不上 {c['host']}:{c['port']}（检查服务商与网络）")
        except smtplib.SMTPRecipientsRefused as e:
            return SendResult(ok=False, code="SMTP_REFUSED", covered=len(msgs), message=str(e)[:200])
        except smtplib.SMTPException as e:
            return SendResult(ok=False, code="SMTP_ERROR", covered=len(msgs),
                              message=f"{type(e).__name__}: {e}")
        finally:
            try:
                if conn is not None:
                    conn.quit()
            except Exception:
                pass

    def test(self) -> SendResult:
        msg = AlertMessage(item_id="__test__", kind="task", title="这是一封测试提醒邮件",
                           fire_at_ms=int(self._now() * 1000), when_text="现在")
        r = self.send([msg])
        r.detail["test"] = True
        return r


def _esc(s: str) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
