"""
tests/test_gating_history_active_recording.py

覆盖 next_doc/daemon_stability_and_ux_improvement_plan.md 第 4 项
（仲裁状态时间线的被动记录问题）：

- ResourceArbiter.gating_state() 本身在计算出结果后，应主动落盘一条
  gating_history 记录——不依赖任何读接口（如 /v1/autonomous/status /
  diagnose()）被外部轮询到。
- 状态未变化时不重复写入（沿用 record_gating_transition 已有的去重逻辑）。
- 覆盖 gating_state() 的所有返回分支（budget blocked / degraded 关闭时的
  二元退化路径 / 三态路径下的 blocked、degraded、full），确认每个分支都
  会记录。

运行方式：
    PYTHONPATH=src python3 -m pytest tests/test_gating_history_active_recording.py -q
"""

from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from mini_agent.evolution.resource_arbiter import ResourceArbiter, read_gating_history
from mini_agent.storage.paths import AgentPaths


def _make_cfg(autonomy_overrides: dict | None = None) -> SimpleNamespace:
    autonomy = dict(
        resource_gating_degraded_enabled=True,
        frustration_blocked_threshold=0.85,
        behavior_gating_enabled=False,
        behavior_gating_switch_threshold=3,
    )
    autonomy.update(autonomy_overrides or {})
    return SimpleNamespace(
        proprioception=SimpleNamespace(frustration_threshold=0.5),
        autonomy=SimpleNamespace(**autonomy),
    )


class TestGatingStateActiveRecording(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.paths = AgentPaths(Path(self._tmpdir.name))

    def tearDown(self):
        self._tmpdir.cleanup()

    def _write_frustration_snapshot(self, frustration: float) -> None:
        snapshot_path = self.paths.proprioception_snapshot
        snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        snapshot_path.write_text(
            json.dumps({"frustration": frustration, "updated_at": time.time()}),
            encoding="utf-8",
        )

    def test_calling_gating_state_alone_writes_history_no_polling_needed(self):
        """核心场景：只调用 gating_state()（模拟 AutonomousLoop tick），
        不触碰任何 HTTP/diagnose 路径，历史文件也应该被写入。"""
        cfg = _make_cfg()
        arbiter = ResourceArbiter(self.paths, cfg)

        self.assertFalse(self.paths.gating_history_path.exists())
        state = arbiter.gating_state()
        self.assertEqual(state["state"], "full")

        history = read_gating_history(self.paths)
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["state"], "full")

    def test_transition_is_recorded_on_change(self):
        cfg = _make_cfg()
        arbiter = ResourceArbiter(self.paths, cfg)

        arbiter.gating_state()  # full
        self._write_frustration_snapshot(0.6)  # -> degraded
        arbiter.gating_state()
        self._write_frustration_snapshot(0.9)  # -> blocked
        arbiter.gating_state()

        history = read_gating_history(self.paths)
        self.assertEqual([h["state"] for h in history], ["full", "degraded", "blocked"])

    def test_unchanged_state_not_duplicated(self):
        cfg = _make_cfg()
        arbiter = ResourceArbiter(self.paths, cfg)
        for _ in range(5):
            arbiter.gating_state()
        history = read_gating_history(self.paths)
        self.assertEqual(len(history), 1)

    def test_budget_blocked_branch_is_recorded(self):
        cfg = _make_cfg()
        arbiter = ResourceArbiter(self.paths, cfg)
        arbiter._check_budget = lambda: False  # 强制预算耗尽分支
        state = arbiter.gating_state()
        self.assertEqual(state["state"], "blocked")
        history = read_gating_history(self.paths)
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["state"], "blocked")

    def test_degraded_disabled_binary_fallback_branch_is_recorded(self):
        cfg = _make_cfg(autonomy_overrides={"resource_gating_degraded_enabled": False})
        arbiter = ResourceArbiter(self.paths, cfg)
        self._write_frustration_snapshot(0.6)  # 三态下是 degraded，二元退化后应是 blocked
        state = arbiter.gating_state()
        self.assertEqual(state["state"], "blocked")
        history = read_gating_history(self.paths)
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["state"], "blocked")


if __name__ == "__main__":
    unittest.main()
