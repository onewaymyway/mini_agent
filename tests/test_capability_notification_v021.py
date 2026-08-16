"""v0.21 §8 通知系统接入单元测试。

覆盖 next_doc/persona_capability_learning_design.md「后续计划（v0.21）」
第 1 项：`maybe_dispatch_capability_notification()` 的节流/开关/空轮判定。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from mini_agent.storage.paths import AgentPaths
from mini_agent.evolution.capability_learning import (
    maybe_dispatch_capability_notification,
    _load_capability_notify_state,
)


@pytest.fixture
def paths(tmp_path):
    return AgentPaths(project_root=tmp_path)


def _cfg(**overrides):
    base = dict(notification_enabled=True, notification_frequency="daily", notification_max_per_day=1)
    base.update(overrides)
    return SimpleNamespace(**base)


def test_empty_cycle_does_not_notify(paths):
    summary = {"questions_raised": 0, "topics_researched": 0}
    result = maybe_dispatch_capability_notification(paths, _cfg(), summary, pending_questions_count=0)
    assert result is None
    # 空轮不应该占用/初始化节流状态
    assert _load_capability_notify_state(paths) == {}


def test_new_pages_triggers_notification(paths):
    summary = {"questions_raised": 0, "topics_researched": 2}
    result = maybe_dispatch_capability_notification(paths, _cfg(), summary, pending_questions_count=3)
    assert result is not None
    assert result["sent"] is True
    state = _load_capability_notify_state(paths)
    assert state["notify_count_today"] == 1


def test_throttled_after_max_per_day(paths):
    summary = {"questions_raised": 1, "topics_researched": 0}
    cfg = _cfg(notification_max_per_day=1)
    first = maybe_dispatch_capability_notification(paths, cfg, summary, pending_questions_count=1)
    assert first is not None
    second = maybe_dispatch_capability_notification(paths, cfg, summary, pending_questions_count=1)
    assert second is None  # 当天额度已用完


def test_kanban_only_never_notifies(paths):
    summary = {"questions_raised": 1, "topics_researched": 1}
    cfg = _cfg(notification_frequency="kanban_only")
    result = maybe_dispatch_capability_notification(paths, cfg, summary, pending_questions_count=1)
    assert result is None


def test_notification_disabled(paths):
    summary = {"questions_raised": 1, "topics_researched": 1}
    cfg = _cfg(notification_enabled=False)
    result = maybe_dispatch_capability_notification(paths, cfg, summary, pending_questions_count=1)
    assert result is None


def test_none_cfg_uses_defaults(paths):
    summary = {"questions_raised": 0, "topics_researched": 1}
    result = maybe_dispatch_capability_notification(paths, None, summary, pending_questions_count=0)
    assert result is not None
