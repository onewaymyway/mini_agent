"""
tests/test_execution_model_status_routes.py

覆盖 next_doc/daemon_execution_model_and_scheduler_heartbeat_improvement_plan.md
配套的看板集成改造新增的 REST 端点：

- GET /v1/self/execution_model_status  汇总"目标级持久 Worker"（阶段一）和
  "调度心跳独立化"（阶段二）两个默认关闭的灰度开关的当前生效状态。

沿用 tests/test_goal_fairness_routes.py 的最小 FastAPI app 模式，不拉起
完整 HttpServer。

运行方式：
    PYTHONPATH=src python3 -m pytest tests/test_execution_model_status_routes.py -q
"""

from __future__ import annotations

import threading
import unittest
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from mini_agent.api.routes import router


def _make_client(http_server_extra: dict, bridge_extra: dict | None = None) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    cfg = SimpleNamespace(
        autonomy=SimpleNamespace(
            objective_persistent_worker_idle_ttl_seconds=1800.0,
            objective_isolated_max_workers=4,
            scheduler_heartbeat_poll_interval_seconds=5.0,
        )
    )
    bridge = SimpleNamespace(agent=SimpleNamespace(cfg=cfg), **(bridge_extra or {}))
    state = SimpleNamespace(bridge=bridge, **http_server_extra)
    app.state.http_server = state
    return TestClient(app)


class _FakePersistentRunner:
    def __init__(self, active_ids, discarded_worker_count=0):
        self._active_ids = list(active_ids)
        self.discarded_worker_count = discarded_worker_count

    def active_execution_ids(self):
        return list(self._active_ids)


class _FakeIsolatedRunner:
    pass


class _FakeHeartbeatThread:
    def __init__(self, alive=True, last_tick_started_at=0.0, last_tick_finished_at=0.0,
                 last_tick_duration_seconds=0.0):
        self._alive = alive
        self.last_tick_started_at = last_tick_started_at
        self.last_tick_finished_at = last_tick_finished_at
        self.last_tick_duration_seconds = last_tick_duration_seconds

    def is_alive(self):
        return self._alive


class _FakeJobRunner:
    def __init__(self, reaped_job_count=0):
        self.reaped_job_count = reaped_job_count


class _FakeCronScheduler:
    def __init__(self, job_runner=None):
        self._job_runner = job_runner


class _FakeObjectiveExecutor:
    def __init__(self, stale_step_reap_count=0):
        self.stale_step_reap_count = stale_step_reap_count


class TestExecutionModelStatusRoute(unittest.TestCase):
    def test_default_state_all_disabled(self):
        """没有任何 runner/heartbeat 挂载时（默认状态），应报告 shared_queue
        执行模式，两个开关都是 disabled。"""
        client = _make_client({
            "_objective_persistent_runner": None,
            "_objective_isolated_runner": None,
            "_scheduler_heartbeat": None,
            "_autonomous_loop": None,
        })
        resp = client.get("/v1/self/execution_model_status")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["objective_execution_mode"], "shared_queue")
        self.assertFalse(body["persistent_worker"]["enabled"])
        self.assertFalse(body["isolated_runner"]["enabled"])
        self.assertFalse(body["scheduler_heartbeat"]["enabled"])
        self.assertFalse(body["scheduler_heartbeat"]["alive"])
        # [daemon_task_hang_recovery_and_watchdog_hardening_plan.md 阶段二/三]
        self.assertEqual(body["persistent_worker"]["discarded_worker_count"], 0)
        self.assertEqual(body["scheduler_heartbeat"]["last_tick_started_at"], 0.0)
        self.assertEqual(body["scheduler_heartbeat"]["last_tick_finished_at"], 0.0)
        self.assertEqual(body["scheduler_heartbeat"]["last_tick_duration_seconds"], 0.0)
        self.assertEqual(body["cron"]["reaped_job_count"], 0)
        self.assertEqual(body["objective_executor"]["stale_step_reap_count"], 0)

    def test_persistent_worker_enabled_reports_active_executions(self):
        client = _make_client({
            "_objective_persistent_runner": _FakePersistentRunner(["exec_a", "exec_b"]),
            "_objective_isolated_runner": None,
            "_scheduler_heartbeat": None,
            "_autonomous_loop": None,
        })
        resp = client.get("/v1/self/execution_model_status")
        body = resp.json()
        self.assertEqual(body["objective_execution_mode"], "persistent")
        self.assertTrue(body["persistent_worker"]["enabled"])
        self.assertEqual(body["persistent_worker"]["active_execution_count"], 2)
        self.assertEqual(set(body["persistent_worker"]["active_execution_ids"]), {"exec_a", "exec_b"})
        self.assertEqual(body["persistent_worker"]["idle_ttl_seconds"], 1800.0)

    def test_isolated_runner_reported_when_persistent_absent(self):
        client = _make_client({
            "_objective_persistent_runner": None,
            "_objective_isolated_runner": _FakeIsolatedRunner(),
            "_scheduler_heartbeat": None,
            "_autonomous_loop": None,
        })
        resp = client.get("/v1/self/execution_model_status")
        body = resp.json()
        self.assertEqual(body["objective_execution_mode"], "isolated")
        self.assertTrue(body["isolated_runner"]["enabled"])
        self.assertEqual(body["isolated_runner"]["max_workers"], 4)

    def test_persistent_takes_priority_over_isolated_when_both_present(self):
        """两个 runner 理论上不应该同时存在（server.py 用 if/elif 互斥接线），
        但路由本身也应该体现"persistent 优先"这条既定语义，不依赖调用方
        一定遵守互斥。"""
        client = _make_client({
            "_objective_persistent_runner": _FakePersistentRunner(["exec_a"]),
            "_objective_isolated_runner": _FakeIsolatedRunner(),
            "_scheduler_heartbeat": None,
            "_autonomous_loop": None,
        })
        resp = client.get("/v1/self/execution_model_status")
        body = resp.json()
        self.assertEqual(body["objective_execution_mode"], "persistent")

    def test_scheduler_heartbeat_alive_reported(self):
        loop = SimpleNamespace(get_digest_status=lambda: {"tick_interval_seconds": 60.0})
        client = _make_client({
            "_objective_persistent_runner": None,
            "_objective_isolated_runner": None,
            "_scheduler_heartbeat": _FakeHeartbeatThread(alive=True),
            "_autonomous_loop": loop,
        })
        resp = client.get("/v1/self/execution_model_status")
        body = resp.json()
        self.assertTrue(body["scheduler_heartbeat"]["enabled"])
        self.assertTrue(body["scheduler_heartbeat"]["alive"])
        self.assertEqual(body["scheduler_heartbeat"]["poll_interval_seconds"], 5.0)
        self.assertEqual(body["scheduler_heartbeat"]["tick_interval_seconds"], 60.0)

    def test_scheduler_heartbeat_enabled_but_dead_thread_reported_not_alive(self):
        loop = SimpleNamespace(get_digest_status=lambda: {"tick_interval_seconds": 60.0})
        client = _make_client({
            "_objective_persistent_runner": None,
            "_objective_isolated_runner": None,
            "_scheduler_heartbeat": _FakeHeartbeatThread(alive=False),
            "_autonomous_loop": loop,
        })
        resp = client.get("/v1/self/execution_model_status")
        body = resp.json()
        self.assertTrue(body["scheduler_heartbeat"]["enabled"])
        self.assertFalse(body["scheduler_heartbeat"]["alive"])

    def test_scheduler_heartbeat_last_tick_timestamps_reported(self):
        """[阶段二] last_tick_started_at/last_tick_finished_at/
        last_tick_duration_seconds 透传自心跳线程对象。"""
        loop = SimpleNamespace(get_digest_status=lambda: {"tick_interval_seconds": 60.0})
        client = _make_client({
            "_objective_persistent_runner": None,
            "_objective_isolated_runner": None,
            "_scheduler_heartbeat": _FakeHeartbeatThread(
                alive=True, last_tick_started_at=100.0,
                last_tick_finished_at=100.5, last_tick_duration_seconds=0.5,
            ),
            "_autonomous_loop": loop,
        })
        resp = client.get("/v1/self/execution_model_status")
        body = resp.json()
        self.assertEqual(body["scheduler_heartbeat"]["last_tick_started_at"], 100.0)
        self.assertEqual(body["scheduler_heartbeat"]["last_tick_finished_at"], 100.5)
        self.assertEqual(body["scheduler_heartbeat"]["last_tick_duration_seconds"], 0.5)

    def test_persistent_worker_discarded_count_reported(self):
        """[阶段三·顺带做] discarded_worker_count 透传自 persistent runner。"""
        client = _make_client({
            "_objective_persistent_runner": _FakePersistentRunner(["exec_a"], discarded_worker_count=7),
            "_objective_isolated_runner": None,
            "_scheduler_heartbeat": None,
            "_autonomous_loop": None,
        })
        resp = client.get("/v1/self/execution_model_status")
        body = resp.json()
        self.assertEqual(body["persistent_worker"]["discarded_worker_count"], 7)

    def test_cron_reaped_job_count_reported(self):
        """[阶段一/三] cron.reaped_job_count 透传自
        bridge._cron_scheduler._job_runner.reaped_job_count。"""
        client = _make_client(
            {
                "_objective_persistent_runner": None,
                "_objective_isolated_runner": None,
                "_scheduler_heartbeat": None,
                "_autonomous_loop": None,
            },
            bridge_extra={
                "_cron_scheduler": _FakeCronScheduler(job_runner=_FakeJobRunner(reaped_job_count=3)),
            },
        )
        resp = client.get("/v1/self/execution_model_status")
        body = resp.json()
        self.assertEqual(body["cron"]["reaped_job_count"], 3)

    def test_objective_executor_stale_step_reap_count_reported(self):
        """[阶段三] objective_executor.stale_step_reap_count 透传自
        bridge._objective_executor.stale_step_reap_count。"""
        client = _make_client(
            {
                "_objective_persistent_runner": None,
                "_objective_isolated_runner": None,
                "_scheduler_heartbeat": None,
                "_autonomous_loop": None,
            },
            bridge_extra={
                "_objective_executor": _FakeObjectiveExecutor(stale_step_reap_count=5),
            },
        )
        resp = client.get("/v1/self/execution_model_status")
        body = resp.json()
        self.assertEqual(body["objective_executor"]["stale_step_reap_count"], 5)


if __name__ == "__main__":
    unittest.main()
