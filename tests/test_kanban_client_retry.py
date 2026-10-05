"""tests/test_kanban_client_retry.py — 看板客户端"读超时不重试"测试
（对应 next_doc/growth_tab_split_and_trend_index_plan.md §6.3 与 §8.3）。

用本地桩 HTTP server 计数请求：
  - 读超时：`retry=False` 只收到 1 次请求，`retry=True`（旧行为）收到多次
  - 504：同上
  - 连接失败：两者都会重试
  - 成长顾问 tab 渲染路径上的 GET 方法全部走 retry=False；新增三个方法的
    路径与超时预算符合方案
"""

from __future__ import annotations

import sys
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "apps" / "mini_agent_kanban"))

import client as client_module  # noqa: E402
from client import AgentClient  # noqa: E402


class _Stub:
    """记录每个路径被请求的次数；/slow 睡 1.2s，/gw 返回 504，/ok 返回 JSON。"""

    def __init__(self):
        self.hits: dict[str, int] = {}
        self.lock = threading.Lock()
        stub = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):  # 静默
                pass

            def do_GET(self):
                path = self.path.split("?")[0]
                with stub.lock:
                    stub.hits[path] = stub.hits.get(path, 0) + 1
                if path == "/slow":
                    time.sleep(1.2)
                if path == "/gw":
                    body, code = b'{"detail": "timeout"}', 504
                else:
                    body, code = b'{"ok": true}', 200
                try:
                    self.send_response(code)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class TestReadTimeoutRetry(unittest.TestCase):
    def setUp(self):
        self.stub = _Stub()
        self.client = AgentClient(f"http://127.0.0.1:{self.stub.port}")

    def tearDown(self):
        self.stub.close()

    def test_no_retry_read_timeout_single_request(self):
        res = self.client._get("/slow", timeout=0.4, retry=False)
        self.assertIn("_error", res)
        time.sleep(1.5)  # 等桩把可能的后续请求处理完再计数
        self.assertEqual(self.stub.hits.get("/slow"), 1)

    def test_default_retry_read_timeout_multiple_requests(self):
        with mock.patch("urllib3.util.retry.Retry.sleep"):
            res = self.client._get("/slow", timeout=0.4)  # 默认 retry=True，旧行为
        self.assertIn("_error", res)
        time.sleep(1.5)
        self.assertGreater(self.stub.hits.get("/slow"), 1)

    def test_no_retry_on_504(self):
        res = self.client._get("/gw", retry=False)
        self.assertIn("HTTP 504", res["_error"])
        self.assertEqual(self.stub.hits.get("/gw"), 1)

    def test_default_retries_504(self):
        with mock.patch("urllib3.util.retry.Retry.sleep"):
            res = self.client._get("/gw")
        self.assertIn("HTTP 504", res["_error"])
        self.assertEqual(self.stub.hits.get("/gw"), 3)  # total=2 -> 共 3 次

    def test_success_unchanged(self):
        self.assertEqual(self.client._get("/ok", retry=False), {"ok": True})
        self.assertEqual(self.client._get("/ok"), {"ok": True})


class TestConnectFailureStillRetried(unittest.TestCase):
    def _count_connects(self, retry: bool) -> int:
        from urllib3.connection import HTTPConnection
        from urllib3.exceptions import NewConnectionError

        calls = {"n": 0}

        def boom(conn_self):
            calls["n"] += 1
            raise NewConnectionError(conn_self, "refused")

        client = AgentClient("http://127.0.0.1:9")
        with mock.patch.object(HTTPConnection, "connect", boom), \
                mock.patch("urllib3.util.retry.Retry.sleep"):
            res = client._get("/x", timeout=1, retry=retry)
        self.assertIn("_error", res)
        return calls["n"]

    def test_both_sessions_retry_connect_failures(self):
        self.assertEqual(self._count_connects(True), 3)
        self.assertEqual(self._count_connects(False), 3)

    def test_session_retry_configs(self):
        rd = client_module._HTTP.get_adapter("http://x").max_retries
        rn = client_module._HTTP_NO_READ_RETRY.get_adapter("http://x").max_retries
        self.assertEqual((rd.connect, rd.read), (2, 2))
        self.assertEqual((rn.connect, rn.read, rn.status), (2, 0, 0))
        self.assertFalse(rn.status_forcelist)


class TestGrowthTabMethods(unittest.TestCase):
    """成长顾问 tab 渲染路径上的 GET 方法：路径、超时预算、retry=False。"""

    def setUp(self):
        self.client = AgentClient("http://x")
        self.calls = []

        def fake_get(path, params=None, timeout=6, *, retry=True):
            self.calls.append((path, params, timeout, retry))
            return {}

        self.client._get = fake_get

    def test_new_methods(self):
        self.client.growth_overview()
        self.client.growth_diagnostics()
        self.client.growth_diagnostics(refresh=True)
        self.client.growth_topic_map()
        self.assertEqual(self.calls, [
            ("/growth/overview", None, 15, False),
            ("/growth/diagnostics", None, 25, False),
            ("/growth/diagnostics", {"refresh_diagnostics": True}, 50, False),
            ("/growth/topic_map", None, 25, False),
        ])

    def test_summary_budget_and_no_retry(self):
        self.client.growth_summary()
        self.client.growth_summary(refresh_diagnostics=True)
        self.assertEqual([(c[2], c[3]) for c in self.calls], [(25, False), (50, False)])

    def test_all_growth_render_path_gets_disable_retry(self):
        for name in ("growth_followups", "growth_reports_refresh_candidates", "growth_pursuits",
                     "growth_pursuits_portfolio_summary", "growth_pursuits_related_directions",
                     "growth_align", "growth_health_trend"):
            self.calls.clear()
            getattr(self.client, name)()
            self.assertEqual(len(self.calls), 1, name)
            self.assertIs(self.calls[0][3], False, name)

    def test_growth_align_timeout_budget_covers_server_hard_timeout(self):
        # 服务端 growth_align 的 run_blocking 硬超时 45s（开 LLM 语义匹配时会调一次
        # LLM），客户端预算必须不小于它，否则客户端先放弃、服务端仍在算。
        self.calls.clear()
        self.client.growth_align()
        self.assertEqual((self.calls[0][2], self.calls[0][3]), (50, False))

    def test_unrelated_get_keeps_default_retry(self):
        self.calls.clear()
        self.client.decision_profile()
        self.assertIs(self.calls[0][3], True)


if __name__ == "__main__":
    unittest.main()
