"""
tests/test_triggers.py

覆盖 compact_mechanism_improvement_plan.md P1-A / P1-B 两项改造：

  P1-A — SafePointGate：安全点判定，把落在"不安全点"（最近工具调用链条
         包含有副作用操作）的触发命中挂起，等下一次到达安全点再放行。
  P1-B — 触发信号强度叠加：硬触发都未命中时，用各触发器的 intensity_hint()
         求和，超过阈值也视为命中一次 composite_intensity 软触发。

两项默认关闭，本文件同时验证"开关关闭时行为不变"和"开关打开时行为符合预期"。
"""

from __future__ import annotations

import sys

sys.path.insert(0, "src")

from mini_agent.config.models import CompressConfig
from mini_agent.history.triggers import (
    CompositeTrigger,
    RedundancyTrigger,
    SafePointGate,
    ToolCallCountTrigger,
    TriggerContext,
    TurnCountTrigger,
)


class _FakeAppConfig:
    """最小可用的 AppConfig 替身，只暴露 triggers.py 实际读取的 `.compress`。"""

    def __init__(self, compress: CompressConfig) -> None:
        self.compress = compress


def _ctx(history, turns=0, tool_calls=0, last_compact_turns=0,
          last_compact_tool_calls=0, turns_since_last_compact=10, budget_pct=0.0):
    return TriggerContext(
        history=history,
        budget_pct=budget_pct,
        turns=turns,
        tool_calls=tool_calls,
        last_compact_turns=last_compact_turns,
        last_compact_tool_calls=last_compact_tool_calls,
        turns_since_last_compact=turns_since_last_compact,
        llm_client=None,
    )


def _user_msg(text="继续"):
    return {"role": "user", "content": text, "_type": "user_input"}


def _assistant_tool_use(name):
    return {
        "role": "assistant",
        "content": [{"type": "tool_use", "id": "t1", "name": name, "input": {}}],
        "_type": "assistant_reply",
    }


def _tool_result_msg():
    return {"role": "user", "content": "<tool_result>ok</tool_result>", "_type": "tool_result"}


# ════════════════════════════════════════════════════════════════════════════
# P1-A: SafePointGate
# ════════════════════════════════════════════════════════════════════════════

def test_safe_point_empty_history_is_safe():
    gate = SafePointGate()
    assert gate.is_safe_point(_ctx(history=[]))


def test_safe_point_last_message_is_turn_boundary():
    gate = SafePointGate()
    history = [_user_msg()]
    assert gate.is_safe_point(_ctx(history=history))


def test_safe_point_unsafe_mid_risky_tool_chain():
    gate = SafePointGate()
    history = [
        _user_msg("帮我改一下代码"),
        _assistant_tool_use("bash"),
        _tool_result_msg(),
        _assistant_tool_use("bash"),
        _tool_result_msg(),
        _assistant_tool_use("bash"),
    ]
    assert not gate.is_safe_point(_ctx(history=history))


def test_safe_point_safe_when_only_readonly_tools():
    gate = SafePointGate()
    history = [
        _user_msg("看看这个文件写了什么"),
        _assistant_tool_use("read_file"),
        _tool_result_msg(),
        _assistant_tool_use("grep"),
        _tool_result_msg(),
    ]
    assert gate.is_safe_point(_ctx(history=history))


def test_composite_trigger_pending_until_safe_point():
    """安全点门控开启时，命中的软触发在不安全点被挂起，
    到达安全点后下一次 check() 应放行挂起结果，不需要重新满足触发条件。"""
    trigger = TurnCountTrigger()
    composite = CompositeTrigger(triggers=[trigger])
    compress = CompressConfig(
        turn_count_trigger_enabled=True,
        max_turns_before_compact=5,
        safe_point_gating_enabled=True,
        compact_cooldown_turns=0,
    )
    cfg = _FakeAppConfig(compress)

    unsafe_history = [
        _user_msg("帮我改一下代码"),
        _assistant_tool_use("bash"),
        _tool_result_msg(),
    ]
    result = composite.check(_ctx(history=unsafe_history, turns=10, turns_since_last_compact=10), cfg)
    assert not result.triggered  # 挂起，不立即执行
    assert composite._pending is not None
    assert composite._pending.reason == "turn_count"

    # 到达安全点（turn 边界）后，下一次 check() 即使触发条件本身没有变化，
    # 也应该直接放行之前挂起的结果。
    safe_history = unsafe_history + [_user_msg("继续")]
    result2 = composite.check(_ctx(history=safe_history, turns=10, turns_since_last_compact=10), cfg)
    assert result2.triggered
    assert result2.reason == "turn_count"
    assert composite._pending is None


def test_composite_trigger_safe_point_gating_disabled_behaves_as_before():
    """开关关闭时，即使处于不安全点，命中依然立即返回（行为与改造前一致）。"""
    trigger = TurnCountTrigger()
    composite = CompositeTrigger(triggers=[trigger])
    compress = CompressConfig(
        turn_count_trigger_enabled=True,
        max_turns_before_compact=5,
        safe_point_gating_enabled=False,
        compact_cooldown_turns=0,
    )
    cfg = _FakeAppConfig(compress)

    unsafe_history = [
        _user_msg("帮我改一下代码"),
        _assistant_tool_use("bash"),
        _tool_result_msg(),
    ]
    result = composite.check(_ctx(history=unsafe_history, turns=10, turns_since_last_compact=10), cfg)
    assert result.triggered
    assert result.reason == "turn_count"


def test_token_threshold_bypasses_safe_point_gating():
    """token 硬阈值（bypass_cooldown=True）不受安全点限制，无条件立即执行。"""
    from mini_agent.history.triggers import TokenThresholdTrigger

    trigger = TokenThresholdTrigger()
    composite = CompositeTrigger(triggers=[trigger])
    compress = CompressConfig(
        enabled=True,
        threshold=0.5,
        safe_point_gating_enabled=True,
        compact_cooldown_turns=0,
    )
    cfg = _FakeAppConfig(compress)

    unsafe_history = [
        _user_msg("帮我改一下代码"),
        _assistant_tool_use("bash"),
        _tool_result_msg(),
    ]
    result = composite.check(_ctx(history=unsafe_history, budget_pct=0.9, turns_since_last_compact=10), cfg)
    assert result.triggered
    assert result.reason == "token_threshold"


# ════════════════════════════════════════════════════════════════════════════
# P1-B: 触发信号强度叠加
# ════════════════════════════════════════════════════════════════════════════

def test_intensity_hint_below_threshold_alone_does_not_trigger():
    turn_trigger = TurnCountTrigger()
    tool_trigger = ToolCallCountTrigger()
    composite = CompositeTrigger(triggers=[turn_trigger, tool_trigger])
    compress = CompressConfig(
        turn_count_trigger_enabled=True,
        max_turns_before_compact=10,
        tool_call_count_trigger_enabled=True,
        max_tool_calls_before_compact=10,
        composite_intensity_enabled=True,
        composite_intensity_threshold=1.2,
        compact_cooldown_turns=0,
    )
    cfg = _FakeAppConfig(compress)

    # turn_count 强度 0.6（6/10），未单独触发（阈值是 10）
    ctx = _ctx(history=[], turns=6, last_compact_turns=0, tool_calls=0,
               last_compact_tool_calls=0, turns_since_last_compact=10)
    result = composite.check(ctx, cfg)
    assert not result.triggered


def test_intensity_hint_sum_reaches_threshold_triggers_composite_intensity():
    turn_trigger = TurnCountTrigger()
    tool_trigger = ToolCallCountTrigger()
    composite = CompositeTrigger(triggers=[turn_trigger, tool_trigger])
    compress = CompressConfig(
        turn_count_trigger_enabled=True,
        max_turns_before_compact=10,
        tool_call_count_trigger_enabled=True,
        max_tool_calls_before_compact=10,
        composite_intensity_enabled=True,
        composite_intensity_threshold=1.2,
        compact_cooldown_turns=0,
    )
    cfg = _FakeAppConfig(compress)

    # turn_count 强度 0.6（6/10）+ tool_call_count 强度 0.7（7/10）= 1.3 >= 1.2
    ctx = _ctx(history=[], turns=6, last_compact_turns=0, tool_calls=7,
               last_compact_tool_calls=0, turns_since_last_compact=10)
    result = composite.check(ctx, cfg)
    assert result.triggered
    assert result.reason == "composite_intensity"


def test_intensity_disabled_by_default_no_composite_trigger():
    turn_trigger = TurnCountTrigger()
    tool_trigger = ToolCallCountTrigger()
    composite = CompositeTrigger(triggers=[turn_trigger, tool_trigger])
    compress = CompressConfig(
        turn_count_trigger_enabled=True,
        max_turns_before_compact=10,
        tool_call_count_trigger_enabled=True,
        max_tool_calls_before_compact=10,
        composite_intensity_enabled=False,  # 默认关闭
        compact_cooldown_turns=0,
    )
    cfg = _FakeAppConfig(compress)

    ctx = _ctx(history=[], turns=6, last_compact_turns=0, tool_calls=7,
               last_compact_tool_calls=0, turns_since_last_compact=10)
    result = composite.check(ctx, cfg)
    assert not result.triggered


def test_redundancy_intensity_hint_respects_own_enabled_flag():
    trigger = RedundancyTrigger()
    compress = CompressConfig(redundancy_detection_enabled=False)
    cfg = _FakeAppConfig(compress)
    history = [_tool_result_msg()] * 6
    ctx = _ctx(history=history)
    # 触发器本身未启用时，intensity_hint 应返回 0，不污染叠加总和
    assert trigger.intensity_hint(ctx, cfg) == 0.0
