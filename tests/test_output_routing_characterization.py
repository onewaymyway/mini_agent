"""P4 特征测试：目标产出目录解析的现有行为。

对应 next_doc/management_output_project_model_plan.md 第 8.3 节与第 9 章 P4。

这些测试在改造前后都必须通过：
  - 未绑定产出项目的目标，产出目录与改造前完全一致；
  - 用户显式设置的 user_output_dir 始终优先；
  - notes / spec / scratch 等内部目录不受绑定影响。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mini_agent.evolution import output_workspace as ow
from mini_agent.storage.paths import AgentPaths


@pytest.fixture
def paths(tmp_path: Path) -> AgentPaths:
    (tmp_path / ".agent").mkdir()
    return AgentPaths(project_root=tmp_path)


def _write_goals(paths: AgentPaths, nodes: list[dict]) -> None:
    (Path(paths.workdir_dir) / "goals.json").write_text(
        json.dumps({"version": 1, "goals": nodes, "directions": []}, ensure_ascii=False),
        encoding="utf-8",
    )


def test_unbound_goal_uses_default_output_dir(paths: AgentPaths):
    expected = ow.goal_output_base_dir(paths, "g1") / "output"
    assert ow.goal_output_dir(paths, "g1") == expected


def test_unbound_goal_with_goals_json_still_uses_default(paths: AgentPaths):
    _write_goals(paths, [
        {"id": "g1", "level": "goal", "title": "t", "status": "active", "parent_id": None},
    ])
    assert ow.goal_output_dir(paths, "g1") == ow.goal_output_base_dir(paths, "g1") / "output"


def test_explicit_relative_user_output_dir_is_honored(paths: AgentPaths):
    got = ow.goal_output_dir(paths, "g1", user_output_dir="research/x")
    assert got == Path(paths.project_root) / "research/x"


def test_explicit_absolute_user_output_dir_is_honored(paths: AgentPaths, tmp_path: Path):
    target = tmp_path / "elsewhere"
    assert ow.goal_output_dir(paths, "g1", user_output_dir=str(target)) == target


def test_empty_user_output_dir_falls_back_to_default(paths: AgentPaths):
    assert ow.goal_output_dir(paths, "g1", user_output_dir="") == ow.goal_output_base_dir(paths, "g1") / "output"


def test_internal_dirs_ignore_user_output_dir(paths: AgentPaths):
    notes_before = ow.goal_notes_dir(paths, "g1")
    spec_before = ow.goal_spec_dir(paths, "g1")
    ow.goal_output_dir(paths, "g1", user_output_dir="research/x")
    assert ow.goal_notes_dir(paths, "g1") == notes_before
    assert ow.goal_spec_dir(paths, "g1") == spec_before
