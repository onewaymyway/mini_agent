"""tests/test_desired_state_review_hint.py — 第十一轮 2.1 节：
`desired_state` 动态提示，`app.py::_should_prompt_desired_state_review()`
纯函数单测。

按方案验收要求（`next_doc/world_simulator_eleventh_round_remaining_
gaps_plan.md` 2.1 节）：只测"触发条件判断"这个纯函数，不测试
Streamlit 渲染本身（提示条实际展示效果由人工验证）。
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app  # noqa: E402  （world_simulator 项目根下的 Streamlit 入口）


def _state(capabilities_gained=None, problems=None):
    return SimpleNamespace(
        capabilities_gained=capabilities_gained,
        problems=problems,
    )


def test_no_capabilities_no_problems_does_not_trigger():
    state = _state(capabilities_gained=[], problems=[])
    assert app._should_prompt_desired_state_review(state) is False


def test_missing_fields_do_not_trigger():
    # 旧数据/未声明这两个字段（属性不存在，用 SimpleNamespace 模拟缺省 None）。
    state = SimpleNamespace()
    assert app._should_prompt_desired_state_review(state) is False


def test_non_empty_capabilities_gained_triggers():
    state = _state(
        capabilities_gained=[{"capability": "能够自动分析客户付费意愿"}],
        problems=[],
    )
    assert app._should_prompt_desired_state_review(state) is True


def test_problem_status_solved_triggers():
    state = _state(
        capabilities_gained=[],
        problems=[{"id": "p1", "status": "solved"}],
    )
    assert app._should_prompt_desired_state_review(state) is True


def test_problem_status_transformed_triggers():
    state = _state(
        capabilities_gained=[],
        problems=[{"id": "p1", "status": "transformed"}],
    )
    assert app._should_prompt_desired_state_review(state) is True


def test_problem_status_emerging_or_active_does_not_trigger():
    state = _state(
        capabilities_gained=[],
        problems=[
            {"id": "p1", "status": "emerging"},
            {"id": "p2", "status": "active"},
        ],
    )
    assert app._should_prompt_desired_state_review(state) is False


def test_malformed_problem_items_are_skipped_not_errored():
    state = _state(
        capabilities_gained=[],
        problems=["not_a_dict", {"id": "p1"}, None],
    )
    # 没有 status 字段 / 非 dict 条目都不应触发，也不应报错。
    assert app._should_prompt_desired_state_review(state) is False


def test_mixed_problems_one_solved_among_many_triggers():
    state = _state(
        capabilities_gained=[],
        problems=[
            {"id": "p1", "status": "active"},
            {"id": "p2", "status": "solved"},
        ],
    )
    assert app._should_prompt_desired_state_review(state) is True
