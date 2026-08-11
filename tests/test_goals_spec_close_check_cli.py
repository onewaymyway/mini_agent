"""tests/test_goals_spec_close_check_cli.py

覆盖 next_doc/goal_execution_spec_generation_implementation_record.md
§3 第 6 条新增的 `/agent goals spec close-check <goal_id>` 手动重触发入口
（cli/commands/goals.py::_cmd_spec_close_check）。

用 monkeypatch 拦截 `mini_agent.ui.renderer` 的输出函数 + 直接调用内部
`_cmd_spec_close_check()`（不经过 handle_goals_cmd 的完整 slash 解析，
保持测试聚焦在这一个子命令本身），验证：
  - Goal 不存在时报错
  - Goal 非 active 时提示跳过，不调用 maybe_close_goal_by_overall_criteria
  - 返回 None（前置条件不满足）时给出说明性提示
  - 返回 "closed"/"kept_open" 时分别给出对应提示
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import mini_agent.ui.renderer as R
from mini_agent.cli.commands.goals import _cmd_spec_close_check
from mini_agent.perception.goal_backlog import GoalBacklog
from mini_agent.storage.paths import AgentPaths


class _Capture:
    def __init__(self):
        self.info = []
        self.error = []
        self.success = []


def _patch_renderer(monkeypatch, cap: _Capture):
    monkeypatch.setattr(R, "print_info", lambda msg: cap.info.append(msg))
    monkeypatch.setattr(R, "print_error", lambda msg: cap.error.append(msg))
    monkeypatch.setattr(R, "print_success", lambda msg: cap.success.append(msg))


def test_goal_not_found_reports_error(monkeypatch):
    cap = _Capture()
    _patch_renderer(monkeypatch, cap)
    with tempfile.TemporaryDirectory() as tmp:
        paths = AgentPaths(project_root=Path(tmp))
        gb = GoalBacklog(paths)
        _cmd_spec_close_check(gb, paths, "goal_not_exist")
    assert cap.error and "不存在" in cap.error[0]


def test_non_active_goal_is_skipped_without_calling_backlog(monkeypatch):
    cap = _Capture()
    _patch_renderer(monkeypatch, cap)
    with tempfile.TemporaryDirectory() as tmp:
        paths = AgentPaths(project_root=Path(tmp))
        gb = GoalBacklog(paths)
        goal = gb.add_goal(title="已放弃的目标")
        gb.set_status(goal.id, "abandoned")

        called = []
        monkeypatch.setattr(
            gb, "maybe_close_goal_by_overall_criteria",
            lambda gid, cfg=None, use_agent=None: called.append(gid) or "closed",
        )
        _cmd_spec_close_check(gb, paths, goal.id)

    assert not called
    assert cap.info and "abandoned" in cap.info[0]


def test_none_outcome_gives_explanatory_hint(monkeypatch):
    cap = _Capture()
    _patch_renderer(monkeypatch, cap)
    with tempfile.TemporaryDirectory() as tmp:
        paths = AgentPaths(project_root=Path(tmp))
        gb = GoalBacklog(paths)
        goal = gb.add_goal(title="一次性目标")

        monkeypatch.setattr(gb, "maybe_close_goal_by_overall_criteria", lambda gid, cfg=None, use_agent=None: None)
        monkeypatch.setattr("mini_agent.config.load_config", lambda: object())
        _cmd_spec_close_check(gb, paths, goal.id)

    assert cap.info and "未触发判定" in cap.info[0]


def test_closed_outcome_reports_success(monkeypatch):
    cap = _Capture()
    _patch_renderer(monkeypatch, cap)
    with tempfile.TemporaryDirectory() as tmp:
        paths = AgentPaths(project_root=Path(tmp))
        gb = GoalBacklog(paths)
        goal = gb.add_goal(title="一次性目标")

        monkeypatch.setattr(gb, "maybe_close_goal_by_overall_criteria", lambda gid, cfg=None, use_agent=None: "closed")
        monkeypatch.setattr("mini_agent.config.load_config", lambda: object())
        _cmd_spec_close_check(gb, paths, goal.id)

    assert cap.success and "completed" in cap.success[0]


def test_kept_open_outcome_reports_info(monkeypatch):
    cap = _Capture()
    _patch_renderer(monkeypatch, cap)
    with tempfile.TemporaryDirectory() as tmp:
        paths = AgentPaths(project_root=Path(tmp))
        gb = GoalBacklog(paths)
        goal = gb.add_goal(title="一次性目标")

        monkeypatch.setattr(gb, "maybe_close_goal_by_overall_criteria", lambda gid, cfg=None, use_agent=None: "kept_open")
        monkeypatch.setattr("mini_agent.config.load_config", lambda: object())
        _cmd_spec_close_check(gb, paths, goal.id)

    assert cap.info and "暂不关闭" in cap.info[0]


def test_use_agent_flag_forwarded_to_backlog(monkeypatch):
    """[implementation_record.md §11 后续建议顺序第 2 条] `--use-agent`/
    `--no-agent` 解析出的 `use_agent` 参数正确透传给
    `maybe_close_goal_by_overall_criteria(use_agent=...)`。"""
    cap = _Capture()
    _patch_renderer(monkeypatch, cap)
    with tempfile.TemporaryDirectory() as tmp:
        paths = AgentPaths(project_root=Path(tmp))
        gb = GoalBacklog(paths)
        goal = gb.add_goal(title="一次性目标")

        received = {}

        def _fake(gid, cfg=None, use_agent=None):
            received["use_agent"] = use_agent
            return "kept_open"

        monkeypatch.setattr(gb, "maybe_close_goal_by_overall_criteria", _fake)
        monkeypatch.setattr("mini_agent.config.load_config", lambda: object())

        _cmd_spec_close_check(gb, paths, goal.id, use_agent=True)
        assert received["use_agent"] is True

        _cmd_spec_close_check(gb, paths, goal.id, use_agent=False)
        assert received["use_agent"] is False

        _cmd_spec_close_check(gb, paths, goal.id)
        assert received["use_agent"] is None


def test_close_check_reports_path_from_persisted_last_check(monkeypatch):
    """[implementation_record.md §11 后续建议顺序第 1 条] 判定后
    `GoalNode.overall_completion_last_check.used_agent` 决定输出提示里
    展示"只读探索 Agent"还是"纯 LLM"。"""
    cap = _Capture()
    _patch_renderer(monkeypatch, cap)
    with tempfile.TemporaryDirectory() as tmp:
        paths = AgentPaths(project_root=Path(tmp))
        gb = GoalBacklog(paths)
        goal = gb.add_goal(title="一次性目标")

        def _fake(gid, cfg=None, use_agent=None):
            gb.update_fields(
                gid,
                overall_completion_last_check={
                    "outcome": "closed", "reasoning": "ok", "used_agent": True, "at": 1.0,
                },
            )
            return "closed"

        monkeypatch.setattr(gb, "maybe_close_goal_by_overall_criteria", _fake)
        monkeypatch.setattr("mini_agent.config.load_config", lambda: object())
        _cmd_spec_close_check(gb, paths, goal.id)

    assert cap.success and "只读探索 Agent" in cap.success[0]
