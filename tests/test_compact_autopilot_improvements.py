"""
tests/test_compact_autopilot_improvements.py

覆盖 compact_mechanism_improvement_plan.md P0-A / P0-B 两项改造：

  P0-A — goal_mode 卡住恢复 compact 时，把未通过的验收标准作为提示
         传给 compact_with_skills(goal_hint=...)（GoalRunner._build_goal_aware_compact_hint）
  P0-B — compact_with_skills() 剥离并解析 ===DECISIONS_JSON=== 块，
         入队交给巩固循环批量落盘（CompactionMixin._extract_and_queue_decisions_from_compact_result）

两项默认关闭，本文件同时验证"开关关闭时行为不变"和"开关打开时行为符合预期"。
"""

import sys
import json

sys.path.insert(0, "src")

import pytest

from mini_agent.goal_mode.runner import GoalRunner
from mini_agent.agent.compaction import CompactionMixin

from tests.test_goal_mode import FakeAgent, _FakeCfg, _confirmed_spec


# ════════════════════════════════════════════════════════════════════════════
# P0-A: goal-aware compact hint
# ════════════════════════════════════════════════════════════════════════════

class _CompressCfg:
    def __init__(self, goal_aware_weighting_enabled=False):
        self.goal_aware_weighting_enabled = goal_aware_weighting_enabled


def _make_runner_with_criteria(tmp_path, goal_aware_enabled: bool, criteria_status):
    agent = FakeAgent(outputs=["attempt 1"])
    cfg = _FakeCfg(tmp_path)
    cfg.compress = _CompressCfg(goal_aware_weighting_enabled=goal_aware_enabled)
    spec = _confirmed_spec()
    runner = GoalRunner(agent=agent, cfg=cfg, goal_spec=spec)
    runner._criteria_status = criteria_status
    return runner, agent


def test_goal_aware_hint_empty_when_disabled(tmp_path):
    runner, _ = _make_runner_with_criteria(
        tmp_path,
        goal_aware_enabled=False,
        criteria_status=[{"text": "输出必须包含摘要", "passed": False}],
    )
    assert runner._build_goal_aware_compact_hint() == ""


def test_goal_aware_hint_empty_when_no_unmet_criteria(tmp_path):
    runner, _ = _make_runner_with_criteria(
        tmp_path,
        goal_aware_enabled=True,
        criteria_status=[{"text": "输出必须包含摘要", "passed": True}],
    )
    assert runner._build_goal_aware_compact_hint() == ""


def test_goal_aware_hint_contains_unmet_criteria_text(tmp_path):
    runner, _ = _make_runner_with_criteria(
        tmp_path,
        goal_aware_enabled=True,
        criteria_status=[
            {"text": "输出必须包含摘要", "passed": True},
            {"text": "必须通过 pytest 全量测试", "passed": False},
        ],
    )
    hint = runner._build_goal_aware_compact_hint()
    assert "必须通过 pytest 全量测试" in hint
    assert "输出必须包含摘要" not in hint  # 已通过的标准不应出现在提示里


def test_goal_aware_hint_missing_compress_cfg_is_safe(tmp_path):
    """_FakeCfg 默认没有 .compress 属性，验证 getattr 兜底不抛异常，返回空字符串。"""
    agent = FakeAgent(outputs=["attempt 1"])
    cfg = _FakeCfg(tmp_path)  # 不设置 cfg.compress
    spec = _confirmed_spec()
    runner = GoalRunner(agent=agent, cfg=cfg, goal_spec=spec)
    runner._criteria_status = [{"text": "must pass", "passed": False}]
    assert runner._build_goal_aware_compact_hint() == ""


def test_do_compact_passes_goal_hint_through_to_agent(tmp_path):
    runner, agent = _make_runner_with_criteria(
        tmp_path,
        goal_aware_enabled=True,
        criteria_status=[{"text": "必须修复 bug X", "passed": False}],
    )
    runner._do_compact()
    assert agent.compact_calls == 1
    assert "必须修复 bug X" in agent.last_goal_hint


def test_do_compact_passes_empty_hint_when_disabled(tmp_path):
    runner, agent = _make_runner_with_criteria(
        tmp_path,
        goal_aware_enabled=False,
        criteria_status=[{"text": "必须修复 bug X", "passed": False}],
    )
    runner._do_compact()
    assert agent.compact_calls == 1
    assert agent.last_goal_hint == ""


# ════════════════════════════════════════════════════════════════════════════
# P0-B: decision extraction on compact_with_skills()
# ════════════════════════════════════════════════════════════════════════════

class _FakeCompressCfgForExtraction:
    project_root = None


class _FakeTopCfg:
    def __init__(self):
        self.compress = _FakeCompressCfgForExtraction()
        self.project_root = None


class _ExtractionHost(CompactionMixin):
    """最小宿主：只提供 _extract_and_queue_decisions_from_compact_result 依赖的属性。"""

    def __init__(self):
        self.cfg = _FakeTopCfg()
        self._history = [1, 2, 3]  # 只用于 len()，内容无关


def test_extract_decisions_no_block_returns_original_text():
    host = _ExtractionHost()
    text = "## Goal\nDo the thing.\n\n## Work Completed\nFixed the bug."
    result = host._extract_and_queue_decisions_from_compact_result(text)
    assert result == text


def test_extract_decisions_strips_block_and_queues(monkeypatch, tmp_path):
    host = _ExtractionHost()
    host.cfg.project_root = tmp_path

    captured = {}

    def _fake_queue_candidates(paths, candidates, *, source_entries=None):
        captured["candidates"] = candidates
        captured["source_entries"] = source_entries

    monkeypatch.setattr(
        "mini_agent.wiki.decision_writer.queue_candidates",
        _fake_queue_candidates,
    )

    decisions_payload = {
        "decisions": [
            {
                "topic": "选择压缩策略",
                "options_considered": ["turn_aligned", "selective"],
                "chosen": "selective",
                "rejected_because": {"turn_aligned": "不区分内容价值"},
                "related_entities": ["history/compression.py"],
            },
            {
                # 缺 chosen，is_meaningful 应为 False，被过滤掉
                "topic": "无意义候选",
                "options_considered": [],
                "chosen": "",
            },
        ]
    }
    block = (
        "\n\n===DECISIONS_JSON===\n"
        + json.dumps(decisions_payload, ensure_ascii=False)
        + "\n===END_DECISIONS_JSON==="
    )
    text = "## Goal\nDo the thing.\n\n## Work Completed\nFixed the bug." + block

    result = host._extract_and_queue_decisions_from_compact_result(text)

    assert "===DECISIONS_JSON===" not in result
    assert "Fixed the bug." in result
    assert len(captured["candidates"]) == 1
    assert captured["candidates"][0].topic == "选择压缩策略"
    assert captured["source_entries"] == ["compact_with_skills@3"]


def test_extract_decisions_malformed_json_falls_back_safely():
    host = _ExtractionHost()
    text = (
        "## Goal\nDo the thing.\n\n"
        "===DECISIONS_JSON===\nnot valid json{{{\n===END_DECISIONS_JSON==="
    )
    # 不应抛异常；剥离掉（哪怕内容非法）JSON 块，返回清理后的摘要
    result = host._extract_and_queue_decisions_from_compact_result(text)
    assert "===DECISIONS_JSON===" not in result
    assert "Do the thing." in result


def test_extract_decisions_empty_decisions_array_queues_nothing(monkeypatch):
    host = _ExtractionHost()
    called = {"count": 0}

    def _fake_queue_candidates(*args, **kwargs):
        called["count"] += 1

    monkeypatch.setattr(
        "mini_agent.wiki.decision_writer.queue_candidates",
        _fake_queue_candidates,
    )
    text = (
        "## Goal\nDo the thing.\n\n"
        "===DECISIONS_JSON===\n{\"decisions\": []}\n===END_DECISIONS_JSON==="
    )
    result = host._extract_and_queue_decisions_from_compact_result(text)
    assert "===DECISIONS_JSON===" not in result
    assert called["count"] == 0
