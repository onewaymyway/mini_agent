"""notification/channels/webhook.py — WebhookChannel（轻量 IM 推送渠道）。

[next_doc/personal_assistant_experience_improvement_directions.md 缺口一]
`NotificationChannel` 此前只有 `email`/`kanban` 两个渠道——对个人助手场景
来说，用户不太可能守着邮箱或天天开看板，成长顾问周报/能力学习通知/
统一收件箱新建议都只能被动等用户自己发现。这里补一个通用的"POST 一条
JSON 到某个 URL"渠道，覆盖企业微信群机器人 / Server 酱 / Bark 等常见的
个人轻量 IM 推送服务——它们本质上都是同一种接入模式，用一个 `template`
配置项区分请求体格式，不需要为每个服务各写一个 channel 类。

跟 `EmailChannel` 一样只用标准库（`urllib.request`），不引入新的第三方
依赖；发送失败只记 log_exception、不重试、不抛异常向上传播——保持跟
项目里"单个渠道失败不拖垮整体 dispatch"的一贯风格一致。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import TYPE_CHECKING

from mini_agent.notification.dispatcher import NotificationChannel, NotificationMessage, register_channel

if TYPE_CHECKING:
    from mini_agent.storage.paths import AgentPaths


def _build_request(template: str, message: NotificationMessage, cfg: dict) -> tuple[bytes, dict]:
    """按 template 拼出请求体和请求头。返回 (body_bytes, headers)。

    支持的 template：
      - "generic"（默认）：POST JSON `{"title", "body", "url"}`，给自建
        接收端或本身就接受这个结构的服务用。
      - "wecom"：企业微信群机器人 webhook，`{"msgtype": "text",
        "text": {"content": "..."}}`。
      - "server_chan"：Server 酱（`sctapi.ftqq.com`），表单字段
        `title`/`desp`（Markdown 正文）。
      - "bark"：Bark（`api.day.app`），JSON `{"title", "body", "url"}`——
        Bark v2 API 原生支持这个格式，跟 generic 共用同一份请求体。
    """
    body_text = message.body
    if message.url:
        body_text = f"{body_text}\n\n链接：{message.url}"

    if template == "wecom":
        payload = {"msgtype": "text", "text": {"content": f"{message.title}\n{body_text}"}}
        return json.dumps(payload, ensure_ascii=False).encode("utf-8"), {"Content-Type": "application/json"}

    if template == "server_chan":
        import urllib.parse

        payload = urllib.parse.urlencode({"title": message.title, "desp": body_text}).encode("utf-8")
        return payload, {"Content-Type": "application/x-www-form-urlencoded"}

    # "generic" / "bark" 共用同一份 JSON 结构
    payload = {"title": message.title, "body": body_text, "url": message.url or ""}
    return json.dumps(payload, ensure_ascii=False).encode("utf-8"), {"Content-Type": "application/json"}


@register_channel("webhook")
class WebhookChannel(NotificationChannel):
    def send(self, message: NotificationMessage, cfg: dict, paths: "AgentPaths") -> bool:
        url = cfg.get("url")
        if not url:
            return False
        template = str(cfg.get("template") or "generic")
        timeout = float(cfg.get("timeout", 10))

        try:
            data, headers = _build_request(template, message, cfg)
            req = urllib.request.Request(url, data=data, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return 200 <= resp.status < 300
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError) as exc:
            from mini_agent.errors import log_exception
            log_exception(exc, where="mini_agent.notification.channels.webhook.WebhookChannel.send")
            return False
