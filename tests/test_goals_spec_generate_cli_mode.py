"""tests/test_goals_spec_generate_cli_mode.py

覆盖 `/agent goals spec generate <id> [--mode llm|agent|auto]`（对应
next_doc/goal_execution_spec_generation_implementation_record.md §9
未实施清单第 2 条"CLI/看板未暴露单次覆盖 mode 的入口"——现已补上）：

- `--mode` 解析进 `_cmd_spec_generate`，透传进
  `GoalExecutionSpecBuilder(cfg, mode=...)` 构造函数；
- 不传 `--mode` 时透传 `None`（回退配置文件默认值）；
- 非法值被 argparse 的 `choices` 拦截，报用法错误，不调用生成器。
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import patch

import mini_agent.ui.renderer as R
from mini_agent.cli.commands.goals import _cmd_spec, _cmd_spec_generate
from mini_agent.perception.goal_backlog import GoalBacklog
from mini_agent.perception.goal_execution_spec import GoalExecutionSpec
from mini_agent.storage.paths import AgentPaths


class _Capture:
    def __init__(self):
        self.info = []
        self.error = []
        self.success = []
        self.warning = []


def _patch_renderer(monkeypatch, cap: _Capture):
    monkeypatch.setattr(R, "print_info", lambda msg: cap.info.append(msg))
    monkeypatch.setattr(R, "print_error", lambda msg: cap.error.append(msg))
    monkeypatch.setattr(R, "print_success", lambda msg: cap.success.append(msg))
    monkeypatch.setattr(R, "print_warning", lambda msg: cap.warning.append(msg))


def test_explicit_mode_forwarded_to_builder(monkeypatch):
    cap = _Capture()
    _patch_renderer(monkeypatch, cap)
    with tempfile.TemporaryDirectory() as tmp:
        paths = AgentPaths(project_root=Path(tmp))
        gb = GoalBacklog(paths)
        goal = gb.add_goal(title="周报生成", description="每周汇总数据")

        captured = {}

        class _FakeBuilder:
            def __init__(self, cfg, mode=None):
                captured["mode"] = mode
                self.last_effective_path = "agent"

            def build_draft(self, *a, **kw):
                return GoalExecutionSpec(version=1, goal_id=goal.id)

        with patch("mini_agent.perception.goal_execution_spec.GoalExecutionSpecBuilder", _FakeBuilder):
            with patch("mini_agent.config.load_config", return_value=object()):
                _cmd_spec(gb, paths, "generate", [goal.id, "--mode", "agent"])

        assert captured["mode"] == "agent"
        assert cap.success and "只读探索 Agent" in cap.success[0]


def test_no_mode_flag_passes_none(monkeypatch):
    cap = _Capture()
    _patch_renderer(monkeypatch, cap)
    with tempfile.TemporaryDirectory() as tmp:
        paths = AgentPaths(project_root=Path(tmp))
        gb = GoalBacklog(paths)
        goal = gb.add_goal(title="周报生成")

        captured = {}

        class _FakeBuilder:
            def __init__(self, cfg, mode=None):
                captured["mode"] = mode
                self.last_effective_path = "llm"

            def build_draft(self, *a, **kw):
                return GoalExecutionSpec(version=1, goal_id=goal.id)

        with patch("mini_agent.perception.goal_execution_spec.GoalExecutionSpecBuilder", _FakeBuilder):
            with patch("mini_agent.config.load_config", return_value=object()):
                _cmd_spec(gb, paths, "generate", [goal.id])

        assert captured["mode"] is None
        assert cap.success and "纯 LLM" in cap.success[0]


def test_invalid_mode_value_rejected_without_calling_generate(monkeypatch):
    cap = _Capture()
    _patch_renderer(monkeypatch, cap)
    with tempfile.TemporaryDirectory() as tmp:
        paths = AgentPaths(project_root=Path(tmp))
        gb = GoalBacklog(paths)
        goal = gb.add_goal(title="周报生成")

        called = []
        monkeypatch.setattr(
            "mini_agent.cli.commands.goals._cmd_spec_generate",
            lambda *a, **kw: called.append(True),
        )
        _cmd_spec(gb, paths, "generate", [goal.id, "--mode", "not_a_mode"])

        assert not called
        assert cap.error and "Usage" in cap.error[0]
