"""tests/test_goal_output_directory_onetime.py

覆盖 next_doc/goal_cron_output_directory_convention_plan.md §5 开放问题 3
的最终结论：一次性（非 recurring）Goal 的子 Objective 也套用产出目录规范。

    1. GoalBacklog.add_objectives_for_goal() 对一次性 Goal 的每个子 Objective
       分配 goals/<goal_id>/run_%04d/ 目录，并把"本轮产出请写入：<目录>"拼进
       description；第二个及以后的子 Objective 还应带上前一个子 Objective 的
       产出摘要（如果前一个已经落过 manifest）。
    2. recurring Goal 不受影响：仍然只在 goal_cron_bridge._fire_goal_cycle()
       触发时分配 cycle_%04d 目录，add_objectives_for_goal() 对 recurring
       Goal 不追加"本轮产出请写入"。
    3. ObjectiveExecutor._write_output_manifest() 对一次性 Goal 的子 Objective
       收尾时，把 manifest 写进与 ①相同规则算出的 run_%04d/manifest.json，
       ordinal 按 children_ids 里的位置计算，与分配目录时的 ordinal 一致。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mini_agent.evolution import output_workspace
from mini_agent.evolution.objective_executor import ObjectiveExecution, ObjectiveExecutor
from mini_agent.perception.goal_backlog import GoalBacklog
from mini_agent.storage.paths import AgentPaths


def _make_paths(tmp: str) -> AgentPaths:
    return AgentPaths(Path(tmp))


class TestOnetimeGoalAddObjectives(unittest.TestCase):
    def test_allocates_run_dir_and_appends_hint(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            backlog = GoalBacklog(paths)
            goal = backlog.add_goal(title="一次性目标", description="做一件事")
            self.assertFalse(goal.recurring)

            created = backlog.add_objectives_for_goal(goal.id, ["第一步", "第二步"])
            self.assertEqual(len(created), 2)

            first, second = created
            self.assertIn(f"run_0001", first.description)
            self.assertIn("本轮产出请写入：", first.description)
            self.assertIn(f"run_0002", second.description)

            base_dir = output_workspace.goal_output_base_dir(paths, goal.id)
            self.assertTrue((base_dir / "run_0001").is_dir())
            self.assertTrue((base_dir / "run_0002").is_dir())

    def test_second_objective_sees_previous_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            backlog = GoalBacklog(paths)
            goal = backlog.add_goal(title="一次性目标", description="做一件事")

            [first] = backlog.add_objectives_for_goal(goal.id, ["第一步"])
            base_dir = output_workspace.goal_output_base_dir(paths, goal.id)
            run1_dir = base_dir / "run_0001"
            output_workspace.write_manifest(
                base_dir, run1_dir,
                task_summary="第一步", status="completed",
                artifacts=[{"path": "step1_output.md", "description": "第一步产出"}],
            )

            [second] = backlog.add_objectives_for_goal(goal.id, ["第二步"])
            self.assertIn("上一个子任务产出", second.description)
            self.assertIn("step1_output.md", second.description)
            self.assertIn("run_0002", second.description)

    def test_recurring_goal_not_affected(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            backlog = GoalBacklog(paths)
            goal = backlog.add_goal(title="周期性目标", description="持续做")
            backlog.update_fields(goal.id, recurring=True)

            [child] = backlog.add_objectives_for_goal(goal.id, ["拆解出的一步"])
            self.assertNotIn("本轮产出请写入：", child.description)
            base_dir = output_workspace.goal_output_base_dir(paths, goal.id)
            self.assertFalse((base_dir / "run_0001").exists())


class TestOnetimeGoalManifestOnCompletion(unittest.TestCase):
    def _make_executor(self, paths, backlog) -> ObjectiveExecutor:
        return ObjectiveExecutor(paths, goal_backlog=backlog)

    def test_write_output_manifest_uses_run_dir_matching_ordinal(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            backlog = GoalBacklog(paths)
            goal = backlog.add_goal(title="一次性目标", description="做一件事")
            first, second = backlog.add_objectives_for_goal(goal.id, ["第一步", "第二步"])

            executor = self._make_executor(paths, backlog)

            ex = ObjectiveExecution(
                execution_id="exec_1",
                objective_id=second.id,
                objective_title=second.title,
                status="completed",
                started_at=1.0,
                finished_at=2.0,
                progress_notes="已完成",
            )
            executor._write_output_manifest(ex, "completed")

            base_dir = output_workspace.goal_output_base_dir(paths, goal.id)
            manifest_path = base_dir / "run_0002" / "manifest.json"
            self.assertTrue(manifest_path.exists(), "第二个子 Objective 应写进 run_0002")

            latest = output_workspace.read_latest_manifest(base_dir)
            self.assertIsNotNone(latest)
            self.assertEqual(latest["dir_name"], "run_0002")
            self.assertEqual(latest["status"], "completed")


if __name__ == "__main__":
    unittest.main()
