"""tests/test_notification_webhook_channel.py — WebhookChannel 测试

[next_doc/personal_assistant_experience_improvement_directions.md 缺口一]

覆盖：
  1. 缺少 url 时直接返回 False，不发起请求
  2. generic/bark 模板请求体格式
  3. wecom 模板请求体格式
  4. server_chan 模板请求体格式（表单编码）
  5. urlopen 抛异常时返回 False，不向上传播
  6. 已注册进 dispatcher 的 channel registry，且能被 NotificationDispatcher
     正常调用（enabled: true 时）
"""

from __future__ import annotations

import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

from mini_agent.notification import NotificationDispatcher, NotificationMessage
from mini_agent.notification.channels.webhook import WebhookChannel
from mini_agent.notification.dispatcher import get_channel_class
from mini_agent.storage.paths import AgentPaths


def _msg(**kw):
    base = dict(title="标题", body="正文", source="growth_report")
    base.update(kw)
    return NotificationMessage(**base)


class TestWebhookChannelRegistration(unittest.TestCase):
    def test_registered_under_webhook_name(self):
        self.assertIs(get_channel_class("webhook"), WebhookChannel)


class TestWebhookChannelSend(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.paths = AgentPaths(Path(self._tmp.name))

    def tearDown(self):
        self._tmp.cleanup()

    def test_missing_url_returns_false_without_request(self):
        channel = WebhookChannel()
        with patch("urllib.request.urlopen") as mock_open:
            ok = channel.send(_msg(), {}, self.paths)
        self.assertFalse(ok)
        mock_open.assert_not_called()

    def _fake_response(self, status=200):
        resp = MagicMock()
        resp.status = status
        resp.__enter__.return_value = resp
        resp.__exit__.return_value = False
        return resp

    def test_generic_template_posts_json_body(self):
        channel = WebhookChannel()
        cfg = {"url": "https://example.com/hook", "template": "generic"}
        with patch("urllib.request.urlopen", return_value=self._fake_response()) as mock_open:
            ok = channel.send(_msg(url="https://a.example/x"), cfg, self.paths)
        self.assertTrue(ok)
        req = mock_open.call_args[0][0]
        payload = json.loads(req.data.decode("utf-8"))
        self.assertEqual(payload["title"], "标题")
        self.assertIn("正文", payload["body"])
        self.assertEqual(payload["url"], "https://a.example/x")

    def test_wecom_template_posts_text_message(self):
        channel = WebhookChannel()
        cfg = {"url": "https://qyapi.weixin.qq.com/x", "template": "wecom"}
        with patch("urllib.request.urlopen", return_value=self._fake_response()) as mock_open:
            ok = channel.send(_msg(), cfg, self.paths)
        self.assertTrue(ok)
        req = mock_open.call_args[0][0]
        payload = json.loads(req.data.decode("utf-8"))
        self.assertEqual(payload["msgtype"], "text")
        self.assertIn("标题", payload["text"]["content"])
        self.assertIn("正文", payload["text"]["content"])

    def test_server_chan_template_uses_form_encoding(self):
        channel = WebhookChannel()
        cfg = {"url": "https://sctapi.ftqq.com/x.send", "template": "server_chan"}
        with patch("urllib.request.urlopen", return_value=self._fake_response()) as mock_open:
            ok = channel.send(_msg(), cfg, self.paths)
        self.assertTrue(ok)
        req = mock_open.call_args[0][0]
        self.assertIn(b"title=", req.data)
        self.assertIn(b"desp=", req.data)

    def test_non_2xx_status_returns_false(self):
        channel = WebhookChannel()
        cfg = {"url": "https://example.com/hook"}
        with patch("urllib.request.urlopen", return_value=self._fake_response(status=500)):
            ok = channel.send(_msg(), cfg, self.paths)
        self.assertFalse(ok)

    def test_urlopen_exception_returns_false_not_raises(self):
        channel = WebhookChannel()
        cfg = {"url": "https://example.com/hook"}
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("boom")):
            ok = channel.send(_msg(), cfg, self.paths)
        self.assertFalse(ok)

    def test_dispatcher_calls_webhook_when_enabled(self):
        import yaml

        cfg_dir = self.paths.notification_config.parent
        cfg_dir.mkdir(parents=True, exist_ok=True)
        self.paths.notification_config.write_text(
            yaml.safe_dump({
                "default_channels": ["kanban", "webhook"],
                "channels": {"webhook": {"enabled": True, "url": "https://example.com/hook"}},
            }),
            encoding="utf-8",
        )
        with patch("urllib.request.urlopen", return_value=self._fake_response()) as mock_open:
            d = NotificationDispatcher(self.paths)
            result = d.dispatch(_msg())
        self.assertTrue(result["webhook"])
        mock_open.assert_called_once()


if __name__ == "__main__":
    unittest.main()
