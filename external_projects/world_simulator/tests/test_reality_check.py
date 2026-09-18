"""tests/test_reality_check.py — 阶段二十四（Reality Loop 完整版）单元
测试。

对应 `next_doc/world_simulator_toward_universal_simulator_plan.md`
4.16 节的验收标准：记录一条 `verdict: diverged` 的现实结果，能在知识
库里看到对应因果知识条目的 `contradicted_count` 增加。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

import world_simulator.knowledge_base as kb
import world_simulator.reality_check as rc


def test_record_reality_check_requires_outcome_and_valid_verdict(tmp_path):
    with pytest.raises(rc.RealityCheckError):
        rc.record_reality_check(
            tmp_path, "sim_a", branch="main", step=1,
            predicted_summary="s", actual_outcome="", verdict="matched",
        )
    with pytest.raises(rc.RealityCheckError):
        rc.record_reality_check(
            tmp_path, "sim_a", branch="main", step=1,
            predicted_summary="s", actual_outcome="确实发生了", verdict="不是合法值",
        )


def test_record_reality_check_persists_and_loads_back(tmp_path):
    item = rc.record_reality_check(
        tmp_path, "sim_a", branch="main", step=2,
        predicted_summary="预计现金会见底",
        actual_outcome="现金确实见底了，和预测一致",
        verdict="matched",
    )
    assert item.sim_id == "sim_a"
    assert item.branch == "main"
    assert item.step == 2
    assert item.verdict == "matched"
    assert item.recorded_at

    loaded = rc.load_all(tmp_path, "sim_a")
    assert len(loaded) == 1
    assert loaded[0].id == item.id
    assert loaded[0].actual_outcome == "现金确实见底了，和预测一致"


def test_find_for_step_filters_by_branch_and_step(tmp_path):
    rc.record_reality_check(
        tmp_path, "sim_a", branch="main", step=1,
        predicted_summary="s1", actual_outcome="o1", verdict="matched",
    )
    rc.record_reality_check(
        tmp_path, "sim_a", branch="main", step=2,
        predicted_summary="s2", actual_outcome="o2", verdict="diverged",
    )
    rc.record_reality_check(
        tmp_path, "sim_a", branch="branch_b", step=1,
        predicted_summary="s1b", actual_outcome="o1b", verdict="matched",
    )
    step1_main = rc.find_for_step(tmp_path, "sim_a", branch="main", step=1)
    assert len(step1_main) == 1
    assert step1_main[0].actual_outcome == "o1"


def test_update_confidence_from_reality_check_increments_contradicted_count(tmp_path):
    """4.16 节验收标准：diverged 反馈应该让匹配到的知识条目
    `contradicted_count` 增加。"""
    kb.record_causal_links(
        tmp_path, sim_id="sim_a", template="life_sim",
        causal_links=[
            {"driver": "AI 成本持续下降", "affected_fields": ["adoption_rate"],
             "effect": "采用率明显提高"},
        ],
    )
    items_before = kb.load_all(tmp_path)
    assert items_before[0].contradicted_count == 0

    contradicted_ids = kb.update_confidence_from_reality_check(
        tmp_path,
        [
            {"driver": "AI 成本持续下降", "affected_fields": ["adoption_rate"],
             "effect": "采用率明显提高"},
        ],
    )
    assert contradicted_ids == [items_before[0].id]

    items_after = kb.load_all(tmp_path)
    assert items_after[0].contradicted_count == 1


def test_update_confidence_from_reality_check_no_match_returns_empty(tmp_path):
    kb.record_causal_links(
        tmp_path, sim_id="sim_a", template="life_sim",
        causal_links=[
            {"driver": "市场需求超预期", "affected_fields": ["revenue"],
             "effect": "营收大幅增长"},
        ],
    )
    contradicted_ids = kb.update_confidence_from_reality_check(
        tmp_path,
        [{"driver": "完全不相关的一个原因", "effect": "完全不相关的一个结果"}],
    )
    assert contradicted_ids == []
    assert kb.load_all(tmp_path)[0].contradicted_count == 0


def test_update_confidence_from_reality_check_handles_empty_knowledge_base(tmp_path):
    """知识库还没有任何条目时（比如这次模拟压根没触发过
    `record_causal_links`），应该安全返回空列表，不抛异常。"""
    assert kb.update_confidence_from_reality_check(
        tmp_path, [{"driver": "d", "effect": "e"}]
    ) == []


def test_record_and_apply_end_to_end_diverged_updates_knowledge_base(tmp_path):
    kb.record_causal_links(
        tmp_path, sim_id="sim_a", template="life_sim",
        causal_links=[
            {"driver": "现金储备见底", "affected_fields": ["cash", "stage"],
             "effect": "被迫从「自由职业」转为「求稳定工作」"},
        ],
    )

    result = rc.record_and_apply(
        tmp_path, "sim_a",
        branch="main", step=3,
        predicted_summary="预计会被迫转为求稳定工作",
        actual_outcome="实际上靠副业撑过去了，没有转职",
        verdict="diverged",
        causal_links=[
            {"driver": "现金储备见底", "affected_fields": ["cash", "stage"],
             "effect": "被迫从「自由职业」转为「求稳定工作」"},
        ],
    )
    assert result["reality_check"].verdict == "diverged"
    assert len(result["contradicted_knowledge_ids"]) == 1
    assert kb.load_all(tmp_path)[0].contradicted_count == 1


def test_record_and_apply_matched_does_not_touch_knowledge_base(tmp_path):
    """`matched`/`partially_matched` 不应该触发任何知识库更新（见
    `reality_check.record_and_apply` docstring：预测对了不重复加分，
    避免和 `record_causal_links()` 的跨模拟重复出现机制双重计数）。"""
    kb.record_causal_links(
        tmp_path, sim_id="sim_a", template="life_sim",
        causal_links=[
            {"driver": "现金储备见底", "affected_fields": ["cash"],
             "effect": "被迫转职"},
        ],
    )
    result = rc.record_and_apply(
        tmp_path, "sim_a",
        branch="main", step=3,
        predicted_summary="预计会被迫转为求稳定工作",
        actual_outcome="确实转职了",
        verdict="matched",
        causal_links=[
            {"driver": "现金储备见底", "affected_fields": ["cash"], "effect": "被迫转职"},
        ],
    )
    assert result["contradicted_knowledge_ids"] == []
    assert kb.load_all(tmp_path)[0].contradicted_count == 0
    assert kb.load_all(tmp_path)[0].validated_count == 0
