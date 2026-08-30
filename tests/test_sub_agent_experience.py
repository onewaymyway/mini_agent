"""tests/test_sub_agent_experience.py — 子 Agent 经历回写
（self_awareness_identity_evolution_plan.md §2.4）专属单测。
"""

from __future__ import annotations

import unittest
from pathlib import Path

from mini_agent.storage.paths import AgentPaths
from mini_agent.evolution.sub_agent_experience import (
    maybe_record_experience,
    load_recent_experiences,
)


def _make_paths(tmp: str) -> AgentPaths:
    return AgentPaths(project_root=Path(tmp))


class TestMaybeRecordExperience(unittest.TestCase):
    def test_success_no_signal_not_written(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            result = maybe_record_experience(
                paths, task_id="t1", task_name="normal task", status="DONE",
                error="", turns=3, tool_calls=5,
            )
            self.assertIsNone(result)
            self.assertFalse(paths.sub_agent_experience_log_path.exists())

    def test_failure_with_error_is_written(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            result = maybe_record_experience(
                paths, task_id="t2", task_name="broke task", status="FAILED",
                error="permission denied", turns=2, tool_calls=3,
            )
            self.assertIsNotNone(result)
            self.assertEqual(result["signal_type"], "failure")
            self.assertTrue(paths.sub_agent_experience_log_path.exists())

    def test_failure_without_error_text_not_written(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            result = maybe_record_experience(
                paths, task_id="t3", task_name="cancelled", status="FAILED",
                error="   ", turns=1, tool_calls=1,
            )
            self.assertIsNone(result)

    def test_high_turns_triggers_high_effort_signal(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            result = maybe_record_experience(
                paths, task_id="t4", task_name="grinding task", status="DONE",
                error="", turns=20, tool_calls=5,
            )
            self.assertIsNotNone(result)
            self.assertEqual(result["signal_type"], "high_effort")

    def test_high_tool_calls_triggers_high_effort_signal(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            result = maybe_record_experience(
                paths, task_id="t5", task_name="tool heavy task", status="DONE",
                error="", turns=5, tool_calls=40,
            )
            self.assertIsNotNone(result)
            self.assertEqual(result["signal_type"], "high_effort")

    def test_error_excerpt_truncated(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            long_error = "x" * 1000
            result = maybe_record_experience(
                paths, task_id="t6", task_name="long error", status="FAILED",
                error=long_error, turns=1, tool_calls=1,
            )
            self.assertLessEqual(len(result["error_excerpt"]), 300)


class TestLoadRecentExperiences(unittest.TestCase):
    def test_empty_when_no_log(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            self.assertEqual(load_recent_experiences(paths), [])

    def test_returns_most_recent_first_and_respects_limit(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            for i in range(5):
                maybe_record_experience(
                    paths, task_id=f"t{i}", task_name=f"task {i}", status="FAILED",
                    error=f"err {i}", turns=1, tool_calls=1,
                )
            recent = load_recent_experiences(paths, limit=3)
            self.assertEqual(len(recent), 3)
            # 最后写入的应该排在最前（倒序）
            self.assertEqual(recent[0]["task_id"], "t4")


if __name__ == "__main__":
    unittest.main()
