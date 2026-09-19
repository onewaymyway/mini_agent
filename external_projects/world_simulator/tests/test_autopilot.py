"""tests/test_autopilot.py — autopilot 单元测试。

同样不真的调用 LLM：用 monkeypatch 打桩 workflow 引擎，让打桩的
`advance_step` 结果里带上 `chosen_option_id`/`chosen_reason`/
`major_decision`，验证 engine.advance() 的自动挡分支解析是否正确，
以及 autopilot.py 的批量推进/review_mode 暂停逻辑是否符合预期。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from world_simulator import autopilot as ap_mod
from world_simulator import engine as engine_mod
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


def _make_sim_with_options(data_dir: Path, sim_id: str, *, pilot_mode="manual", autopilot=None):
    store = SimStore.for_root(data_dir, sim_id)
    ts = now_iso()
    store.save_manifest(
        SimManifest(
            sim_id=sim_id, template="life_sim", intent="i", title="t",
            created_at=ts, updated_at=ts, pilot_mode=pilot_mode, autopilot=autopilot or {},
        )
    )
    store.append_state(
        SimState(
            step=0, summary="s0",
            vars={"age": 20},
            options=[
                ChoiceOption(id="safe", label="稳妥选项"),
                ChoiceOption(id="risky", label="激进选项"),
            ],
        )
    )
    return store


def _patch_advance_step_workflow(monkeypatch, tmp_path, response_payload, capture: dict):
    step_step = _FakeStep("step")

    class FakeStore:
        def __init__(self, root):
            pass

        def load(self, name):
            assert name == "advance_step"
            return _FakeWorkflow([step_step])

    class FakeRunner:
        def __init__(self, cfg):
            pass

        def run(self, wf, inputs):
            capture["inputs"] = inputs
            result_file = _write_result_file(tmp_path, "advance_result.json", response_payload)
            return SimpleNamespace(
                status="done",
                step_results=[
                    SimpleNamespace(step_id="step", status=_FakeStatus("done"), result_file=result_file)
                ],
            )

    monkeypatch.setattr("mini_agent.workflow.store.WorkflowStore", FakeStore)
    monkeypatch.setattr("mini_agent.workflow.runner.WorkflowRunner", FakeRunner)
    return step_step


def test_autopilot_records_llm_chosen_option(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    _make_sim_with_options(
        data_dir, "sim1", pilot_mode="autopilot",
        autopilot={"enabled": True, "principles": ["优先稳定"], "risk_preference": "conservative",
                   "review_mode": "silent"},
    )
    capture: dict = {}
    _patch_advance_step_workflow(
        monkeypatch, tmp_path,
        {
            "next_summary": "选择了稳妥方向",
            "narrative": "代理选择了稳妥选项",
            "next_vars": {"age": 21},
            "options": [],
            "chosen_option_id": "safe",
            "chosen_reason": "符合保守风险偏好",
        },
        capture,
    )

    next_state = ap_mod.run_autopilot_step(object(), tmp_path, data_dir, "sim1")

    assert next_state.step == 1
    assert "决策者画像" in capture["inputs"]["decision_context"] or "代理" in capture["inputs"]["decision_context"]
    assert json.loads(capture["inputs"]["current_options_json"])[0]["id"] == "safe"

    store = SimStore.for_root(data_dir, "sim1")
    history = store.load_history()
    assert history[0].chosen_option_id == "safe"
    assert history[0].chosen_by == "autopilot"
    assert history[0].chosen_reason == "符合保守风险偏好"


def test_autopilot_rejects_hallucinated_option_id(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    _make_sim_with_options(
        data_dir, "sim1", pilot_mode="autopilot",
        autopilot={"enabled": True, "review_mode": "silent"},
    )
    capture: dict = {}
    _patch_advance_step_workflow(
        monkeypatch, tmp_path,
        {
            "next_summary": "x", "narrative": "x", "next_vars": {}, "options": [],
            "chosen_option_id": "not_a_real_option",
        },
        capture,
    )

    with pytest.raises(engine_mod.SimEngineError):
        ap_mod.run_autopilot_step(object(), tmp_path, data_dir, "sim1")


def test_autopilot_accepts_hallucinated_option_id_when_allow_custom_options(tmp_path, monkeypatch):
    """allow_custom_options=True 时，LLM 把编造的新方向塞进
    `chosen_option_id`（而不是按约定用 `custom_option_label`）不应该
    再中止推进——应该被当成一次合法的自定义选项收下。"""
    data_dir = tmp_path / "data"
    _make_sim_with_options(
        data_dir, "sim1", pilot_mode="autopilot",
        autopilot={"enabled": True, "review_mode": "silent", "allow_custom_options": True},
    )
    capture: dict = {}
    _patch_advance_step_workflow(
        monkeypatch, tmp_path,
        {
            "next_summary": "x", "narrative": "x", "next_vars": {}, "options": [],
            "chosen_option_id": "clone_decision_layer_start",
            "chosen_reason": "r",
        },
        capture,
    )

    next_state = ap_mod.run_autopilot_step(object(), tmp_path, data_dir, "sim1")
    assert next_state.step == 1

    history = SimStore.for_root(data_dir, "sim1").load_history()
    assert history[0].chosen_by == "autopilot"
    assert history[0].chosen_option_id is not None
    assert history[0].chosen_option_id.startswith("custom_")
    assert history[0].chosen_reason == "r"


def test_autopilot_disabled_raises(tmp_path):
    data_dir = tmp_path / "data"
    _make_sim_with_options(data_dir, "sim1", pilot_mode="manual")
    with pytest.raises(ap_mod.AutopilotDisabledError):
        ap_mod.run_autopilot_step(object(), tmp_path, data_dir, "sim1")


def test_pause_on_major_decision(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    _make_sim_with_options(
        data_dir, "sim1", pilot_mode="autopilot",
        autopilot={"enabled": True, "review_mode": "pause_on_major_decision"},
    )
    capture: dict = {}
    _patch_advance_step_workflow(
        monkeypatch, tmp_path,
        {
            "next_summary": "重大转折", "narrative": "x", "next_vars": {}, "options": [],
            "chosen_option_id": "risky", "chosen_reason": "r", "major_decision": True,
        },
        capture,
    )

    ap_mod.run_autopilot_step(object(), tmp_path, data_dir, "sim1")

    store = SimStore.for_root(data_dir, "sim1")
    manifest = store.load_manifest()
    assert manifest.status == "paused"


def test_batch_autopilot_skips_manual_instances(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    _make_sim_with_options(data_dir, "manual1", pilot_mode="manual")
    _make_sim_with_options(
        data_dir, "auto1", pilot_mode="autopilot",
        autopilot={"enabled": True, "review_mode": "silent"},
    )
    capture: dict = {}
    _patch_advance_step_workflow(
        monkeypatch, tmp_path,
        {
            "next_summary": "s1", "narrative": "n", "next_vars": {"age": 21}, "options": [],
            "chosen_option_id": "safe", "chosen_reason": "r",
        },
        capture,
    )

    results = ap_mod.run_batch_autopilot(object(), tmp_path, data_dir, steps=1)

    assert [r.sim_id for r in results] == ["auto1"]
    assert results[0].ok is True
    assert results[0].next_step == 1


def test_manual_advance_with_custom_option_bypasses_options_list(tmp_path, monkeypatch):
    """手动挡：用户自己写的选项不需要预先出现在候选列表里。"""
    data_dir = tmp_path / "data"
    _make_sim_with_options(data_dir, "sim1", pilot_mode="manual")
    capture: dict = {}
    _patch_advance_step_workflow(
        monkeypatch, tmp_path,
        {"next_summary": "按自定义方向推进", "narrative": "x", "next_vars": {"age": 21}, "options": []},
        capture,
    )

    next_state = engine_mod.advance(
        object(), tmp_path, data_dir, "sim1",
        custom_option={"label": "先按兵不动", "description": "观察一个季度再说"},
        chosen_by="user",
    )

    assert next_state.step == 1
    chosen_option_sent = json.loads(capture["inputs"]["chosen_option_json"])
    assert chosen_option_sent["label"] == "先按兵不动"
    assert chosen_option_sent["id"].startswith("custom_")

    store = SimStore.for_root(data_dir, "sim1")
    history = store.load_history()
    assert history[0].chosen_option_id.startswith("custom_")
    assert history[0].chosen_by == "user"


def test_manual_advance_custom_option_requires_label(tmp_path):
    data_dir = tmp_path / "data"
    _make_sim_with_options(data_dir, "sim1", pilot_mode="manual")
    with pytest.raises(engine_mod.SimEngineError):
        engine_mod.advance(
            object(), tmp_path, data_dir, "sim1",
            custom_option={"label": "  ", "description": "空标题不合法"},
        )


def test_autopilot_custom_option_accepted_when_llm_proposes_it(tmp_path, monkeypatch):
    """自动挡：画像里没有强制要求 LLM 一定要输出 chosen_option_id，
    LLM 判断候选列表都不合理时，可以改为输出 custom_option_label/
    custom_option_description，engine 应当接收并落盘为 autopilot 选择。
    """
    data_dir = tmp_path / "data"
    _make_sim_with_options(
        data_dir, "sim1", pilot_mode="autopilot",
        autopilot={"enabled": True, "review_mode": "silent", "allow_custom_options": True},
    )
    capture: dict = {}
    _patch_advance_step_workflow(
        monkeypatch, tmp_path,
        {
            "next_summary": "代理提出了新方向", "narrative": "x", "next_vars": {}, "options": [],
            "custom_option_label": "先谈判争取更好条件",
            "custom_option_description": "候选列表里两个选项都太极端",
            "chosen_reason": "候选列表缺少折中方案",
        },
        capture,
    )

    next_state = ap_mod.run_autopilot_step(object(), tmp_path, data_dir, "sim1")

    assert next_state.step == 1
    assert "允许你跳出这个列表" in capture["inputs"]["decision_context"]

    store = SimStore.for_root(data_dir, "sim1")
    history = store.load_history()
    assert history[0].chosen_option_id.startswith("custom_")
    assert history[0].chosen_by == "autopilot"
    assert history[0].chosen_reason == "候选列表缺少折中方案"


def test_decision_context_denies_custom_option_by_default(tmp_path):
    """未开启 allow_custom_options 时，画像文本应明确要求代理只能从
    候选列表里选，不应该出现"允许跳出"这句话。
    """
    manifest = SimManifest(
        sim_id="sim1", template="life_sim", intent="i", title="t",
        created_at=now_iso(), updated_at=now_iso(), pilot_mode="autopilot",
        autopilot={"enabled": True, "review_mode": "silent"},
    )
    context = ap_mod._build_decision_context(manifest)
    assert "不要自己发明列表之外的新选项" in context
    assert "允许你跳出这个列表" not in context


def test_decision_context_observer_mode_off_by_default(tmp_path):
    """阶段三十一，4.25 节：`settings.observer_mode` 未声明时，画像
    文本不应该出现 Observer Mode 的提示。"""
    manifest = SimManifest(
        sim_id="sim1", template="life_sim", intent="i", title="t",
        created_at=now_iso(), updated_at=now_iso(), pilot_mode="autopilot",
        autopilot={"enabled": True, "review_mode": "silent"},
    )
    context = ap_mod._build_decision_context(manifest)
    assert "Observer Mode" not in context


def test_decision_context_observer_mode_on_adds_background_evolution_hint(tmp_path):
    """`settings.observer_mode = True` 时，画像文本应该追加"优先让
    背景/宏观因果线自然演化、尽量不产生要求用户当下做重大决策的新
    分支"这类提示——只是提示，不应该出现"必须"/"禁止"这类硬约束
    措辞（延续 hint 类字段"不强制"的一贯风格）。"""
    manifest = SimManifest(
        sim_id="sim1", template="life_sim", intent="i", title="t",
        created_at=now_iso(), updated_at=now_iso(), pilot_mode="autopilot",
        autopilot={"enabled": True, "review_mode": "silent"},
        settings={"observer_mode": True},
    )
    context = ap_mod._build_decision_context(manifest)
    assert "Observer Mode" in context
    assert "背景/宏观因果线" in context
    assert "必须" not in context and "禁止" not in context


def test_run_comparison_experiment_forks_one_branch_per_profile(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    _make_sim_with_options(data_dir, "sim1", pilot_mode="manual")

    capture: dict = {}
    _patch_advance_step_workflow(
        monkeypatch, tmp_path,
        {
            "next_summary": "ok", "narrative": "ok", "next_vars": {},
            "options": [{"id": "safe", "label": "稳妥选项"}, {"id": "risky", "label": "激进选项"}],
            "chosen_option_id": "safe", "chosen_reason": "r",
        },
        capture,
    )

    profiles = [
        {"name": "保守派", "risk_preference": "conservative", "review_mode": "silent"},
        {"name": "进取派", "risk_preference": "aggressive", "review_mode": "silent"},
    ]
    results = ap_mod.run_comparison_experiment(
        object(), tmp_path, data_dir, "sim1",
        source_branch="main", from_step=0, steps=2, profiles=profiles,
    )

    assert [r.profile_name for r in results] == ["保守派", "进取派"]
    assert all(r.steps_done == 2 for r in results)
    assert all(not r.error for r in results)
    branch_ids = {r.branch for r in results}
    assert len(branch_ids) == 2  # 两份画像各自跑在独立分支上

    # 实验结束后，实例的活跃分支应该恢复成实验开始前那一条（main），
    # 不应该被顺带切换到最后一条策略分支上。
    store = SimStore.for_root(data_dir, "sim1")
    manifest = store.load_manifest()
    assert manifest.branch == "main"

    # main 分支自身的历史不应该被实验分支的推进影响到（各自独立）。
    assert len(store.load_history("main")) == 1


def test_batch_autopilot_continues_after_single_failure(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    _make_sim_with_options(
        data_dir, "auto_bad", pilot_mode="autopilot",
        autopilot={"enabled": True, "review_mode": "silent"},
    )
    _make_sim_with_options(
        data_dir, "auto_good", pilot_mode="autopilot",
        autopilot={"enabled": True, "review_mode": "silent"},
    )

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
            if "auto_bad" in json.dumps(inputs, ensure_ascii=False) or inputs["title"] == "t" and False:
                pass
            # 用 inputs 里的 current_step/summary 无法区分是哪个 sim，改用一个
            # 模块级计数器：第一次调用（按 list_simulations 的顺序，"auto_bad"
            # 先于 "auto_good"）制造失败，第二次成功。
            call_count = getattr(FakeRunner, "_calls", 0)
            FakeRunner._calls = call_count + 1
            if call_count == 0:
                return SimpleNamespace(status="failed", step_results=[
                    SimpleNamespace(step_id="step", status=_FakeStatus("failed"), result_file=None, error="boom")
                ])
            result_file = _write_result_file(
                tmp_path, f"advance_result_{call_count}.json",
                {
                    "next_summary": "ok", "narrative": "ok", "next_vars": {}, "options": [],
                    "chosen_option_id": "safe", "chosen_reason": "r",
                },
            )
            return SimpleNamespace(status="done", step_results=[
                SimpleNamespace(step_id="step", status=_FakeStatus("done"), result_file=result_file)
            ])

    monkeypatch.setattr("mini_agent.workflow.store.WorkflowStore", FakeStore)
    monkeypatch.setattr("mini_agent.workflow.runner.WorkflowRunner", FakeRunner)

    results = ap_mod.run_batch_autopilot(object(), tmp_path, data_dir, steps=1)

    by_id = {r.sim_id: r for r in results}
    assert by_id["auto_bad"].ok is False
    assert by_id["auto_good"].ok is True


def test_run_repeated_experiment_forks_n_branches_with_same_profile(tmp_path, monkeypatch):
    """阶段十（4.2 节）：`run_repeated_experiment()` 应该用**同一份**
    策略画像 fork 出 n_repeats 条独立分支，每条分支各自推进相同步数，
    且每条分支的 `final_vars` 都能取到（供后续统计聚合使用）。"""
    data_dir = tmp_path / "data"
    _make_sim_with_options(data_dir, "sim1", pilot_mode="manual")

    # 每次调用返回不同的 age，模拟"同一份策略、不同的 LLM 随机结果"。
    call_count = {"n": 0}
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
            call_count["n"] += 1
            age = 20 + call_count["n"]
            result_file = _write_result_file(
                tmp_path, f"advance_result_{call_count['n']}.json",
                {
                    "next_summary": "ok", "narrative": "ok", "next_vars": {"age": age},
                    "options": [{"id": "safe", "label": "稳妥选项"}, {"id": "risky", "label": "激进选项"}],
                    "chosen_option_id": "safe", "chosen_reason": "r",
                },
            )
            return SimpleNamespace(
                status="done",
                step_results=[
                    SimpleNamespace(step_id="step", status=_FakeStatus("done"), result_file=result_file)
                ],
            )

    monkeypatch.setattr("mini_agent.workflow.store.WorkflowStore", FakeStore)
    monkeypatch.setattr("mini_agent.workflow.runner.WorkflowRunner", FakeRunner)

    profile = {"risk_preference": "balanced", "review_mode": "silent"}
    results = ap_mod.run_repeated_experiment(
        object(), tmp_path, data_dir, "sim1",
        source_branch="main", from_step=0, steps=1, profile=profile, n_repeats=3,
    )

    assert len(results) == 3
    assert [r.profile_name for r in results] == ["重复采样 #1", "重复采样 #2", "重复采样 #3"]
    assert all(r.steps_done == 1 for r in results)
    branch_ids = {r.branch for r in results}
    assert len(branch_ids) == 3

    # 每条分支的 age 应该各不相同（来自各自独立的 LLM 调用结果），
    # 且都能喂给 analysis.aggregate_field_stats() 算出有意义的统计摘要。
    ages = sorted(r.final_vars["age"] for r in results)
    assert ages == [21, 22, 23]

    from world_simulator.analysis import aggregate_field_stats

    stats = aggregate_field_stats([r.final_vars for r in results], ["age"])
    assert stats[0].kind == "numeric"
    assert stats[0].count == 3
    assert stats[0].mean == 22.0
    assert stats[0].min == 21.0
    assert stats[0].max == 23.0

    # 实验结束后应该切回实验开始前的活跃分支（main），不留在最后一条
    # 重复分支上。
    store = SimStore.for_root(data_dir, "sim1")
    assert store.load_manifest().branch == "main"
