"""
tests/test_judge_verdict.py — role_agents/verdict.py::parse_judge_verdict 单元测试

覆盖范围：
  - 合法 JSON（含代码块围栏/夹杂说明文字/大小写不敏感）正确解析
  - 非 JSON / 缺 status 字段 / status 不在白名单 → parse_ok=False + fallback_status
  - extra 字段透传（如 GoalJudge 可能附带的 checklist）
  - feedback.py::extract_goal_status / extract_turn_status 的委托 + 旧格式兼容
"""

from __future__ import annotations

import pytest

from mini_agent.role_agents.verdict import JudgeVerdict, parse_judge_verdict


GOAL_STATUSES = ["DONE", "CONTINUE", "NEED_COMPACT"]
TURN_STATUSES = ["NEED_USER", "AUTO_CONTINUE", "NEED_COMPACT"]


# ── 合法 JSON 解析 ────────────────────────────────────────────────────────

def test_parse_plain_json():
    v = parse_judge_verdict(
        '{"status": "DONE", "feedback": "all good"}',
        valid_statuses=GOAL_STATUSES, fallback_status="CONTINUE",
    )
    assert v.parse_ok is True
    assert v.status == "DONE"
    assert v.feedback == "all good"


def test_parse_json_wrapped_in_code_fence():
    text = '```json\n{"status": "CONTINUE", "feedback": "do X"}\n```'
    v = parse_judge_verdict(text, valid_statuses=GOAL_STATUSES, fallback_status="CONTINUE")
    assert v.parse_ok is True
    assert v.status == "CONTINUE"
    assert v.feedback == "do X"


def test_parse_json_with_surrounding_prose():
    text = 'Here is my verdict: {"status": "need_compact", "feedback": "too messy"} done.'
    v = parse_judge_verdict(text, valid_statuses=GOAL_STATUSES, fallback_status="CONTINUE")
    assert v.parse_ok is True
    assert v.status == "NEED_COMPACT"  # 大小写归一化


def test_parse_status_case_insensitive_against_whitelist():
    v = parse_judge_verdict(
        '{"status": "done", "feedback": "x"}',
        valid_statuses=GOAL_STATUSES, fallback_status="CONTINUE",
    )
    assert v.status == "DONE"


def test_extra_fields_are_preserved():
    v = parse_judge_verdict(
        '{"status": "CONTINUE", "feedback": "x", "checklist": [{"c": "a", "passed": false}]}',
        valid_statuses=GOAL_STATUSES, fallback_status="CONTINUE",
    )
    assert v.parse_ok is True
    assert "checklist" in v.extra
    assert v.extra["checklist"] == [{"c": "a", "passed": False}]


def test_custom_status_and_feedback_keys():
    v = parse_judge_verdict(
        '{"verdict": "DONE", "reason": "ok"}',
        valid_statuses=GOAL_STATUSES, fallback_status="CONTINUE",
        status_key="verdict", feedback_key="reason",
    )
    assert v.parse_ok is True
    assert v.status == "DONE"
    assert v.feedback == "ok"


# ── 解析失败 → 保守 fallback ─────────────────────────────────────────────

def test_non_json_text_falls_back():
    v = parse_judge_verdict(
        "blah\nGOAL_STATUS: DONE\n",
        valid_statuses=GOAL_STATUSES, fallback_status="CONTINUE",
    )
    assert v.parse_ok is False
    assert v.status == "CONTINUE"
    assert v.feedback == ""
    assert v.raw == "blah\nGOAL_STATUS: DONE\n"


def test_missing_status_field_falls_back():
    v = parse_judge_verdict(
        '{"feedback": "no status here"}',
        valid_statuses=GOAL_STATUSES, fallback_status="CONTINUE",
    )
    assert v.parse_ok is False
    assert v.status == "CONTINUE"


def test_status_not_in_whitelist_falls_back():
    v = parse_judge_verdict(
        '{"status": "MAYBE", "feedback": "x"}',
        valid_statuses=GOAL_STATUSES, fallback_status="CONTINUE",
    )
    assert v.parse_ok is False
    assert v.status == "CONTINUE"


def test_empty_string_falls_back():
    v = parse_judge_verdict("", valid_statuses=GOAL_STATUSES, fallback_status="CONTINUE")
    assert v.parse_ok is False
    assert v.status == "CONTINUE"


def test_non_string_status_falls_back():
    v = parse_judge_verdict(
        '{"status": 123, "feedback": "x"}',
        valid_statuses=GOAL_STATUSES, fallback_status="CONTINUE",
    )
    assert v.parse_ok is False
    assert v.status == "CONTINUE"


def test_json_array_falls_back():
    v = parse_judge_verdict("[1, 2, 3]", valid_statuses=GOAL_STATUSES, fallback_status="CONTINUE")
    assert v.parse_ok is False


def test_feedback_defaults_to_empty_string_when_missing():
    v = parse_judge_verdict(
        '{"status": "DONE"}', valid_statuses=GOAL_STATUSES, fallback_status="CONTINUE",
    )
    assert v.parse_ok is True
    assert v.feedback == ""


def test_non_string_feedback_is_stringified():
    v = parse_judge_verdict(
        '{"status": "DONE", "feedback": 42}',
        valid_statuses=GOAL_STATUSES, fallback_status="CONTINUE",
    )
    assert v.parse_ok is True
    assert v.feedback == "42"


# ── TurnJudge 场景（不同白名单/fallback）────────────────────────────────

def test_turn_judge_statuses():
    v = parse_judge_verdict(
        '{"status": "AUTO_CONTINUE", "feedback": "retry with X"}',
        valid_statuses=TURN_STATUSES, fallback_status="NEED_USER",
    )
    assert v.parse_ok is True
    assert v.status == "AUTO_CONTINUE"


def test_turn_judge_fallback_is_need_user_not_done_or_continue():
    v = parse_judge_verdict(
        "not json", valid_statuses=TURN_STATUSES, fallback_status="NEED_USER",
    )
    assert v.parse_ok is False
    assert v.status == "NEED_USER"


# ── feedback.py 委托 + 向后兼容 ───────────────────────────────────────────

def test_extract_goal_status_parses_new_json_format():
    from mini_agent.role_agents.feedback import extract_goal_status
    assert extract_goal_status('{"status": "DONE", "feedback": "x"}') == "DONE"


def test_extract_goal_status_falls_back_to_legacy_plain_text():
    from mini_agent.role_agents.feedback import extract_goal_status
    assert extract_goal_status("blah\nGOAL_STATUS: DONE\n") == "DONE"
    assert extract_goal_status("GOAL_STATUS:NEED_COMPACT") == "NEED_COMPACT"


def test_extract_goal_status_returns_none_when_nothing_matches():
    from mini_agent.role_agents.feedback import extract_goal_status
    assert extract_goal_status("no status information at all") is None


def test_extract_turn_status_parses_new_json_format():
    from mini_agent.role_agents.feedback import extract_turn_status
    assert extract_turn_status('{"status": "AUTO_CONTINUE", "feedback": "x"}') == "AUTO_CONTINUE"


def test_extract_turn_status_falls_back_to_legacy_plain_text():
    from mini_agent.role_agents.feedback import extract_turn_status
    assert extract_turn_status("TURN_STATUS: NEED_USER") == "NEED_USER"


# ── GoalRunner / TurnJudge 集成：JSON feedback 展示层 ────────────────────

def test_goal_runner_uses_json_feedback_field_for_display_and_injection(monkeypatch, tmp_path):
    """[Phase 5] run_goal_judge 返回 JSON 时，注入历史/展示用的是干净的
    feedback 字段内容，而不是原始 JSON 字符串。"""
    from tests.test_goal_mode import FakeAgent, _FakeCfg, _confirmed_spec
    from mini_agent.goal_mode.runner import GoalRunner

    agent = FakeAgent(outputs=["attempt 1"])
    cfg = _FakeCfg(tmp_path)
    spec = _confirmed_spec()

    monkeypatch.setattr(
        "mini_agent.role_agents.goal_judge.run_goal_judge",
        lambda **kw: '{"status": "DONE", "feedback": "全部验收标准均已通过。"}',
    )

    runner = GoalRunner(agent=agent, cfg=cfg, goal_spec=spec)
    result = runner.run()

    assert result.status == "done"
    assert result.final_report == "全部验收标准均已通过。"
    # 注入到主 Agent 历史里的也应该是干净的 feedback 文本，而不是原始 JSON
    injected = [m for m in agent._hist.entries if m.get("_type") == "role_agent"]
    assert injected, "应该有一条 role_agent 类型的注入消息"
    assert "全部验收标准均已通过" in injected[-1]["content"]
    assert '"status"' not in injected[-1]["content"]
