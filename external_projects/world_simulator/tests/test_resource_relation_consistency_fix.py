"""tests/test_resource_relation_consistency_fix.py — 第九轮批次一：
资源转移一致性机制根本性改进的测试。

对应 `next_doc/world_simulator_resource_relation_consistency_fix_plan.md`。
覆盖三个改动点：
1. 新增 `conversion` 类型（跨量纲有代价转化）——不参与任何数值核对。
2. `transfer` 类型的"显式流水优先"核对——`resource_transfers` 报告
   了搬运量时，改用报告值核对，不再依赖整体快照差分。
3. `transfer` 类型的"共享字段自动降级"——`from`/`to` 被多条关系
   引用、且没有显式流水时，跳过差分核对，不误报。

只单测 `engine/resource_guard.py` 里的归一化 + 检查函数，手法与
`test_resource_production_relation.py` 一致，不重复端到端集成测试
（那部分已有 `test_spec_and_engine.py`/`test_resource_production_
relation.py` 覆盖 `transfer`/`production` 的基本流程）。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from world_simulator.engine.resource_guard import (
    _check_resource_relations,
    _normalize_resource_relations,
)


# ---------------------------------------------------------------------------
# 1. conversion 类型：归一化 + 不参与数值核对
# ---------------------------------------------------------------------------


def test_normalize_conversion_relation():
    raw = [
        {
            "type": "conversion",
            "from": "resources.cash",
            "to": "health.physical_fitness",
            "note": "花钱健身",
        }
    ]
    assert _normalize_resource_relations(raw) == [
        {
            "type": "conversion",
            "from": "resources.cash",
            "to": "health.physical_fitness",
            "note": "花钱健身",
        }
    ]


def test_normalize_conversion_relation_missing_from_or_to_is_skipped():
    raw = [{"type": "conversion", "from": "", "to": "health.physical_fitness"}]
    assert _normalize_resource_relations(raw) == []


def test_normalize_conversion_relation_note_defaults_to_empty_string():
    raw = [{"type": "conversion", "from": "a", "to": "b"}]
    result = _normalize_resource_relations(raw)
    assert result == [{"type": "conversion", "from": "a", "to": "b", "note": ""}]


def test_check_conversion_relation_never_produces_violation_even_when_wildly_unequal():
    """回归你贴的原始报警场景：cash 大幅减少、physical_fitness 只涨了
    一点点——量纲完全不同，声明成 conversion 之后不应该有任何提示。"""
    raw = [
        {
            "type": "conversion",
            "from": "resources.cash",
            "to": "health.physical_fitness",
        }
    ]
    current = {"resources": {"cash": 20000}, "health": {"physical_fitness": 50}}
    nxt = {"resources": {"cash": 8000}, "health": {"physical_fitness": 51}}
    assert _check_resource_relations(current, nxt, raw) == []


# ---------------------------------------------------------------------------
# 2. transfer：显式流水优先核对
# ---------------------------------------------------------------------------


def test_transfer_with_matching_ledger_entry_produces_no_violation_despite_noisy_diff():
    """同一步 cash 除了这条转移之外还有别的花销（净变化 -12000，
    甚至方向都被盖过），但显式流水报告了这条关系发生过——报了账
    之后直接信任，不再拿净差分去反过来验证它，这正是要修复的原始
    报警场景（invested→cash：净差分被未声明的其它花销污染）。"""
    raw = [{"type": "transfer", "from": "resources.invested", "to": "resources.cash"}]
    current = {"resources": {"invested": 5000, "cash": 3000}}
    nxt = {"resources": {"invested": 4000, "cash": -9000}}  # cash 净变化 -12000
    transfers = [{"relation": "resources.invested->resources.cash", "amount": 1000}]
    assert _check_resource_relations(current, nxt, raw, transfers) == []


def test_transfer_with_ledger_entry_skips_diff_check_regardless_of_magnitude():
    """只要报告了这条关系的流水，无论 `next_vars` 里两端实际变化量
    看起来多离谱，都不再做差分核对——报账之后就是"记录在案"，不是
    "拿去二次验证"。"""
    raw = [{"type": "transfer", "from": "resources.invested", "to": "resources.cash"}]
    current = {"resources": {"invested": 5000, "cash": 3000}}
    nxt = {"resources": {"invested": 5500, "cash": 3200}}  # 方向看起来都不对
    transfers = [{"relation": "resources.invested->resources.cash", "amount": 1000}]
    assert _check_resource_relations(current, nxt, raw, transfers) == []


def test_transfer_ledger_entry_for_unrelated_relation_is_ignored():
    """流水表里报告的是另一条关系，这条关系没有对应记录，应该退回
    差分法核对（而不是被别的关系的流水污染）。"""
    raw = [{"type": "transfer", "from": "cash", "to": "inventory.value"}]
    current = {"cash": 1000, "inventory": {"value": 0}}
    nxt = {"cash": 900, "inventory": {"value": 20}}
    transfers = [{"relation": "resources.other->resources.thing", "amount": 999}]
    violations = _check_resource_relations(current, nxt, raw, transfers)
    assert violations == [
        {
            "from": "cash",
            "to": "inventory.value",
            "delta_from": -100,
            "delta_to": 20,
            "checked_by": "diff",
        }
    ]


# ---------------------------------------------------------------------------
# 3. transfer：共享字段自动降级
# ---------------------------------------------------------------------------


def test_transfer_sharing_a_field_with_another_relation_skips_diff_check_without_ledger():
    """cash 同时是两条关系的 to 端（比如"投资回收"和"打工收入"都进
    cash），没有显式流水时差分法已知无法正确归因，应该自动跳过、不
    产生误报。"""
    raw = [
        {"type": "transfer", "from": "resources.invested", "to": "resources.cash"},
        {"type": "transfer", "from": "resources.labor", "to": "resources.cash"},
    ]
    current = {"resources": {"invested": 5000, "labor": 100, "cash": 3000}}
    # cash 净变化 -12000，明显不等于任何一条单独关系的搬运量。
    nxt = {"resources": {"invested": 4000, "labor": 90, "cash": -9000}}
    assert _check_resource_relations(current, nxt, raw) == []


def test_transfer_sharing_a_field_still_trusts_ledger_when_reported():
    """同样是共享字段的场景，但这条关系显式报了账——报了账就直接
    信任、不再关心 `cash` 这一步是否还被别的关系/未建模变动共同
    影响，这正是"有账就按账核对、不再依赖净差分"要解决的场景。"""
    raw = [
        {"type": "transfer", "from": "resources.invested", "to": "resources.cash"},
        {"type": "transfer", "from": "resources.labor", "to": "resources.cash"},
    ]
    current = {"resources": {"invested": 5000, "labor": 100, "cash": 3000}}
    nxt = {"resources": {"invested": 4000, "labor": 90, "cash": -9000}}
    transfers = [{"relation": "resources.invested->resources.cash", "amount": 1000}]
    violations = _check_resource_relations(current, nxt, raw, transfers)
    # invested->cash 报了账且方向一致，不产生不一致项。
    assert not any(v.get("from") == "resources.invested" for v in violations)
    # labor->cash 没报账，且 cash 被多条关系共享，按降级规则跳过。
    assert not any(v.get("from") == "resources.labor" for v in violations)
    assert violations == []


def test_transfer_not_shared_with_any_other_relation_still_uses_diff_check():
    """回归：只有一条 transfer 关系（不共享字段）时，行为和改动前
    完全一致，仍然走差分核对。"""
    raw = [{"type": "transfer", "from": "cash", "to": "inventory.value"}]
    current = {"cash": 1000, "inventory": {"value": 0}}
    nxt = {"cash": 900, "inventory": {"value": 20}}
    assert _check_resource_relations(current, nxt, raw) == [
        {
            "from": "cash",
            "to": "inventory.value",
            "delta_from": -100,
            "delta_to": 20,
            "checked_by": "diff",
        }
    ]


def test_conversion_relation_does_not_count_toward_transfer_field_sharing():
    """`conversion` 关系虽然也引用了 `cash` 字段，但它和 `transfer`
    关系语义不同——这里确认它确实会计入共享判断（因为 conversion 也
    说明这个字段这一步可能有多个来源在变），使唯一一条 transfer 因
    共享而降级。"""
    raw = [
        {"type": "transfer", "from": "resources.invested", "to": "resources.cash"},
        {"type": "conversion", "from": "resources.cash", "to": "health.physical_fitness"},
    ]
    current = {"resources": {"invested": 5000, "cash": 3000}, "health": {"physical_fitness": 50}}
    nxt = {"resources": {"invested": 4000, "cash": -9000}, "health": {"physical_fitness": 51}}
    assert _check_resource_relations(current, nxt, raw) == []
