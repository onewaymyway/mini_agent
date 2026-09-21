"""tests/test_knowledge_base.py — 阶段二十（因果知识库）单元测试。

对应 `next_doc/world_simulator_toward_universal_simulator_plan.md`
4.12 节的验收标准：写入 → 相似合并 → 检索排序 → 格式化拼进 prompt
这条最小闭环。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import world_simulator.knowledge_base as kb


def test_record_causal_links_creates_new_items(tmp_path):
    added = kb.record_causal_links(
        tmp_path,
        sim_id="sim_a",
        template="life_sim",
        causal_links=[
            {
                "driver": "AI 成本持续下降",
                "affected_fields": ["adoption_rate"],
                "effect": "采用率明显提高",
            }
        ],
    )
    assert added == 1
    items = kb.load_all(tmp_path)
    assert len(items) == 1
    item = items[0]
    assert item.cause == "AI 成本持续下降"
    assert item.effect == "采用率明显提高"
    assert item.confidence == "hypothesis"
    assert item.source_sim_id == "sim_a"
    assert item.source_template == "life_sim"
    assert item.validated_count == 0
    assert "adoption_rate" in item.mechanism


def test_record_causal_links_ignores_incomplete_entries(tmp_path):
    """`driver`/`effect` 缺一不可——残缺的因果链条目不构成一条可复用的
    知识，不应该污染知识库。"""
    added = kb.record_causal_links(
        tmp_path,
        sim_id="sim_a",
        template="life_sim",
        causal_links=[
            {"driver": "", "effect": "采用率提高"},
            {"driver": "成本下降", "effect": ""},
            {"driver": "只有 driver 没有 affected_fields", "effect": "有效果"},
        ],
    )
    assert added == 1
    items = kb.load_all(tmp_path)
    assert len(items) == 1
    assert items[0].mechanism == ""  # 没有 affected_fields 时机制留空，不编造


def test_record_causal_links_merges_similar_entries_by_bumping_validated_count(tmp_path):
    """同一条（或高度相似的）因果关系在不同 sim_id 里反复出现时，应该
    递增 `validated_count` 而不是产生重复条目（4.12 节 4.）。"""
    kb.record_causal_links(
        tmp_path,
        sim_id="sim_a",
        template="startup",
        causal_links=[
            {"driver": "AI 成本持续下降", "affected_fields": ["adoption_rate"], "effect": "采用率明显提高"}
        ],
    )
    added_second = kb.record_causal_links(
        tmp_path,
        sim_id="sim_b",
        template="startup",
        causal_links=[
            {"driver": "AI 成本持续下降", "affected_fields": ["adoption_rate"], "effect": "采用率明显提高"}
        ],
    )
    items = kb.load_all(tmp_path)
    assert added_second == 0
    assert len(items) == 1
    assert items[0].validated_count == 1
    assert items[0].source_sim_id == "sim_a"  # 保留最初来源，不因合并而改写


def test_record_causal_links_keeps_dissimilar_entries_separate(tmp_path):
    kb.record_causal_links(
        tmp_path,
        sim_id="sim_a",
        template="startup",
        causal_links=[{"driver": "AI 成本持续下降", "effect": "采用率明显提高"}],
    )
    added_second = kb.record_causal_links(
        tmp_path,
        sim_id="sim_b",
        template="startup",
        causal_links=[{"driver": "谈判陷入僵局", "effect": "双方转向第三方调解"}],
    )
    assert added_second == 1
    assert len(kb.load_all(tmp_path)) == 2


def test_search_returns_relevant_items_sorted_by_score_and_validated_count(tmp_path):
    kb.record_causal_links(
        tmp_path, sim_id="sim_a", template="startup",
        causal_links=[{"driver": "AI 成本持续下降", "effect": "采用率明显提高"}],
    )
    kb.record_causal_links(
        tmp_path, sim_id="sim_b", template="startup",
        causal_links=[{"driver": "AI 成本持续下降", "effect": "采用率明显提高"}],
    )  # 命中相似合并，validated_count += 1
    kb.record_causal_links(
        tmp_path, sim_id="sim_c", template="negotiation",
        causal_links=[{"driver": "谈判陷入僵局", "effect": "双方转向第三方调解"}],
    )

    results = kb.search(tmp_path, "AI 成本下降会怎样影响采用率", template="startup")
    assert results
    assert results[0].cause == "AI 成本持续下降"
    assert results[0].validated_count == 1


def test_search_returns_empty_when_nothing_relevant(tmp_path):
    kb.record_causal_links(
        tmp_path, sim_id="sim_a", template="startup",
        causal_links=[{"driver": "AI 成本持续下降", "effect": "采用率明显提高"}],
    )
    assert kb.search(tmp_path, "完全不相关的火星殖民主题", template="startup") == []


def test_search_on_empty_knowledge_base_returns_empty(tmp_path):
    assert kb.search(tmp_path, "任意查询") == []


def test_format_for_prompt_handles_empty_and_nonempty():
    assert kb.format_for_prompt([]) == "（暂无相关的已知因果知识）"
    item = kb.KnowledgeItem(
        id="x", cause="A 提高", effect="B 提高", mechanism="涉及字段：b",
        confidence="hypothesis", validated_count=2, contradicted_count=1,
    )
    text = kb.format_for_prompt([item])
    assert "A 提高" in text and "B 提高" in text
    assert "已被印证 2 次" in text
    assert "曾被证伪 1 次" in text


def test_suggest_for_prompt_combines_search_and_format(tmp_path):
    kb.record_causal_links(
        tmp_path, sim_id="sim_a", template="startup",
        causal_links=[{"driver": "现金储备见底", "effect": "被迫收缩开支"}],
    )
    text = kb.suggest_for_prompt(tmp_path, "现金储备见底该怎么办", template="startup")
    assert "现金储备见底" in text


def test_record_contradiction_updates_existing_item(tmp_path):
    kb.record_causal_links(
        tmp_path, sim_id="sim_a", template="startup",
        causal_links=[{"driver": "现金储备见底", "effect": "被迫收缩开支"}],
    )
    item_id = kb.load_all(tmp_path)[0].id
    assert kb.record_contradiction(tmp_path, item_id) is True
    assert kb.load_all(tmp_path)[0].contradicted_count == 1
    assert kb.record_contradiction(tmp_path, "不存在的 id") is False


def test_knowledge_item_round_trips_through_dict():
    item = kb.KnowledgeItem(
        id="abc", cause="c", effect="e", mechanism="m", confidence="confirmed",
        source_sim_id="s1", source_template="life_sim", created_at="2026-01-01",
        validated_count=3, contradicted_count=1,
    )
    restored = kb.KnowledgeItem.from_dict(item.to_dict())
    assert restored == item


def test_knowledge_item_from_dict_rejects_invalid_confidence():
    item = kb.KnowledgeItem.from_dict({"cause": "c", "effect": "e", "confidence": "not-a-real-level"})
    assert item.confidence == "hypothesis"


# ── 第八轮批次一：evidence/valid_range/version ──────────────────────


def test_record_causal_links_records_evidence_with_step(tmp_path):
    kb.record_causal_links(
        tmp_path,
        sim_id="sim_a",
        template="life_sim",
        causal_links=[{"driver": "AI 成本下降", "effect": "采用率提高"}],
        step=3,
    )
    item = kb.load_all(tmp_path)[0]
    assert item.evidence == ["sim_a#step3"]
    assert item.version == 1
    assert item.valid_range == ""


def test_record_causal_links_without_step_does_not_record_evidence(tmp_path):
    kb.record_causal_links(
        tmp_path, sim_id="sim_a", template="life_sim",
        causal_links=[{"driver": "AI 成本下降", "effect": "采用率提高"}],
    )
    assert kb.load_all(tmp_path)[0].evidence == []


def test_record_causal_links_appends_evidence_on_match_without_duplicates(tmp_path):
    link = {"driver": "AI 成本下降", "effect": "采用率提高"}
    kb.record_causal_links(tmp_path, sim_id="sim_a", template="life_sim", causal_links=[link], step=1)
    kb.record_causal_links(tmp_path, sim_id="sim_b", template="life_sim", causal_links=[link], step=2)
    # 同一来源（同 sim_id + step）重复调用不应该重复记录。
    kb.record_causal_links(tmp_path, sim_id="sim_a", template="life_sim", causal_links=[link], step=1)

    item = kb.load_all(tmp_path)[0]
    assert item.validated_count == 2
    assert sorted(item.evidence) == ["sim_a#step1", "sim_b#step2"]


def test_record_causal_links_valid_range_change_bumps_version(tmp_path):
    kb.record_causal_links(
        tmp_path, sim_id="sim_a", template="startup",
        causal_links=[{"driver": "现金见底", "effect": "被迫收缩", "valid_range": "初创期成立"}],
    )
    item = kb.load_all(tmp_path)[0]
    assert item.valid_range == "初创期成立"
    assert item.version == 1

    kb.record_causal_links(
        tmp_path, sim_id="sim_b", template="startup",
        causal_links=[{"driver": "现金见底", "effect": "被迫收缩", "valid_range": "规模化后不成立"}],
    )
    item = kb.load_all(tmp_path)[0]
    assert item.valid_range == "规模化后不成立"
    assert item.version == 2

    # 相同的 valid_range 再次出现不应该重复递增版本号。
    kb.record_causal_links(
        tmp_path, sim_id="sim_c", template="startup",
        causal_links=[{"driver": "现金见底", "effect": "被迫收缩", "valid_range": "规模化后不成立"}],
    )
    assert kb.load_all(tmp_path)[0].version == 2


def test_knowledge_item_from_dict_defaults_for_legacy_data_without_new_fields():
    """旧数据（没有 evidence/valid_range/version 字段的历史 jsonl 行）
    应该被安全兼容读出，不报错，取合理默认值。"""
    legacy = {
        "id": "abc", "cause": "c", "effect": "e", "mechanism": "m",
        "confidence": "confirmed", "source_sim_id": "s1", "source_template": "life_sim",
        "created_at": "2026-01-01", "validated_count": 3, "contradicted_count": 1,
    }
    item = kb.KnowledgeItem.from_dict(legacy)
    assert item.evidence == []
    assert item.valid_range == ""
    assert item.version == 1


def test_knowledge_item_round_trips_with_new_fields():
    item = kb.KnowledgeItem(
        id="abc", cause="c", effect="e", mechanism="m", confidence="confirmed",
        source_sim_id="s1", source_template="life_sim", created_at="2026-01-01",
        validated_count=3, contradicted_count=1,
        evidence=["s1#step2"], valid_range="仅在初创期成立", version=2,
    )
    restored = kb.KnowledgeItem.from_dict(item.to_dict())
    assert restored == item
