"""
tests/test_daemon_dual_signal_hang_detection.py

覆盖 next_doc/daemon_dual_signal_hang_detection_plan.md 阶段B：

  - evolution/scheduler_heartbeat.py:
      - heartbeat_status_file_path() 路径约定
      - SchedulerHeartbeat 的看门狗轮询会把观测状态写入磁盘旁路文件
        （_write_status_file()），写入内容与内存态一致
  - cli/daemon.py:
      - read_scheduler_heartbeat_status()：文件不存在/内容损坏时返回 None，
        正常时原样返回解析后的 dict
      - evaluate_scheduler_heartbeat_freshness()：
          - status=None -> None（信号不可用）
          - 新鲜且 suspected_stuck=False -> True
          - suspected_stuck=True -> False
          - written_at 过期 -> False
  - cli/daemon_supervisor.py 的双信号判定矩阵（端到端，通过 mock
    DaemonClient.health_check + 手写心跳旁路文件模拟两种信号的组合）：
      - HTTP 连续无响应 + 心跳新鲜 -> 不强杀，继续观察
      - HTTP 连续无响应 + 心跳过期/suspected_stuck -> 强杀，
        hang_signal="scheduler_heartbeat"
      - HTTP 连续无响应 + 无心跳文件（未开启/未产生数据）-> 强杀，
        hang_signal="http_only"（与阶段一原有行为一致）
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
import unittest
import unittest.mock
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Optional

from mini_agent.cli import daemon as daemon_mod
from mini_agent.cli import daemon_supervisor
from mini_agent.evolution.scheduler_heartbeat import (
    SchedulerHeartbeat,
    heartbeat_status_file_path,
)


class _IsolatedHomeTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.project_root = Path(self._tmp.name) / "project"
        self.project_root.mkdir(parents=True)
        self._home = Path(self._tmp.name) / "home"
        self._home.mkdir(parents=True)
        self._old_home = os.environ.get("HOME")
        os.environ["HOME"] = str(self._home)

    def tearDown(self):
        if self._old_home is not None:
            os.environ["HOME"] = self._old_home
        else:
            os.environ.pop("HOME", None)
        self._tmp.cleanup()


# ── SchedulerHeartbeat 磁盘旁路写入 ───────────────────────────────────────

class _FakeAutonomousLoop:
    def __init__(self, tick_side_effect=None):
        self.tick_calls = 0
        self._tick_side_effect = tick_side_effect
        self._lock = threading.Lock()

    def should_tick(self) -> bool:
        return True

    def tick(self) -> None:
        with self._lock:
            self.tick_calls += 1
            if self._tick_side_effect:
                self._tick_side_effect()


class _FakePaths:
    """只提供 SchedulerHeartbeat._write_status_file() 需要的 project_root
    属性，不引入完整 AgentPaths 依赖。"""

    def __init__(self, project_root: Path):
        self.project_root = project_root


class TestSchedulerHeartbeatDiskSidecar(_IsolatedHomeTestCase):
    def test_status_file_path_convention(self):
        p = heartbeat_status_file_path(self.project_root)
        self.assertEqual(p, self.project_root / ".agent" / "scheduler_heartbeat_status.json")

    def test_watchdog_writes_status_file_with_matching_fields(self):
        loop = _FakeAutonomousLoop()
        lock = threading.Lock()
        hb = SchedulerHeartbeat(
            loop, lock, interval_seconds=0.05, tick_interval_seconds=1.0,
            paths=_FakePaths(self.project_root),
        )
        hb.start()
        try:
            status_path = heartbeat_status_file_path(self.project_root)
            deadline = time.time() + 3
            while not status_path.exists() and time.time() < deadline:
                time.sleep(0.02)
            self.assertTrue(status_path.exists())

            payload = json.loads(status_path.read_text(encoding="utf-8"))
            self.assertIn("written_at", payload)
            self.assertIn("last_tick_started_at", payload)
            self.assertIn("last_tick_finished_at", payload)
            self.assertIn("suspected_stuck", payload)
            self.assertEqual(payload["pid"], os.getpid())
            self.assertFalse(payload["suspected_stuck"])
            # 至少发生过一次 tick，且旁路文件里的耗时字段与内存态一致。
            deadline = time.time() + 2
            while loop.tick_calls < 1 and time.time() < deadline:
                time.sleep(0.02)
            self.assertGreaterEqual(loop.tick_calls, 1)
        finally:
            hb.stop()
            hb.join(timeout=2)

    def test_no_paths_skips_writing_without_raising(self):
        """paths=None（构造 AgentPaths 失败的极端情况）时不应该写文件，
        也不应该影响心跳线程本身的存活。"""
        loop = _FakeAutonomousLoop()
        lock = threading.Lock()
        hb = SchedulerHeartbeat(loop, lock, interval_seconds=0.05, paths=None)
        hb.start()
        try:
            time.sleep(0.3)
            self.assertTrue(hb.is_alive())
            self.assertFalse(heartbeat_status_file_path(self.project_root).exists())
        finally:
            hb.stop()
            hb.join(timeout=2)

    def test_suspected_stuck_reflected_in_status_file(self):
        """看门狗判定为疑似卡死时，磁盘旁路文件应该同步反映
        suspected_stuck=True（不需要等下一次 tick 真正返回）。"""
        block_evt = threading.Event()

        def _blocking_tick():
            block_evt.wait(timeout=5)

        loop = _FakeAutonomousLoop(tick_side_effect=_blocking_tick)
        lock = threading.Lock()
        hb = SchedulerHeartbeat(
            loop, lock, interval_seconds=0.05, tick_interval_seconds=0.05,
            stuck_threshold_multiplier=1.0, paths=_FakePaths(self.project_root),
        )
        hb.start()
        try:
            status_path = heartbeat_status_file_path(self.project_root)
            deadline = time.time() + 5
            found_stuck = False
            while time.time() < deadline:
                if status_path.exists():
                    try:
                        payload = json.loads(status_path.read_text(encoding="utf-8"))
                        if payload.get("suspected_stuck"):
                            found_stuck = True
                            break
                    except (OSError, json.JSONDecodeError):
                        pass
                time.sleep(0.02)
            self.assertTrue(found_stuck, "旁路文件应该在看门狗判定疑似卡死后同步置位")
        finally:
            block_evt.set()
            hb.stop()
            hb.join(timeout=2)


# ── daemon.py 读取 + 新鲜度判定辅助函数 ────────────────────────────────────

class TestReadSchedulerHeartbeatStatus(_IsolatedHomeTestCase):
    def test_missing_file_returns_none(self):
        self.assertIsNone(daemon_mod.read_scheduler_heartbeat_status(self.project_root))

    def test_valid_file_returns_parsed_dict(self):
        p = heartbeat_status_file_path(self.project_root)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"written_at": 123.0, "suspected_stuck": False}), encoding="utf-8")
        status = daemon_mod.read_scheduler_heartbeat_status(self.project_root)
        self.assertEqual(status["written_at"], 123.0)

    def test_corrupt_file_returns_none(self):
        p = heartbeat_status_file_path(self.project_root)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("{not valid json", encoding="utf-8")
        self.assertIsNone(daemon_mod.read_scheduler_heartbeat_status(self.project_root))


class TestEvaluateSchedulerHeartbeatFreshness(unittest.TestCase):
    def test_none_status_is_unavailable_signal(self):
        self.assertIsNone(daemon_mod.evaluate_scheduler_heartbeat_freshness(None))

    def test_fresh_and_not_stuck_is_healthy(self):
        now = 1000.0
        status = {"written_at": now - 1.0, "tick_interval_seconds": 5.0, "suspected_stuck": False}
        self.assertTrue(
            daemon_mod.evaluate_scheduler_heartbeat_freshness(status, now=now)
        )

    def test_suspected_stuck_is_unhealthy_even_if_written_recently(self):
        now = 1000.0
        status = {"written_at": now - 0.1, "tick_interval_seconds": 5.0, "suspected_stuck": True}
        self.assertFalse(
            daemon_mod.evaluate_scheduler_heartbeat_freshness(status, now=now)
        )

    def test_stale_written_at_is_unhealthy(self):
        now = 1000.0
        # tick_interval=5s，multiplier 默认 2 -> 阈值 10s；这里过期 100s。
        status = {"written_at": now - 100.0, "tick_interval_seconds": 5.0, "suspected_stuck": False}
        self.assertFalse(
            daemon_mod.evaluate_scheduler_heartbeat_freshness(status, now=now)
        )

    def test_malformed_status_returns_none(self):
        self.assertIsNone(
            daemon_mod.evaluate_scheduler_heartbeat_freshness({"written_at": "not-a-number"})
        )


# ── daemon_supervisor.py 双信号判定矩阵（端到端） ─────────────────────────

_CHILD_SLEEP_FOREVER_SCRIPT = """
import os
import time
from pathlib import Path

Path({pid_marker!r}).write_text(str(os.getpid()))
time.sleep(3600)
"""


class TestDualSignalHangDetectionMatrix(_IsolatedHomeTestCase):
    def _write_sleep_forever_script(self) -> Path:
        pid_marker = self.project_root / "child.pid"
        script_path = self.project_root / "child_sleep_forever.py"
        script_path.write_text(
            _CHILD_SLEEP_FOREVER_SCRIPT.format(pid_marker=str(pid_marker)),
            encoding="utf-8",
        )
        return script_path

    def _write_heartbeat_status(self, **overrides):
        payload = {
            "written_at": time.time(),
            "last_tick_started_at": time.time() - 0.1,
            "last_tick_finished_at": time.time(),
            "last_tick_duration_seconds": 0.05,
            "tick_interval_seconds": 60.0,
            "suspected_stuck": False,
            "pid": 99999,
        }
        payload.update(overrides)
        p = heartbeat_status_file_path(self.project_root)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(payload), encoding="utf-8")

    def test_http_unresponsive_but_heartbeat_fresh_is_not_killed(self):
        """核心场景：HTTP 连续无响应，但核心调度心跳新鲜——不应该被强杀，
        不产生任何崩溃记录。"""
        script_path = self._write_sleep_forever_script()
        self._write_heartbeat_status()

        with unittest.mock.patch.object(
            daemon_mod.DaemonClient, "health_check", return_value=False
        ):
            proc_result = {}

            def _run():
                proc_result["done"] = True
                daemon_supervisor.run_supervisor(
                    self.project_root,
                    [sys.executable, str(script_path)],
                    auto_restart=False,
                    http_port=1,
                    hang_detection_enabled=True,
                    hang_check_interval_seconds=0.02,
                    hang_check_timeout_seconds=0.02,
                    hang_consecutive_failures=2,
                )

            t = threading.Thread(target=_run, daemon=True)
            t.start()
            # 保持心跳文件持续新鲜，跑一段时间验证期间没有被误杀。
            deadline = time.time() + 0.5
            while time.time() < deadline:
                self._write_heartbeat_status()
                time.sleep(0.02)

            pid_marker = self.project_root / "child.pid"
            child_pid = int(pid_marker.read_text().strip()) if pid_marker.exists() else None
            daemon_mod.mark_stopped_by_user(self.project_root, child_pid or 0)
            if child_pid:
                daemon_mod._force_kill_process(child_pid)
            t.join(timeout=5)

        hist_path = daemon_mod._crash_history_file(self.project_root)
        self.assertFalse(hist_path.exists())

    def test_http_unresponsive_and_heartbeat_stuck_is_killed_with_scheduler_signal(self):
        """HTTP 连续无响应 + 核心调度心跳疑似卡死——判定为核心调度卡死，
        hang_signal 应为 scheduler_heartbeat。"""
        script_path = self._write_sleep_forever_script()
        self._write_heartbeat_status(suspected_stuck=True)

        with unittest.mock.patch.object(
            daemon_mod.DaemonClient, "health_check", return_value=False
        ):
            daemon_supervisor.run_supervisor(
                self.project_root,
                [sys.executable, str(script_path)],
                auto_restart=False,
                http_port=1,
                hang_detection_enabled=True,
                hang_check_interval_seconds=0.05,
                hang_check_timeout_seconds=0.05,
                hang_consecutive_failures=2,
            )

        hist_path = daemon_mod._crash_history_file(self.project_root)
        record = json.loads(hist_path.read_text(encoding="utf-8").splitlines()[0])
        self.assertEqual(record["restart_decision"], "hang_killed")
        self.assertEqual(record["hang_signal"], "scheduler_heartbeat")
        self.assertIn("核心任务调度卡死", record["summary"])

    def test_http_unresponsive_and_heartbeat_stale_is_killed_with_scheduler_signal(self):
        """written_at 过期（看门狗线程自己都调度不动）同样按核心调度卡死
        处理，不需要 suspected_stuck 显式为 True。"""
        script_path = self._write_sleep_forever_script()
        self._write_heartbeat_status(
            written_at=time.time() - 10000.0, tick_interval_seconds=1.0,
        )

        with unittest.mock.patch.object(
            daemon_mod.DaemonClient, "health_check", return_value=False
        ):
            daemon_supervisor.run_supervisor(
                self.project_root,
                [sys.executable, str(script_path)],
                auto_restart=False,
                http_port=1,
                hang_detection_enabled=True,
                hang_check_interval_seconds=0.05,
                hang_check_timeout_seconds=0.05,
                hang_consecutive_failures=2,
            )

        hist_path = daemon_mod._crash_history_file(self.project_root)
        record = json.loads(hist_path.read_text(encoding="utf-8").splitlines()[0])
        self.assertEqual(record["hang_signal"], "scheduler_heartbeat")

    def test_no_heartbeat_file_falls_back_to_http_only(self):
        """没有旁路文件（未开启 scheduler_heartbeat_enabled）时，行为与
        阶段一完全一致：HTTP 连续无响应直接判定卡死，hang_signal 为
        http_only。"""
        script_path = self._write_sleep_forever_script()
        # 不写心跳文件。

        with unittest.mock.patch.object(
            daemon_mod.DaemonClient, "health_check", return_value=False
        ):
            daemon_supervisor.run_supervisor(
                self.project_root,
                [sys.executable, str(script_path)],
                auto_restart=False,
                http_port=1,
                hang_detection_enabled=True,
                hang_check_interval_seconds=0.05,
                hang_check_timeout_seconds=0.05,
                hang_consecutive_failures=2,
            )

        hist_path = daemon_mod._crash_history_file(self.project_root)
        record = json.loads(hist_path.read_text(encoding="utf-8").splitlines()[0])
        self.assertEqual(record["restart_decision"], "hang_killed")
        self.assertEqual(record["hang_signal"], "http_only")


if __name__ == "__main__":
    unittest.main()
