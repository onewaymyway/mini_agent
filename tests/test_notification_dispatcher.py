"""tests/test_notification_dispatcher.py — NotificationDispatcher（P1）测试

覆盖：
  1. 默认配置（无 config.yaml）：只有 kanban 兜底渠道生效
  2. kanban 发送：落地成 alerts.jsonl 一条记录，source_type=notification
  3. kanban 隐式兜底：channels 显式传 ["email"]（未启用）时，kanban 仍然
     被追加尝试发送（§9.3 #8）
  4. email 渠道未启用（config.yaml 里 enabled: false）：不发送，不报错
  5. email 渠道配置缺 smtp_host/to_addrs：send() 直接返回 False，不报错
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from mini_agent.notification import NotificationDispatcher, NotificationMessage
from mini_agent.notification.channels.email import EmailChannel
from mini_agent.storage.paths import AgentPaths


class TestNotificationDispatcher(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.paths = AgentPaths(Path(self._tmp.name))

    def tearDown(self):
        self._tmp.cleanup()

    def _msg(self, **kw):
        base = dict(title="t", body="b", source="watchlist_report")
        base.update(kw)
        return NotificationMessage(**base)

    def test_default_channels_kanban_only(self):
        d = NotificationDispatcher(self.paths)
        result = d.dispatch(self._msg())
        self.assertEqual(result, {"kanban": True})

    def test_kanban_writes_alert_record(self):
        d = NotificationDispatcher(self.paths)
        d.dispatch(self._msg(title="标题", body="正文", meta={"watchlist_id": "x"}))
        lines = self.paths.external_input_alerts.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 1)
        rec = json.loads(lines[0])
        self.assertEqual(rec["title"], "标题")
        self.assertEqual(rec["source_type"], "notification")
        self.assertFalse(rec["acknowledged"])

    def test_kanban_implicit_fallback_when_only_email_requested(self):
        d = NotificationDispatcher(self.paths)
        result = d.dispatch(self._msg(), channels=["email"])
        self.assertIn("kanban", result)
        self.assertTrue(result["kanban"])
        self.assertEqual(result.get("email"), False)  # email 未在 config.yaml 里启用

    def test_email_channel_not_enabled_by_default(self):
        d = NotificationDispatcher(self.paths)
        result = d.dispatch(self._msg(), channels=["email", "kanban"])
        self.assertFalse(result["email"])

    def test_email_channel_missing_required_fields_returns_false(self):
        ch = EmailChannel()
        ok = ch.send(self._msg(), {}, self.paths)
        self.assertFalse(ok)


if __name__ == "__main__":
    unittest.main()
