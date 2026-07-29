"""notification/channels/email.py — EmailChannel（P1）。

标准 SMTP（smtplib + email.mime），配置见
next_doc/watchlist_notification_goal_design.md §3.3。发送失败（连接超时/
认证失败等）记 log_exception，不重试、不阻塞其它渠道——kanban 兜底渠道
保证这条通知本身不会因为邮件发送失败而彻底消失（§9.3 #8）。
"""

from __future__ import annotations

import smtplib
from email.mime.text import MIMEText
from typing import TYPE_CHECKING

from mini_agent.notification.dispatcher import NotificationChannel, NotificationMessage, register_channel

if TYPE_CHECKING:
    from mini_agent.storage.paths import AgentPaths


@register_channel("email")
class EmailChannel(NotificationChannel):
    def send(self, message: NotificationMessage, cfg: dict, paths: "AgentPaths") -> bool:
        host = cfg.get("smtp_host")
        to_addrs = cfg.get("to_addrs") or []
        if not host or not to_addrs:
            return False
        port = int(cfg.get("smtp_port", 465))
        use_ssl = bool(cfg.get("use_ssl", True))
        username = cfg.get("username") or ""
        password = cfg.get("password") or ""
        from_addr = cfg.get("from_addr") or username or "mini-agent@localhost"

        body = message.body
        if message.url:
            body = f"{body}\n\n链接：{message.url}"
        mime = MIMEText(body, "plain", "utf-8")
        mime["Subject"] = message.title
        mime["From"] = from_addr
        mime["To"] = ", ".join(to_addrs)

        try:
            if use_ssl:
                server = smtplib.SMTP_SSL(host, port, timeout=15)
            else:
                server = smtplib.SMTP(host, port, timeout=15)
            try:
                if not use_ssl:
                    server.starttls()
                if username:
                    server.login(username, password)
                server.sendmail(from_addr, to_addrs, mime.as_string())
            finally:
                server.quit()
            return True
        except Exception as exc:
            from mini_agent.errors import log_exception
            log_exception(exc, where="mini_agent.notification.channels.email.send")
            return False
