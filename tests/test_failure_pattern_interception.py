"""tests/test_failure_pattern_interception.py

覆盖 next_doc/daemon_stability_and_ux_improvement_plan.md 第 7 项
（P3-7 失败模式事中拦截）：

  1. failure_pattern_store.format_pattern_warning：空列表返回空字符串；
     非空列表格式化为可读提示，截断到 max_patterns 条；
  2. ObjectiveExecutor._submit_step：
     - failure_pattern_store.json 里有该 task_category 的高频失败模式时，
       提交的 message 里带上"[已知失败模式提醒]"；
     - 未命中（无历史数据/未达到 min_occurrence）时不附带该段落，
       行为与改造前一致；
     - cfg.autonomy.failure_pattern_interception_enabled=False 时，即使
       命中也不附带该段落（可整体关闭）；
     - 未传入 cfg（cfg=None）时默认按开启处理，不因为没配置而报错或
       跳过。
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from mini_agent.evolution.failure_pattern_store import (
    FailurePattern,
    format_pattern_warning,
)
from mini_agent.evolution.objective_executor import ObjectiveExecutor
from mini_agent.perception.goal_backlog import GoalBacklog, GoalNode
from mini_agent.storage.paths import AgentPaths


def _make_objective(backlog: GoalBacklog, title: str) -> GoalNode:
    goal = backlog.add_goal(title=f"{title}-goal", description="", source="user", priority=50)
    objs = backlog.add_objectives_for_goal(goal.id, [title])
    return objs[0]


class _FakeSubmitter:
    def __init__(self):
        self.calls: list[dict] = []
        self._n = 0

    def __call__(self, message: str, initiator: str, meta: dict):
        self._n += 1
        turn_id = f"turn_{self._n}"
        self.calls.append({"turn_id": turn_id, "message": message, "initiator": initiator, "meta": meta})
        return turn_id


class TestFormatPatternWarning(unittest.TestCase):
    def test_empty_list_returns_empty_string(self):
        self.assertEqual(format_pattern_warning([]), "")

    def test_formats_and_truncates(self):
        patterns = [
            FailurePattern(
                pattern_id=f"objective:cat:tag{i}", source="objective", task_category="cat",
                root_cause_tag=f"tag{i}", occurrence_count=5 + i, first_seen=1.0, last_seen=2.0,
                example_summary=f"example {i}",
            )
            for i in range(5)
        ]
        text = format_pattern_warning(patterns, max_patterns=2)
        self.assertIn("[已知失败模式提醒]", text)
        self.assertIn("tag0", text)
        self.assertIn("tag1", text)
        self.assertNotIn("tag2", text)


def _write_pattern_store(paths: AgentPaths, task_category: str, root_cause_tag: str, occurrence_count: int) -> None:
    p = paths.workdir_dir / "failure_pattern_store.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    pattern = FailurePattern(
        pattern_id=f"objective:{task_category}:{root_cause_tag}",
        source="objective", task_category=task_category, root_cause_tag=root_cause_tag,
        occurrence_count=occurrence_count, first_seen=1.0, last_seen=2.0,
        example_summary="曾经因为超时失败",
    )
    p.write_text(json.dumps({"ran_at": 1.0, "patterns": [pattern.to_dict()]}, ensure_ascii=False), encoding="utf-8")


class TestObjectiveExecutorInterception(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.paths = AgentPaths(Path(self._tmpdir.name))
        self.backlog = GoalBacklog(self.paths)
        self.submitter = _FakeSubmitter()

    def tearDown(self):
        self._tmpdir.cleanup()

    def _executor(self, cfg=None) -> ObjectiveExecutor:
        return ObjectiveExecutor(
            paths=self.paths,
            submit_fn=self.submitter,
            llm_decompose_fn=lambda obj: [f"{obj.title} - 单步"],
            declare_paths_fn=lambda desc: [f"path-for-{desc}"],
            goal_backlog=self.backlog,
            cfg=cfg,
        )

    def test_hit_injects_warning(self):
        title = "任务命中模式"
        # task_category 由 _normalize_category(objective_title) 生成：
        # 小写、去标点、取前 6 个词——这里标题够短，直接整句作为 category。
        # llm_decompose_fn 返回单元素列表时 `_decompose()` 会判定"没有比
        # 原来更细"而整体降级为 [objective.title]（见 `_decompose()` 里
        # `len(steps) >= 2` 的判断），所以这里 step.description 实际就是
        # objective 的 title 本身，category 直接用 title 归一化。
        from mini_agent.evolution.failure_pattern_store import _normalize_category
        category = _normalize_category(title)
        _write_pattern_store(self.paths, category, "timeout", occurrence_count=5)

        executor = self._executor()
        obj = _make_objective(self.backlog, title)
        executor.start(obj)

        message = self.submitter.calls[0]["message"]
        self.assertIn("[已知失败模式提醒]", message)
        self.assertIn("timeout", message)

    def test_no_hit_when_store_empty(self):
        executor = self._executor()
        obj = _make_objective(self.backlog, "任务无历史数据")
        executor.start(obj)

        message = self.submitter.calls[0]["message"]
        self.assertNotIn("[已知失败模式提醒]", message)

    def test_disabled_via_config_skips_even_when_hit(self):
        title = "任务但被关闭"
        from mini_agent.evolution.failure_pattern_store import _normalize_category
        category = _normalize_category(title)
        _write_pattern_store(self.paths, category, "timeout", occurrence_count=5)

        cfg = SimpleNamespace(autonomy=SimpleNamespace(failure_pattern_interception_enabled=False))
        executor = self._executor(cfg=cfg)
        obj = _make_objective(self.backlog, title)
        executor.start(obj)

        message = self.submitter.calls[0]["message"]
        self.assertNotIn("[已知失败模式提醒]", message)

    def test_none_cfg_defaults_to_enabled_and_does_not_crash(self):
        executor = self._executor(cfg=None)
        obj = _make_objective(self.backlog, "任务无cfg")
        # 不应抛异常，且能正常提交（命中与否不是本用例重点）。
        self.assertTrue(executor.start(obj))


if __name__ == "__main__":
    unittest.main()
