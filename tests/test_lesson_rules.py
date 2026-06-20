"""
tests/test_lesson_rules.py — Stage 1.2 验证

对应 self_evolution_implementation_plan.md Stage 1.2：
  规则触发引擎（LessonRuleEngine）—— 连续失败计数 + 权限拒绝后重试成功检测，
  两条规则均不依赖 LLM，纯规则判断。
"""

from __future__ import annotations

from mini_agent.perception.lesson_rules import LessonRuleEngine, is_tool_error


# ── is_tool_error ────────────────────────────────────────────────────────────

def test_is_tool_error_detects_common_prefixes():
    assert is_tool_error("[error: something failed]")
    assert is_tool_error("[tool error: x]")
    assert is_tool_error("Error: file not found")
    assert is_tool_error("Traceback (most recent call last):\n  ...")
    assert is_tool_error("fatal: not a git repository")


def test_is_tool_error_detects_exit_code():
    assert is_tool_error("output here\n[exit code: 1]")
    assert not is_tool_error("output here\n[exit code: 0]")


def test_is_tool_error_false_for_normal_output():
    assert not is_tool_error("file1.txt\nfile2.txt")
    assert not is_tool_error("")
    assert not is_tool_error(None)


# ── 规则一：连续失败 ──────────────────────────────────────────────────────────

def test_consecutive_failure_triggers_at_threshold():
    engine = LessonRuleEngine(session_id="s1", model="m", fail_threshold=3)
    e1 = engine.observe("bash", {"command": "x"}, True, "[error]", True)
    e2 = engine.observe("bash", {"command": "x"}, True, "[error]", True)
    e3 = engine.observe("bash", {"command": "x"}, True, "[error]", True)
    assert e1 is None
    assert e2 is None
    assert e3 is not None
    assert e3.entry_type == "lesson"
    assert e3.source == "self_reflection"
    assert e3.occurrence_count == 3
    assert "bash" in e3.trigger


def test_consecutive_failure_not_duplicated_within_same_streak():
    engine = LessonRuleEngine(session_id="s1", fail_threshold=2)
    engine.observe("bash", {}, True, "[error]", True)
    e2 = engine.observe("bash", {}, True, "[error]", True)
    e3 = engine.observe("bash", {}, True, "[error]", True)  # 第3次，仍在同一连续失败区间
    assert e2 is not None
    assert e3 is None  # 不重复生成


def test_consecutive_failure_resets_after_success():
    engine = LessonRuleEngine(session_id="s1", fail_threshold=2)
    engine.observe("bash", {}, True, "[error]", True)
    engine.observe("bash", {}, True, "[error]", True)  # 触发一次
    engine.observe("bash", {}, True, "ok", False)       # 成功，重置计数
    e1 = engine.observe("bash", {}, True, "[error]", True)
    e2 = engine.observe("bash", {}, True, "[error]", True)  # 应该能再次触发
    assert e1 is None
    assert e2 is not None


def test_different_tools_tracked_independently():
    engine = LessonRuleEngine(session_id="s1", fail_threshold=2)
    engine.observe("bash", {}, True, "[error]", True)
    e_bash = engine.observe("bash", {}, True, "[error]", True)
    e_write = engine.observe("write_file", {}, True, "[error]", True)
    assert e_bash is not None
    assert e_write is None  # write_file 只失败了1次，未达阈值


def test_custom_fail_threshold():
    engine = LessonRuleEngine(session_id="s1", fail_threshold=5)
    entries = [
        engine.observe("bash", {}, True, "[error]", True) for _ in range(4)
    ]
    assert all(e is None for e in entries)
    e5 = engine.observe("bash", {}, True, "[error]", True)
    assert e5 is not None
    assert e5.occurrence_count == 5


# ── 规则二：权限拒绝后重试成功 ────────────────────────────────────────────────

def test_denial_then_success_triggers_lesson():
    engine = LessonRuleEngine(session_id="s1")
    deny = engine.observe("write_file", {"path": "/etc/passwd"}, False, "[Tool call denied by user]", False)
    assert deny is None

    retry = engine.observe("write_file", {"path": "/tmp/safe.txt"}, True, "written ok", False)
    assert retry is not None
    assert retry.source == "self_reflection"
    assert retry.confidence == 0.6
    assert "write_file" in retry.trigger


def test_denial_then_failure_does_not_trigger():
    engine = LessonRuleEngine(session_id="s1")
    engine.observe("bash", {}, False, "[Tool call denied by user]", False)
    retry = engine.observe("bash", {}, True, "[error: still broken]", True)
    assert retry is None


def test_denial_pending_consumed_only_once():
    engine = LessonRuleEngine(session_id="s1")
    engine.observe("bash", {}, False, "[Tool call denied by user]", False)
    first = engine.observe("bash", {}, True, "ok", False)
    second = engine.observe("bash", {}, True, "ok again", False)
    assert first is not None
    assert second is None  # pending 已被消费，第二次成功调用不再触发


def test_denial_retry_window_expired():
    import time
    engine = LessonRuleEngine(session_id="s1")
    engine.observe("bash", {}, False, "[Tool call denied by user]", False)
    # 手动伪造拒绝时间为很久之前，模拟超出重试窗口
    pending = engine._pending_denials["bash"]
    pending.denied_at = time.time() - 700  # 超过 _DENIAL_RETRY_WINDOW_SECONDS(600s)
    retry = engine.observe("bash", {}, True, "ok", False)
    assert retry is None


def test_unrelated_tool_success_no_false_trigger():
    """没有任何 pending denial 时，普通成功调用不应触发规则二。"""
    engine = LessonRuleEngine(session_id="s1")
    result = engine.observe("read_file", {"path": "x"}, True, "content", False)
    assert result is None
