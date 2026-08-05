"""
tests/test_objective_persistent_worker_restart_summary.py

覆盖 next_doc/daemon_stability_and_ux_improvement_plan.md 第 2 项 / P3-2：
持久 Worker 跨重启连续性——

  - 关闭时（默认）：不调用 compact_with_skills、不落盘、不读取，行为与
    升级前完全一致
  - 开启时：每个 step 完成后落盘摘要；"重启"（模拟为清空内存里的
    Agent 缓存）后重建 Agent 时读取摘要并注入 restart_summary
  - release() 清理摘要文件

全部用 fake Agent，不构造真实 Agent/LLM client。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mini_agent.evolution import objective_agent_bridge as bridge_mod
from mini_agent.evolution.objective_agent_bridge import ObjectivePersistentRunner


class _FakePaths:
    def __init__(self, root: Path):
        self._root = root

    @property
    def workdir_dir(self) -> Path:
        return self._root


class _Autonomy:
    def __init__(self, enabled: bool, max_chars: int = 4000):
        self.objective_persistent_worker_restart_summary_enabled = enabled
        self.objective_persistent_worker_restart_summary_max_chars = max_chars


class _FakeAppConfig:
    def __init__(self, enabled: bool, max_chars: int = 4000):
        self.autonomy = _Autonomy(enabled, max_chars)


class _FakeAgent:
    def __init__(self):
        self.run_turn_calls: list[str] = []
        self.compact_calls = 0
        self._last_turn_result_invalid = False

    def run_turn(self, message: str) -> str:
        self.run_turn_calls.append(message)
        return f"done: {message[:20]}"

    def compact_with_skills(self, goal_hint: str = "") -> str:
        self.compact_calls += 1
        return f"摘要#{self.compact_calls}: 已完成 {len(self.run_turn_calls)} 个 step"


def _wait_idle(runner: ObjectivePersistentRunner, execution_id: str, timeout: float = 2.0):
    import time
    executor = runner._executors.get(execution_id)
    if executor is None:
        return
    executor.shutdown(wait=True)
    # 重新放回一个可用的 executor 供后续 submit 使用（shutdown 后不能
    # 再 submit）——测试里改用直接调用 _run_step 而不是 submit()/线程池，
    # 避免这里处理 executor 生命周期的复杂度。


class TestRestartSummaryDisabledByDefault(unittest.TestCase):
    def test_disabled_does_not_compact_or_touch_disk(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _FakePaths(Path(tmp))
            cfg = _FakeAppConfig(enabled=False)
            agent = _FakeAgent()

            with patch.object(bridge_mod, "build_objective_agent", return_value=agent) as mock_build:
                runner = ObjectivePersistentRunner(
                    base_cfg=cfg, on_done=lambda *a, **k: None,
                    on_failed=lambda *a, **k: None, paths=paths,
                )
                runner._run_step("t1", "exec-1", "步骤1", {"objective_id": "obj"})
                mock_build.assert_called_once()
                _, kwargs = mock_build.call_args
                self.assertIsNone(kwargs.get("restart_summary"))

            self.assertEqual(agent.compact_calls, 0)
            self.assertFalse((Path(tmp) / "objective_worker_summaries").exists())


class TestRestartSummaryEnabled(unittest.TestCase):
    def test_step_completion_persists_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _FakePaths(Path(tmp))
            cfg = _FakeAppConfig(enabled=True)
            agent = _FakeAgent()

            with patch.object(bridge_mod, "build_objective_agent", return_value=agent):
                runner = ObjectivePersistentRunner(
                    base_cfg=cfg, on_done=lambda *a, **k: None,
                    on_failed=lambda *a, **k: None, paths=paths,
                )
                runner._run_step("t1", "exec-1", "步骤1", {"objective_id": "obj"})

            self.assertEqual(agent.compact_calls, 1)
            saved = bridge_mod.load_worker_restart_summary(paths, "exec-1")
            self.assertIn("摘要#1", saved)

    def test_restart_rebuild_reads_persisted_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _FakePaths(Path(tmp))
            cfg = _FakeAppConfig(enabled=True)
            agent1 = _FakeAgent()
            agent2 = _FakeAgent()

            with patch.object(bridge_mod, "build_objective_agent", side_effect=[agent1, agent2]) as mock_build:
                runner = ObjectivePersistentRunner(
                    base_cfg=cfg, on_done=lambda *a, **k: None,
                    on_failed=lambda *a, **k: None, paths=paths,
                )
                runner._run_step("t1", "exec-1", "步骤1", {"objective_id": "obj"})
                # 模拟 daemon 重启：内存里的 Agent 缓存清空，但磁盘摘要还在。
                with runner._lock:
                    runner._agents.pop("exec-1", None)
                runner._run_step("t2", "exec-1", "步骤2", {"objective_id": "obj"})

                self.assertEqual(mock_build.call_count, 2)
                _, second_kwargs = mock_build.call_args_list[1]
                self.assertIsNotNone(second_kwargs.get("restart_summary"))
                self.assertIn("摘要#1", second_kwargs["restart_summary"])

    def test_max_chars_truncation(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _FakePaths(Path(tmp))
            cfg = _FakeAppConfig(enabled=True, max_chars=10)

            class _LongAgent(_FakeAgent):
                def compact_with_skills(self, goal_hint: str = "") -> str:
                    self.compact_calls += 1
                    return "x" * 100

            agent = _LongAgent()
            with patch.object(bridge_mod, "build_objective_agent", return_value=agent):
                runner = ObjectivePersistentRunner(
                    base_cfg=cfg, on_done=lambda *a, **k: None,
                    on_failed=lambda *a, **k: None, paths=paths,
                )
                runner._run_step("t1", "exec-1", "步骤1", {"objective_id": "obj"})

            saved = bridge_mod.load_worker_restart_summary(paths, "exec-1")
            self.assertEqual(len(saved), 10)

    def test_release_discards_summary_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _FakePaths(Path(tmp))
            cfg = _FakeAppConfig(enabled=True)
            agent = _FakeAgent()

            with patch.object(bridge_mod, "build_objective_agent", return_value=agent):
                runner = ObjectivePersistentRunner(
                    base_cfg=cfg, on_done=lambda *a, **k: None,
                    on_failed=lambda *a, **k: None, paths=paths,
                )
                runner._run_step("t1", "exec-1", "步骤1", {"objective_id": "obj"})
                self.assertIsNotNone(bridge_mod.load_worker_restart_summary(paths, "exec-1"))
                runner.release("exec-1")
                self.assertIsNone(bridge_mod.load_worker_restart_summary(paths, "exec-1"))


class TestWithoutPaths(unittest.TestCase):
    def test_no_paths_disables_feature_even_if_config_enabled(self):
        cfg = _FakeAppConfig(enabled=True)
        agent = _FakeAgent()
        with patch.object(bridge_mod, "build_objective_agent", return_value=agent) as mock_build:
            runner = ObjectivePersistentRunner(
                base_cfg=cfg, on_done=lambda *a, **k: None,
                on_failed=lambda *a, **k: None,
                # paths 未传入
            )
            runner._run_step("t1", "exec-1", "步骤1", {"objective_id": "obj"})
            _, kwargs = mock_build.call_args
            self.assertIsNone(kwargs.get("restart_summary"))
        self.assertEqual(agent.compact_calls, 0)


if __name__ == "__main__":
    unittest.main()
