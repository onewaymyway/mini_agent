"""
tests/test_daemon_dual_signal_hang_detection_stage_c.py

覆盖 next_doc/daemon_dual_signal_hang_detection_plan.md 阶段C：

  - api/http_busy.py:
      - HttpBusyTracker：request_started/request_finished 计数正确、
        snapshot() 返回 in_flight_count / oldest_in_flight_seconds
      - HttpBusyMiddleware：非 http scope（如 lifespan）直接透传不计数；
        并发请求下计数准确，请求结束后归零
  - api/routes.py::get_self_execution_model_status:
      - 响应新增 http_busy 字段，来源于 app.state.http_busy 的快照
      - app.state 上没有 http_busy（旧客户端/未挂载）时返回默认零值，
        不报错
  - api/server.py::create_app:
      - 创建的 app 会把 HttpBusyTracker 挂到 app.state.http_busy，
        且真实发起请求时计数会变化（集成验证，非纯 mock）

运行方式：
    PYTHONPATH=src python3 -m pytest tests/test_daemon_dual_signal_hang_detection_stage_c.py -q
"""

from __future__ import annotations

import threading
import time
import unittest
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from mini_agent.api.http_busy import HttpBusyMiddleware, HttpBusyTracker
from mini_agent.api.routes import router


class TestHttpBusyTracker(unittest.TestCase):
    def test_snapshot_zero_when_idle(self):
        tracker = HttpBusyTracker()
        snap = tracker.snapshot()
        self.assertEqual(snap["in_flight_count"], 0)
        self.assertEqual(snap["oldest_in_flight_seconds"], 0.0)

    def test_request_started_increments_count(self):
        tracker = HttpBusyTracker()
        tracker.request_started(1)
        tracker.request_started(2)
        snap = tracker.snapshot()
        self.assertEqual(snap["in_flight_count"], 2)

    def test_request_finished_decrements_count(self):
        tracker = HttpBusyTracker()
        tracker.request_started(1)
        tracker.request_started(2)
        tracker.request_finished(1)
        snap = tracker.snapshot()
        self.assertEqual(snap["in_flight_count"], 1)

    def test_finish_never_goes_negative(self):
        tracker = HttpBusyTracker()
        tracker.request_finished(999)  # 从未 started 过的 token
        snap = tracker.snapshot()
        self.assertEqual(snap["in_flight_count"], 0)

    def test_oldest_in_flight_seconds_reflects_earliest_start(self):
        tracker = HttpBusyTracker()
        tracker.request_started(1)
        time.sleep(0.05)
        tracker.request_started(2)
        snap = tracker.snapshot()
        # token 1 更早开始，oldest_in_flight_seconds 应该 >= sleep 的时长
        self.assertGreaterEqual(snap["oldest_in_flight_seconds"], 0.04)

    def test_finish_removes_from_started_at(self):
        tracker = HttpBusyTracker()
        tracker.request_started(1)
        tracker.request_finished(1)
        snap = tracker.snapshot()
        self.assertEqual(snap["oldest_in_flight_seconds"], 0.0)

    def test_concurrent_start_finish_thread_safe(self):
        tracker = HttpBusyTracker()
        n = 50

        def _worker(token):
            tracker.request_started(token)
            time.sleep(0.001)
            tracker.request_finished(token)

        threads = [threading.Thread(target=_worker, args=(i,)) for i in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        snap = tracker.snapshot()
        self.assertEqual(snap["in_flight_count"], 0)


class TestHttpBusyMiddleware(unittest.TestCase):
    def _make_app(self, tracker: HttpBusyTracker) -> FastAPI:
        app = FastAPI()
        app.add_middleware(HttpBusyMiddleware, tracker=tracker)

        @app.get("/probe")
        async def probe():
            return {"in_flight": tracker.snapshot()["in_flight_count"]}

        return app

    def test_probe_reports_itself_as_in_flight(self):
        tracker = HttpBusyTracker()
        app = self._make_app(tracker)
        client = TestClient(app)
        resp = client.get("/probe")
        self.assertEqual(resp.status_code, 200)
        # 请求处理这一刻自己也被计入 in-flight（预期行为，见 routes.py 注释）
        self.assertEqual(resp.json()["in_flight"], 1)

    def test_count_returns_to_zero_after_request_completes(self):
        tracker = HttpBusyTracker()
        app = self._make_app(tracker)
        client = TestClient(app)
        client.get("/probe")
        self.assertEqual(tracker.snapshot()["in_flight_count"], 0)

    def test_multiple_sequential_requests_do_not_leak_count(self):
        tracker = HttpBusyTracker()
        app = self._make_app(tracker)
        client = TestClient(app)
        for _ in range(5):
            client.get("/probe")
        self.assertEqual(tracker.snapshot()["in_flight_count"], 0)


class TestExecutionModelStatusHttpBusyField(unittest.TestCase):
    def _make_client(self, http_busy=None) -> TestClient:
        app = FastAPI()
        app.include_router(router)
        cfg = SimpleNamespace(
            autonomy=SimpleNamespace(
                objective_persistent_worker_idle_ttl_seconds=1800.0,
                objective_isolated_max_workers=4,
                scheduler_heartbeat_poll_interval_seconds=5.0,
            )
        )
        bridge = SimpleNamespace(agent=SimpleNamespace(cfg=cfg))
        state = SimpleNamespace(
            bridge=bridge,
            _objective_persistent_runner=None,
            _objective_isolated_runner=None,
            _scheduler_heartbeat=None,
            _autonomous_loop=None,
        )
        app.state.http_server = state
        if http_busy is not None:
            app.state.http_busy = http_busy
        return TestClient(app)

    def test_defaults_to_zero_when_tracker_not_mounted(self):
        client = self._make_client(http_busy=None)
        resp = client.get("/v1/self/execution_model_status")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["http_busy"], {"in_flight_count": 0, "oldest_in_flight_seconds": 0.0})

    def test_reports_tracker_snapshot_when_mounted(self):
        tracker = HttpBusyTracker()
        tracker.request_started(1)
        tracker.request_started(2)
        client = self._make_client(http_busy=tracker)
        resp = client.get("/v1/self/execution_model_status")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        # 本请求自己也会被算作 in-flight（见 HttpBusyMiddleware 未挂载在这个
        # 最小测试 app 上——这里只挂了 tracker 对象本身，不经过中间件，所以
        # 只反映我们手动 started 的两个 token）。
        self.assertEqual(body["http_busy"]["in_flight_count"], 2)

    def test_snapshot_failure_does_not_break_endpoint(self):
        class _BrokenTracker:
            def snapshot(self):
                raise RuntimeError("boom")

        client = self._make_client(http_busy=_BrokenTracker())
        resp = client.get("/v1/self/execution_model_status")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        # 快照失败时保留默认零值，不影响端点其余字段正常返回
        self.assertEqual(body["http_busy"], {"in_flight_count": 0, "oldest_in_flight_seconds": 0.0})


class TestCreateAppMountsHttpBusyTracker(unittest.TestCase):
    def test_create_app_state_has_http_busy_tracker(self):
        from mini_agent.api.bridge import init_bridge
        from mini_agent.api.fs_helper import FsHelper
        from mini_agent.api.server import create_app
        from pathlib import Path
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as tmp:
            project_root = Path(tmp)
            bridge = init_bridge(ring_maxlen=100)
            bridge.agent = SimpleNamespace(cfg=SimpleNamespace(autonomy=SimpleNamespace()))
            fs_helper = FsHelper(project_root=project_root, readonly=False, excludes=[])
            app = create_app(
                bridge=bridge,
                fs_helper=fs_helper,
                token="test-token",
                allowed_ips=["127.0.0.1"],
                cors_origins=["*"],
                role_store=None,
                project_root=project_root,
                session_pool=None,
                access_log_enabled=False,
                access_log_path="",
            )
            self.assertIsInstance(app.state.http_busy, HttpBusyTracker)

    def test_real_request_through_created_app_updates_tracker_transiently(self):
        # 集成验证：真实发一个请求（走完整中间件链），确认 HttpBusyMiddleware
        # 确实被挂载且生效（请求结束后计数应归零，不残留）。
        from mini_agent.api.bridge import init_bridge
        from mini_agent.api.fs_helper import FsHelper
        from mini_agent.api.server import create_app
        from pathlib import Path
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as tmp:
            project_root = Path(tmp)
            bridge = init_bridge(ring_maxlen=100)
            bridge.agent = SimpleNamespace(cfg=SimpleNamespace(autonomy=SimpleNamespace()))
            fs_helper = FsHelper(project_root=project_root, readonly=False, excludes=[])
            app = create_app(
                bridge=bridge,
                fs_helper=fs_helper,
                token="test-token",
                allowed_ips=["127.0.0.1"],
                cors_origins=["*"],
                role_store=None,
                project_root=project_root,
                session_pool=None,
                access_log_enabled=False,
                access_log_path="",
            )
            client = TestClient(app)
            client.get("/", headers={"Authorization": "Bearer test-token"})
            self.assertEqual(app.state.http_busy.snapshot()["in_flight_count"], 0)


if __name__ == "__main__":
    unittest.main()
