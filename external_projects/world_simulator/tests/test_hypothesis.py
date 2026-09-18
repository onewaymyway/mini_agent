"""tests/test_hypothesis.py — 阶段二十一（Hypothesis Engine）单元测试。

对应 `next_doc/world_simulator_toward_universal_simulator_plan.md`
4.15 节的验收标准：对一个"AI 成本"被标注为 `confidence: low` 的
实例，建议接口能看到它被列为候选；分叉出三个假设分支各推进几步后，
`find_robust_outcomes()` 能正确区分"哪些结果字段在多条分支里都朝
同一方向变化"和"哪些字段差异巨大"。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from world_simulator import hypothesis as hyp_mod
from world_simulator.state_model import ChoiceOption, SimManifest, SimState
from world_simulator.store import SimStore, now_iso


class _FakeStatus:
    def __init__(self, value: str) -> None:
        self.value = value


class _FakeStep:
    def __init__(self, step_id: str) -> None:
        self.id = step_id
        self.skill_name = None


class _FakeWorkflow:
    def __init__(self, steps):
        self.steps = steps


def _write_result_file(tmp_path: Path, name: str, payload: dict) -> str:
    p = tmp_path / name
    p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return str(p)


def _make_sim(data_dir: Path, sim_id: str, *, uncertain_fields=None, causal_links=None):
    store = SimStore.for_root(data_dir, sim_id)
    ts = now_iso()
    store.save_manifest(
        SimManifest(
            sim_id=sim_id, template="life_sim", intent="i", title="t",
            created_at=ts, updated_at=ts, pilot_mode="manual",
        )
    )
    store.append_state(
        SimState(
            step=0, summary="s0", vars={"ai_cost_trend": 100},
            options=[
                ChoiceOption(id="a", label="选项 A"),
                ChoiceOption(id="b", label="选项 B"),
            ],
            uncertain_fields=uncertain_fields or [],
            causal_links=causal_links or [],
        )
    )
    return store


# ── suggest_critical_uncertainties ──────────────────────────────────


def test_suggest_critical_uncertainties_filters_low_confidence_and_counts_causal_links(tmp_path):
    data_dir = tmp_path / "data"
    store = _make_sim(
        data_dir, "sim1",
        uncertain_fields=[
            {"field": "ai_cost_trend", "confidence": "low", "note": "行业波动大"},
            {"field": "user_mood", "confidence": "medium"},
        ],
        causal_links=[
            {"driver": "d1", "affected_fields": ["ai_cost_trend", "cash"]},
            {"driver": "d2", "affected_fields": ["ai_cost_trend"]},
        ],
    )
    manifest = store.load_manifest()
    current = store.load_current_state("main")

    results = hyp_mod.suggest_critical_uncertainties(manifest, current)
    assert len(results) == 1
    assert results[0]["field"] == "ai_cost_trend"
    assert results[0]["confidence"] == "low"
    assert "2 条因果链的共同起点" in results[0]["why"]
    assert results[0]["note"] == "行业波动大"


def test_suggest_critical_uncertainties_empty_when_no_low_confidence_fields(tmp_path):
    data_dir = tmp_path / "data"
    store = _make_sim(
        data_dir, "sim1",
        uncertain_fields=[{"field": "user_mood", "confidence": "medium"}],
    )
    manifest = store.load_manifest()
    current = store.load_current_state("main")
    assert hyp_mod.suggest_critical_uncertainties(manifest, current) == []


def test_suggest_critical_uncertainties_empty_when_no_uncertain_fields(tmp_path):
    data_dir = tmp_path / "data"
    store = _make_sim(data_dir, "sim1")
    manifest = store.load_manifest()
    current = store.load_current_state("main")
    assert hyp_mod.suggest_critical_uncertainties(manifest, current) == []


def test_suggest_critical_uncertainties_ignores_entries_without_field(tmp_path):
    data_dir = tmp_path / "data"
    store = _make_sim(
        data_dir, "sim1",
        uncertain_fields=[{"confidence": "low"}, "不是字典"],
    )
    manifest = store.load_manifest()
    current = store.load_current_state("main")
    assert hyp_mod.suggest_critical_uncertainties(manifest, current) == []


# ── run_hypothesis_worlds ────────────────────────────────────────────


def _patch_advance_step_by_assumption(monkeypatch, tmp_path, *, value_by_keyword: dict):
    """打桩 `advance_step`：按 `inputs["decision_context"]` 里出现的
    关键词（假设的具体方向）决定这一步给 `ai_cost_trend` 加多少，
    模拟"LLM 认真遵循了假设锚定"这件事，供 `find_robust_outcomes()`
    的判定逻辑做端到端验证。"""
    step_step = _FakeStep("step")

    class FakeStore:
        def __init__(self, root):
            pass

        def load(self, name):
            return _FakeWorkflow([step_step])

    class FakeRunner:
        def __init__(self, cfg):
            pass

        def run(self, wf, inputs):
            decision_context = inputs.get("decision_context", "")
            delta = 0
            for keyword, d in value_by_keyword.items():
                if keyword in decision_context:
                    delta = d
                    break
            current_value = inputs.get("current_vars_json", "")
            try:
                base = json.loads(current_value).get("ai_cost_trend", 100)
            except Exception:
                base = 100
            result_file = _write_result_file(
                tmp_path, f"advance_result_{id(inputs)}.json",
                {
                    "next_summary": "ok", "narrative": "ok",
                    "next_vars": {"ai_cost_trend": base + delta},
                    "options": [{"id": "a", "label": "选项 A"}, {"id": "b", "label": "选项 B"}],
                    "chosen_option_id": "a", "chosen_reason": "r",
                },
            )
            return SimpleNamespace(
                status="done",
                step_results=[SimpleNamespace(step_id="step", status=_FakeStatus("done"), result_file=result_file)],
            )

    monkeypatch.setattr("mini_agent.workflow.store.WorkflowStore", FakeStore)
    monkeypatch.setattr("mini_agent.workflow.runner.WorkflowRunner", FakeRunner)


def test_run_hypothesis_worlds_forks_one_branch_per_hypothesis(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    _make_sim(data_dir, "sim1")
    _patch_advance_step_by_assumption(monkeypatch, tmp_path, value_by_keyword={"快速下降": -50, "缓慢下降": -10, "基本不变": 0})

    hypotheses = [
        {"assumption": "快速下降", "why": "AI 芯片成本快速下探"},
        {"assumption": "缓慢下降"},
        {"assumption": "基本不变"},
    ]
    results = hyp_mod.run_hypothesis_worlds(
        object(), tmp_path, data_dir, "sim1",
        field="ai_cost_trend", hypotheses=hypotheses, steps=2,
    )

    assert len(results) == 3
    assert all(r.steps_done == 2 for r in results)
    assert all(not r.error for r in results)
    branch_ids = {r.branch for r in results}
    assert len(branch_ids) == 3  # 三个假设各自跑在独立分支上

    # 活跃分支应该恢复成运行前那一条
    store = SimStore.for_root(data_dir, "sim1")
    assert store.load_manifest().branch == "main"
    assert len(store.load_history("main")) == 1  # main 分支自身没被影响


def test_run_hypothesis_worlds_skips_entries_without_assumption(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    _make_sim(data_dir, "sim1")
    _patch_advance_step_by_assumption(monkeypatch, tmp_path, value_by_keyword={"快速下降": -50})

    hypotheses = [{"assumption": "快速下降"}, {"why": "缺少方向描述"}, {}]
    results = hyp_mod.run_hypothesis_worlds(
        object(), tmp_path, data_dir, "sim1",
        field="ai_cost_trend", hypotheses=hypotheses, steps=1,
    )
    assert len(results) == 3
    assert results[0].error is None
    assert results[1].branch == "?" and results[1].error and "跳过" in results[1].error
    assert results[2].branch == "?" and results[2].error and "跳过" in results[2].error


# ── find_robust_outcomes ─────────────────────────────────────────────


def test_find_robust_outcomes_end_to_end_distinguishes_robust_and_fragile(tmp_path, monkeypatch):
    """端到端：三个假设分支推进后，`ai_cost_trend` 因为假设锚定生效
    应该呈现明显分歧（fragile），而 `stable_field`（打桩输出里始终
    不变）应该被判定为稳健（robust）。"""
    data_dir = tmp_path / "data"
    store = _make_sim(data_dir, "sim1")

    step_step = _FakeStep("step")

    class FakeStore:
        def __init__(self, root):
            pass

        def load(self, name):
            return _FakeWorkflow([step_step])

    class FakeRunner:
        def __init__(self, cfg):
            pass

        def run(self, wf, inputs):
            decision_context = inputs.get("decision_context", "")
            trend_value = {"快速下降": 20, "缓慢下降": 90, "基本不变": 150}
            value = next((v for k, v in trend_value.items() if k in decision_context), 100)
            result_file = _write_result_file(
                tmp_path, f"advance_result_{id(inputs)}.json",
                {
                    "next_summary": "ok", "narrative": "ok",
                    "next_vars": {"ai_cost_trend": value, "stable_field": 42},
                    "options": [{"id": "a", "label": "选项 A"}, {"id": "b", "label": "选项 B"}],
                    "chosen_option_id": "a", "chosen_reason": "r",
                },
            )
            return SimpleNamespace(
                status="done",
                step_results=[SimpleNamespace(step_id="step", status=_FakeStatus("done"), result_file=result_file)],
            )

    monkeypatch.setattr("mini_agent.workflow.store.WorkflowStore", FakeStore)
    monkeypatch.setattr("mini_agent.workflow.runner.WorkflowRunner", FakeRunner)

    hypotheses = [
        {"assumption": "快速下降"}, {"assumption": "缓慢下降"}, {"assumption": "基本不变"},
    ]
    results = hyp_mod.run_hypothesis_worlds(
        object(), tmp_path, data_dir, "sim1",
        field="ai_cost_trend", hypotheses=hypotheses, steps=1,
    )
    branch_ids = [r.branch for r in results]

    outcome = hyp_mod.find_robust_outcomes(
        data_dir, "sim1", branch_ids, ["ai_cost_trend", "stable_field"]
    )
    assert "stable_field" in outcome["robust_fields"]
    assert "ai_cost_trend" in outcome["fragile_fields"]
    assert {s.field for s in outcome["stats"]} == {"ai_cost_trend", "stable_field"}


def test_find_robust_outcomes_categorical_majority_is_robust(tmp_path):
    data_dir = tmp_path / "data"
    for i, val in enumerate(["转型", "转型", "转型", "坚守"]):
        store = SimStore.for_root(data_dir, "sim1")
        if i == 0:
            store.save_manifest(
                SimManifest(
                    sim_id="sim1", template="life_sim", intent="i", title="t",
                    created_at=now_iso(), updated_at=now_iso(), pilot_mode="manual",
                )
            )
        store.append_state(SimState(step=0, summary="s", vars={"stage": val}), branch=f"b{i}")

    outcome = hyp_mod.find_robust_outcomes(
        data_dir, "sim1", ["b0", "b1", "b2", "b3"], ["stage"]
    )
    assert outcome["robust_fields"] == ["stage"]
    assert outcome["fragile_fields"] == []


def test_find_robust_outcomes_skips_missing_branches_and_missing_fields(tmp_path):
    data_dir = tmp_path / "data"
    store = SimStore.for_root(data_dir, "sim1")
    store.save_manifest(
        SimManifest(
            sim_id="sim1", template="life_sim", intent="i", title="t",
            created_at=now_iso(), updated_at=now_iso(), pilot_mode="manual",
        )
    )
    store.append_state(SimState(step=0, summary="s", vars={"a": 1}), branch="b0")

    outcome = hyp_mod.find_robust_outcomes(
        data_dir, "sim1", ["b0", "does_not_exist"], ["a", "missing_field"]
    )
    # missing_field 在所有能读到的分支里都取不到值 -> kind == "missing"，
    # 既不算稳健也不算分歧
    assert "missing_field" not in outcome["robust_fields"]
    assert "missing_field" not in outcome["fragile_fields"]
