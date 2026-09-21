"""tests/test_reflexivity.py — 阶段三十六第四批（2.4 节反身性最小
诠释）单元测试。

覆盖三条链路：
1. `reflexivity.detect_suggested_direction()` 关键词判断建议方向。
2. `knowledge_base.annotate_reflexivity_observation()` 只标注检索到的
   相关条目，不新建条目、不重复追加同一句话。
3. `reflexivity.evaluate_and_annotate()` 端到端：样本不足不下结论、
   选择模式与建议方向一致时才追加标注，且同一份复盘不会被重复处理。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mini_agent.utils.atomic_write import atomic_write_jsonl

from world_simulator import knowledge_base, reflexivity, retrospective as retro
from world_simulator.engine.materialize import materialize_simulation
from world_simulator.state_model import ChoiceOption, SimState
from world_simulator.store import SimStore


def _report(lessons_text: str) -> retro.RetrospectiveReport:
    return retro.RetrospectiveReport(
        lessons=[{"point": lessons_text}] if lessons_text else [],
        caveats=["这是基于这次模拟内部记录的总结"],
    )


def test_detect_suggested_direction_reduce_risk():
    report = _report("过度冒险、孤注一掷是这次失败的主要原因")
    assert reflexivity.detect_suggested_direction(report) == "reduce_risk"


def test_detect_suggested_direction_increase_risk():
    report = _report("过于保守、屡次错失机会是这次表现不佳的原因")
    assert reflexivity.detect_suggested_direction(report) == "increase_risk"


def test_detect_suggested_direction_none_when_ambiguous_or_empty():
    assert reflexivity.detect_suggested_direction(_report("")) is None
    # 两类关键词都出现时不勉强给结论。
    mixed = _report("既有过度冒险的地方，也有太保守错失机会的地方")
    assert reflexivity.detect_suggested_direction(mixed) is None


def _seed_knowledge(tmp_path: Path) -> str:
    added = knowledge_base.record_causal_links(
        tmp_path,
        sim_id="sim-x",
        template="life_sim",
        causal_links=[
            {"driver": "冒险投资", "effect": "现金大幅波动", "affected_fields": ["cash"]},
        ],
    )
    assert added == 1
    items = knowledge_base.load_all(tmp_path)
    return items[0].id


def test_annotate_reflexivity_observation_appends_to_matched_item(tmp_path):
    item_id = _seed_knowledge(tmp_path)
    annotated = knowledge_base.annotate_reflexivity_observation(
        tmp_path, query_text="冒险投资 现金", note="用户在看到这条复盘后，后续表现出更保守的选择倾向"
    )
    assert annotated == [item_id]

    items = knowledge_base.load_all(tmp_path)
    assert items[0].notes == ["用户在看到这条复盘后，后续表现出更保守的选择倾向"]

    # 重复调用同一条 note 不应该重复追加。
    annotated_again = knowledge_base.annotate_reflexivity_observation(
        tmp_path, query_text="冒险投资 现金", note="用户在看到这条复盘后，后续表现出更保守的选择倾向"
    )
    assert annotated_again == []
    items_after = knowledge_base.load_all(tmp_path)
    assert items_after[0].notes == ["用户在看到这条复盘后，后续表现出更保守的选择倾向"]


def test_annotate_reflexivity_observation_no_match_returns_empty(tmp_path):
    _seed_knowledge(tmp_path)
    annotated = knowledge_base.annotate_reflexivity_observation(
        tmp_path, query_text="完全不相关的查询词汇", note="不该被写入"
    )
    assert annotated == []


def _make_sim_with_history(tmp_path: Path, *, risk_levels_after) -> str:
    """创建一个实例，第 1 步（`up_to_step`）之后再追加若干步，每步的
    `chosen_option_id` 对应上一步里声明了给定 `risk_level` 的选项。"""
    manifest = materialize_simulation(
        tmp_path,
        template="life_sim",
        intent="模拟一段创业经历",
        title="创业三年",
        summary="刚起步",
        vars={"cash": 1000},
        options=[
            ChoiceOption(id="a", label="冒险扩张", description="", risk_level="high")
        ],
        settings={},
    )
    store = SimStore.for_root(tmp_path, manifest.sim_id)
    history = store.load_history("main")

    # 第 1 步：选中了 step 0 里 risk_level=high 的选项 a（复盘覆盖到
    # 这一步为止，代表"复盘之前"的选择偏冒险）。
    history.append(
        SimState(
            step=1,
            summary="选择了冒险扩张",
            narrative="投入了大量资金扩张",
            vars={"cash": 1200},
            chosen_option_id="a",
            chosen_by="user",
            options=[ChoiceOption(id="b", label="保守经营", description="", risk_level="low")],
        )
    )

    # 之后每一步都选中上一步里 risk_level 由调用方指定的选项。
    for i, level in enumerate(risk_levels_after, start=2):
        next_options = [ChoiceOption(id="b", label="下一步", description="", risk_level="low")]
        history.append(
            SimState(
                step=i,
                summary=f"第 {i} 步",
                narrative="继续推进",
                vars={"cash": 1200 + i * 10},
                chosen_option_id="b",
                chosen_by="user",
                options=next_options,
            )
        )
        # 修正上一条历史里的 options，使其声明的 risk_level 是本次
        # 要验证的值（`chosen_option_id="b"` 对应的选项）。
        history[-2].options = [
            ChoiceOption(id="b", label="上一步给出的选项", description="", risk_level=level)
        ]

    atomic_write_jsonl(store.state_history_path("main"), [s.to_dict() for s in history])
    return manifest.sim_id


def test_evaluate_and_annotate_requires_pending_retrospective(tmp_path):
    sim_id = _make_sim_with_history(tmp_path, risk_levels_after=["low", "low"])
    # 还没有生成过任何复盘报告时，没有可处理的对象。
    assert reflexivity.evaluate_and_annotate(tmp_path, sim_id) is None


def test_evaluate_and_annotate_insufficient_samples_stays_pending(tmp_path):
    sim_id = _make_sim_with_history(tmp_path, risk_levels_after=["low"])
    record = retro.RetrospectiveRecord(
        id="r1", sim_id=sim_id, branch="main", up_to_step=1,
        created_at="2026-09-20T00:00:00+00:00",
        report=_report("过度冒险是主要败因"),
        suggested_direction="reduce_risk",
    )
    retro._save_append(tmp_path, sim_id, record)

    result = reflexivity.evaluate_and_annotate(tmp_path, sim_id)
    assert result is None
    # 样本不足时不应该标记为已处理，留给下次有更多历史时重新判断。
    records = retro.load_for_branch(tmp_path, sim_id, "main")
    assert records[0].reflexivity_annotated is False


def test_evaluate_and_annotate_consistent_direction_annotates_once(tmp_path):
    sim_id = _make_sim_with_history(tmp_path, risk_levels_after=["low", "low"])
    _seed_knowledge(tmp_path)
    record = retro.RetrospectiveRecord(
        id="r1", sim_id=sim_id, branch="main", up_to_step=1,
        created_at="2026-09-20T00:00:00+00:00",
        report=_report("过度冒险投资是主要败因"),
        suggested_direction="reduce_risk",
    )
    retro._save_append(tmp_path, sim_id, record)

    result = reflexivity.evaluate_and_annotate(tmp_path, sim_id)
    assert result == "用户在看到这条复盘后，后续表现出更保守的选择倾向"

    records = retro.load_for_branch(tmp_path, sim_id, "main")
    assert records[0].reflexivity_annotated is True

    # 已处理过的复盘不会被再次处理（即使再调用一次）。
    assert reflexivity.evaluate_and_annotate(tmp_path, sim_id) is None


def test_evaluate_and_annotate_inconsistent_direction_no_annotation(tmp_path):
    # 复盘建议"更保守"，但用户后续仍然全是高风险选择——方向不一致。
    sim_id = _make_sim_with_history(tmp_path, risk_levels_after=["high", "high"])
    _seed_knowledge(tmp_path)
    record = retro.RetrospectiveRecord(
        id="r1", sim_id=sim_id, branch="main", up_to_step=1,
        created_at="2026-09-20T00:00:00+00:00",
        report=_report("过度冒险投资是主要败因"),
        suggested_direction="reduce_risk",
    )
    retro._save_append(tmp_path, sim_id, record)

    result = reflexivity.evaluate_and_annotate(tmp_path, sim_id)
    assert result is None
    records = retro.load_for_branch(tmp_path, sim_id, "main")
    # 判断过了（不会被重复判断），只是没有产出标注。
    assert records[0].reflexivity_annotated is True
    items = knowledge_base.load_all(tmp_path)
    assert items[0].notes == []


def test_evaluate_and_annotate_no_direction_marks_processed_without_annotation(tmp_path):
    sim_id = _make_sim_with_history(tmp_path, risk_levels_after=["low", "low"])
    record = retro.RetrospectiveRecord(
        id="r1", sim_id=sim_id, branch="main", up_to_step=1,
        created_at="2026-09-20T00:00:00+00:00",
        report=_report("整体表现平稳，没有明显的败因"),
        suggested_direction=None,
    )
    retro._save_append(tmp_path, sim_id, record)

    result = reflexivity.evaluate_and_annotate(tmp_path, sim_id)
    assert result is None
    records = retro.load_for_branch(tmp_path, sim_id, "main")
    assert records[0].reflexivity_annotated is True
