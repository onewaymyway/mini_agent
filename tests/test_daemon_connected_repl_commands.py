"""
tests/test_daemon_connected_repl_commands.py — [具身改进 A1] Connected REPL
完整命令对等：/cron /goals /digest。

覆盖：
  1. DaemonClient 新增方法（list_cron_jobs / run_cron_job / list_goals /
     get_autonomous_status / get_digest）正确转发到对应 HTTP 端点
  2. _handle_connected_cron / _handle_connected_goals / _handle_connected_digest
     在拿到数据 / 拿不到数据时都有合理输出，不抛异常
"""

from __future__ import annotations

import json
import unittest
from unittest.mock import MagicMock, patch

from mini_agent.cli.daemon import (
    DaemonClient,
    _handle_connected_cron,
    _handle_connected_goals,
    _handle_connected_digest,
)


def _fake_response(payload: dict):
    """构造一个可被 `with urllib.request.urlopen(...) as resp` 使用的 mock。"""
    resp = MagicMock()
    resp.read.return_value = json.dumps(payload).encode()
    resp.__enter__.return_value = resp
    resp.__exit__.return_value = False
    return resp


class TestDaemonClientNewMethods(unittest.TestCase):
    def setUp(self):
        self.client = DaemonClient(http_port=9999, token="t")

    def test_list_cron_jobs_get(self):
        payload = {"jobs": [{"id": "j1", "name": "n", "enabled": True}]}
        with patch("urllib.request.urlopen", return_value=_fake_response(payload)) as m:
            result = self.client.list_cron_jobs()
        self.assertEqual(result, payload)
        called_url = m.call_args[0][0].full_url
        self.assertIn("/v1/cron/jobs", called_url)

    def test_run_cron_job_post(self):
        payload = {"triggered": True, "job_id": "j1"}
        with patch("urllib.request.urlopen", return_value=_fake_response(payload)) as m:
            result = self.client.run_cron_job("j1")
        self.assertEqual(result, payload)
        req = m.call_args[0][0]
        self.assertIn("/v1/cron/jobs/j1/run", req.full_url)
        self.assertEqual(req.get_method(), "POST")

    def test_list_goals_get(self):
        payload = {"goals": [], "objectives": []}
        with patch("urllib.request.urlopen", return_value=_fake_response(payload)):
            result = self.client.list_goals()
        self.assertEqual(result, payload)

    def test_get_autonomous_status_get(self):
        payload = {"autonomy_level": "passive"}
        with patch("urllib.request.urlopen", return_value=_fake_response(payload)):
            result = self.client.get_autonomous_status()
        self.assertEqual(result, payload)

    def test_get_digest_hits_self_status_endpoint(self):
        payload = {"goals": {"active_goals": [], "active_objectives": []}}
        with patch("urllib.request.urlopen", return_value=_fake_response(payload)) as m:
            result = self.client.get_digest()
        self.assertEqual(result, payload)
        self.assertIn("/v1/self/status", m.call_args[0][0].full_url)

    def test_methods_return_none_on_failure(self):
        with patch("urllib.request.urlopen", side_effect=OSError("boom")):
            self.assertIsNone(self.client.list_cron_jobs())
            self.assertIsNone(self.client.run_cron_job("x"))
            self.assertIsNone(self.client.list_goals())
            self.assertIsNone(self.client.get_autonomous_status())
            self.assertIsNone(self.client.get_digest())


class TestConnectedReplCommandHandlers(unittest.TestCase):
    def setUp(self):
        self.lines: list[str] = []
        self._out = self.lines.append

    def test_cron_list_renders_jobs(self):
        client = MagicMock()
        client.list_cron_jobs.return_value = {
            "jobs": [
                {"id": "j1", "name": "morning_scan", "enabled": True,
                 "schedule": "interval:3600", "next_run_str": "2026-07-01 09:00",
                 "run_count": 3},
            ]
        }
        _handle_connected_cron(client, "/cron", self._out)
        self.assertTrue(any("morning_scan" in l for l in self.lines))
        self.assertTrue(any("j1" in l for l in self.lines))

    def test_cron_list_empty(self):
        client = MagicMock()
        client.list_cron_jobs.return_value = {"jobs": []}
        _handle_connected_cron(client, "/cron list", self._out)
        self.assertTrue(any("no cron jobs" in l for l in self.lines))

    def test_cron_run_dispatches_job_id(self):
        client = MagicMock()
        client.run_cron_job.return_value = {"triggered": True, "job_id": "j1"}
        _handle_connected_cron(client, "/cron run j1", self._out)
        client.run_cron_job.assert_called_once_with("j1")
        self.assertTrue(any("Triggered job" in l for l in self.lines))

    def test_cron_run_missing_job_id_shows_usage(self):
        client = MagicMock()
        _handle_connected_cron(client, "/cron run", self._out)
        self.assertTrue(any("usage" in l for l in self.lines))
        client.run_cron_job.assert_not_called()

    def test_cron_fetch_failure_does_not_raise(self):
        client = MagicMock()
        client.list_cron_jobs.return_value = None
        _handle_connected_cron(client, "/cron", self._out)
        self.assertTrue(any("Failed to fetch cron jobs" in l for l in self.lines))

    def test_goals_renders_goals_and_objectives(self):
        client = MagicMock()
        client.list_goals.return_value = {
            "goals": [{"id": "g1", "status": "active", "priority": 1, "title": "Improve X"}],
            "objectives": [{"id": "o1", "status": "in_progress", "title": "Step 1", "progress_notes": "halfway"}],
        }
        _handle_connected_goals(client, self._out)
        self.assertTrue(any("Improve X" in l for l in self.lines))
        self.assertTrue(any("Step 1" in l for l in self.lines))

    def test_goals_empty(self):
        client = MagicMock()
        client.list_goals.return_value = {"goals": [], "objectives": []}
        _handle_connected_goals(client, self._out)
        self.assertTrue(any("no active goals" in l for l in self.lines))

    def test_goals_fetch_failure_does_not_raise(self):
        client = MagicMock()
        client.list_goals.return_value = None
        _handle_connected_goals(client, self._out)
        self.assertTrue(any("Failed to fetch goals" in l for l in self.lines))

    def test_digest_combines_autonomous_and_self_status(self):
        client = MagicMock()
        client.get_autonomous_status.return_value = {
            "autonomy_level": "maintenance", "next_tick_in": 42.0, "cron_jobs": [1, 2],
        }
        client.get_digest.return_value = {
            "goals": {"active_goals": [1], "active_objectives": []},
            "recent_activity": [{"ts": 1719999999, "type": "session_crashed"}],
            "session_pool": {"active_count": 2},
        }
        _handle_connected_digest(client, self._out)
        self.assertTrue(any("autonomy_level=maintenance" in l for l in self.lines))
        self.assertTrue(any("active_goals=1" in l for l in self.lines))
        self.assertTrue(any("session_pool" in l for l in self.lines))

    def test_digest_total_failure_does_not_raise(self):
        client = MagicMock()
        client.get_autonomous_status.return_value = None
        client.get_digest.return_value = None
        _handle_connected_digest(client, self._out)
        self.assertTrue(any("Failed to fetch digest" in l for l in self.lines))


if __name__ == "__main__":
    unittest.main()
