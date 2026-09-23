"""tests/test_field_ledger.py — `world_simulator/engine/resource_guard.py`
里字段变化台账（field_ledger）相关函数的单测：`_flatten_numeric_
leaves()`/`_check_field_ledger()`/`_auto_fill_unaccounted_ledger_
entries()`。

补这份专门的单测文件是第十八轮直接的动机：审计代码库发现
`_check_field_ledger()`（第十五轮引入）此前完全没有独立单测覆盖，
只在集成层面（`advance()` 的端到端测试）间接跑到过——这次改动
（全字段扫描 + 确定性自动补全）恰好是对"没有账本的变更"这个用户
反馈问题的直接回应，理应把这几个函数的行为锁定下来，不再是"没有
测试保护的隐藏逻辑"。
"""

from __future__ import annotations

from world_simulator.engine.resource_guard import (
    _auto_fill_unaccounted_ledger_entries,
    _check_field_ledger,
    _flatten_numeric_leaves,
)


# ── _flatten_numeric_leaves ──────────────────────────────────────────

def test_flatten_numeric_leaves_collects_top_level_and_one_level_nested():
    vars_dict = {
        "age": 22,
        "resources": {"cash": 1000.5, "total_capital": 2000},
        "name": "小明",  # 非数值，跳过
        "flags": {"active": True, "score": 10},  # bool 跳过，数值保留
        "meta": {"tags": ["a", "b"]},  # 内层非数值，跳过
    }
    leaves = _flatten_numeric_leaves(vars_dict)
    assert leaves == {
        "age": 22,
        "resources.cash": 1000.5,
        "resources.total_capital": 2000,
        "flags.score": 10,
    }


def test_flatten_numeric_leaves_handles_empty_and_none():
    assert _flatten_numeric_leaves({}) == {}
    assert _flatten_numeric_leaves(None) == {}


# ── _check_field_ledger：算术类校验（第十五轮既有行为，改动后不应该变）──

def test_check_field_ledger_detects_start_mismatch():
    current_vars = {"resources": {"cash": 500}}
    next_vars = {"resources": {"cash": 1500}}
    field_ledger_raw = [
        {"field": "resources.cash", "kind": "increase", "amount": 500,
         "value_before": 1000, "value_after": 1500, "reason": "签单"},
    ]
    _, violations = _check_field_ledger(current_vars, next_vars, field_ledger_raw)
    issues = {v["issue"] for v in violations}
    assert "start_mismatch" in issues


def test_check_field_ledger_detects_arithmetic_mismatch():
    current_vars = {"resources": {"cash": 1000}}
    next_vars = {"resources": {"cash": 1600}}
    field_ledger_raw = [
        {"field": "resources.cash", "kind": "increase", "amount": 500,
         "value_before": 1000, "value_after": 1600, "reason": "算错了"},
    ]
    _, violations = _check_field_ledger(current_vars, next_vars, field_ledger_raw)
    issues = {v["issue"] for v in violations}
    assert "arithmetic_mismatch" in issues


def test_check_field_ledger_detects_end_mismatch_with_actual():
    current_vars = {"resources": {"cash": 1000}}
    next_vars = {"resources": {"cash": 1400}}  # 实际落盘是 1400，账上说 1500
    field_ledger_raw = [
        {"field": "resources.cash", "kind": "increase", "amount": 500,
         "value_before": 1000, "value_after": 1500, "reason": "签单"},
    ]
    _, violations = _check_field_ledger(current_vars, next_vars, field_ledger_raw)
    end_mismatches = [v for v in violations if v["issue"] == "end_mismatch_with_actual"]
    assert len(end_mismatches) == 1
    assert end_mismatches[0]["expected"] == 1500
    assert end_mismatches[0]["actual"] == 1400


def test_check_field_ledger_no_violation_when_ledger_is_correct():
    current_vars = {"resources": {"cash": 1000}}
    next_vars = {"resources": {"cash": 1500}}
    field_ledger_raw = [
        {"field": "resources.cash", "kind": "increase", "amount": 500,
         "value_before": 1000, "value_after": 1500, "reason": "签单"},
    ]
    _, violations = _check_field_ledger(current_vars, next_vars, field_ledger_raw)
    assert violations == []


# ── _check_field_ledger：全字段扫描（第十八轮核心改动）──────────────

def test_check_field_ledger_flags_never_before_mentioned_field_as_unaccounted():
    """核心场景：`resources.total_capital` 从来没有在任何一步的
    `field_ledger` 里出现过（旧机制下 `tracked_ledger_fields` 里不会
    有它，永远不会被检查到）；这一步它变了，但账本完全没提——全字段
    扫描应该能抓到，不依赖它"曾经被记过账"这个前提。"""
    current_vars = {"resources": {"cash": 352110, "total_capital": 2889130}}
    next_vars = {"resources": {"cash": 320500, "total_capital": 2856000}}
    field_ledger_raw = [
        {"field": "resources.cash", "kind": "decrease", "amount": 31610,
         "value_before": 352110, "value_after": 320500, "reason": "API 预算增加"},
        # total_capital 完全没有对应记录
    ]
    _, violations = _check_field_ledger(current_vars, next_vars, field_ledger_raw)
    unaccounted = [v for v in violations if v["issue"] == "unaccounted_resource_change"]
    assert len(unaccounted) == 1
    assert unaccounted[0]["field"] == "resources.total_capital"
    assert unaccounted[0]["expected"] == 2889130
    assert unaccounted[0]["actual"] == 2856000


def test_check_field_ledger_multiple_unaccounted_fields_like_user_report():
    """复现用户反馈里的场景：一步里同时有好几个字段完全没有记账
    记录（`total_capital`/`digital_twin_operation`/`harness_
    expertise`/`agent_architecture`/`api_budget`），全部应该被
    识别出来，一个不漏。"""
    current_vars = {
        "resources": {"total_capital": 2889130, "api_budget": 6100},
        "research": {"digital_twin_operation": 0.59},
        "knowledge": {"harness_expertise": 86, "agent_architecture": 83},
    }
    next_vars = {
        "resources": {"total_capital": 2856000, "api_budget": 7800},
        "research": {"digital_twin_operation": 0.63},
        "knowledge": {"harness_expertise": 88, "agent_architecture": 85},
    }
    _, violations = _check_field_ledger(current_vars, next_vars, field_ledger_raw=[])
    unaccounted_fields = {
        v["field"] for v in violations if v["issue"] == "unaccounted_resource_change"
    }
    assert unaccounted_fields == {
        "resources.total_capital", "resources.api_budget",
        "research.digital_twin_operation",
        "knowledge.harness_expertise", "knowledge.agent_architecture",
    }


def test_check_field_ledger_ignores_unchanged_fields():
    current_vars = {"resources": {"cash": 1000, "reputation": 50}}
    next_vars = {"resources": {"cash": 1000, "reputation": 50}}  # 都没变
    _, violations = _check_field_ledger(current_vars, next_vars, field_ledger_raw=[])
    assert violations == []


def test_check_field_ledger_ignores_field_only_present_in_one_side():
    """这一步才新增/消失的字段（只在 `current_vars` 或只在
    `next_vars` 里出现）没有"变化前"或"变化后"可比较，不应该被误判
    成未记账变化。"""
    current_vars = {"resources": {"cash": 1000}}
    next_vars = {"resources": {"cash": 1000, "bonus": 500}}  # 本步新增字段
    _, violations = _check_field_ledger(current_vars, next_vars, field_ledger_raw=[])
    assert violations == []


# ── _auto_fill_unaccounted_ledger_entries ────────────────────────────

def test_auto_fill_synthesizes_entry_for_increase():
    field_ledger = []
    violations = [
        {"field": "research.agi_score", "issue": "unaccounted_resource_change", "expected": 36, "actual": 42},
    ]
    updated_ledger, remaining = _auto_fill_unaccounted_ledger_entries(field_ledger, violations)
    assert remaining == []
    assert len(updated_ledger) == 1
    entry = updated_ledger[0]
    assert entry["field"] == "research.agi_score"
    assert entry["kind"] == "increase"
    assert entry["amount"] == 6
    assert entry["value_before"] == 36
    assert entry["value_after"] == 42
    assert entry["auto_filled"] is True
    assert "系统自动补录" in entry["reason"]


def test_auto_fill_synthesizes_entry_for_decrease():
    violations = [
        {"field": "health.physical_fitness", "issue": "unaccounted_resource_change", "expected": 70, "actual": 68},
    ]
    updated_ledger, remaining = _auto_fill_unaccounted_ledger_entries([], violations)
    entry = updated_ledger[0]
    assert entry["kind"] == "decrease"
    assert entry["amount"] == 2


def test_auto_fill_keeps_other_violation_types_untouched():
    """`start_mismatch`/`arithmetic_mismatch` 等不是单纯遗漏，系统
    猜不出"正确答案"，不应该被这个函数处理——原样保留在 remaining
    里，交给 LLM 修正调用或者展示给用户。"""
    violations = [
        {"field": "resources.cash", "issue": "arithmetic_mismatch", "expected": 1500, "actual": 1600},
        {"field": "research.agi_score", "issue": "unaccounted_resource_change", "expected": 36, "actual": 42},
    ]
    updated_ledger, remaining = _auto_fill_unaccounted_ledger_entries([], violations)
    assert len(remaining) == 1
    assert remaining[0]["issue"] == "arithmetic_mismatch"
    assert len(updated_ledger) == 1  # 只补了 unaccounted 的那一条


def test_auto_fill_preserves_existing_ledger_entries():
    existing_entry = {
        "field": "resources.cash", "kind": "decrease", "amount": 31610,
        "value_before": 352110, "value_after": 320500, "reason": "API 预算增加",
    }
    violations = [
        {"field": "resources.total_capital", "issue": "unaccounted_resource_change", "expected": 2889130, "actual": 2856000},
    ]
    updated_ledger, remaining = _auto_fill_unaccounted_ledger_entries([existing_entry], violations)
    assert remaining == []
    assert len(updated_ledger) == 2
    assert existing_entry in updated_ledger
    auto_entry = next(e for e in updated_ledger if e["field"] == "resources.total_capital")
    assert auto_entry["auto_filled"] is True


def test_check_then_auto_fill_end_to_end_leaves_no_unaccounted_violations():
    """把 `_check_field_ledger()` 和 `_auto_fill_unaccounted_ledger_
    entries()` 串起来跑一遍用户反馈里的完整场景，验证最终
    `ledger_violations` 里不会再剩下任何 `unaccounted_resource_
    change`，且每个字段都能在补全后的 `field_ledger` 里找到对应
    记录——这正是"所有变更都应该有对应的记账记录"这个用户要求的
    端到端验证。"""
    current_vars = {
        "resources": {"cash": 352110, "total_capital": 2889130, "api_budget": 6100},
        "research": {"agi_score": 36, "digital_twin_operation": 0.59},
        "knowledge": {"harness_expertise": 86, "agent_architecture": 83},
    }
    next_vars = {
        "resources": {"cash": 320500, "total_capital": 2856000, "api_budget": 7800},
        "research": {"agi_score": 42, "digital_twin_operation": 0.63},
        "knowledge": {"harness_expertise": 88, "agent_architecture": 85},
    }
    field_ledger_raw = [
        {"field": "resources.cash", "kind": "decrease", "amount": 31610,
         "value_before": 352110, "value_after": 320500, "reason": "API 预算增加及月均净消耗导致现金下降"},
        {"field": "research.agi_score", "kind": "increase", "amount": 6,
         "value_before": 36, "value_after": 42, "reason": "自进化模块加速迭代带来的 AGI 评分提升"},
    ]
    ledger, violations = _check_field_ledger(current_vars, next_vars, field_ledger_raw)
    ledger, violations = _auto_fill_unaccounted_ledger_entries(ledger, violations)

    assert violations == []  # 不再有任何未解决的 unaccounted_resource_change

    all_changed_fields = {
        "resources.cash", "resources.total_capital", "resources.api_budget",
        "research.agi_score", "research.digital_twin_operation",
        "knowledge.harness_expertise", "knowledge.agent_architecture",
    }
    ledgered_fields = {e["field"] for e in ledger}
    assert all_changed_fields == ledgered_fields  # 每个变化字段都有对应记录

    auto_filled_fields = {e["field"] for e in ledger if e.get("auto_filled")}
    assert auto_filled_fields == {
        "resources.total_capital", "resources.api_budget",
        "research.digital_twin_operation",
        "knowledge.harness_expertise", "knowledge.agent_architecture",
    }
