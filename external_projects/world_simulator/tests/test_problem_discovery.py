"""tests/test_problem_discovery.py — Problem Discovery Engine 单元测试
（第九轮批次三，`next_doc/world_simulator_problem_capability_gap_
plan.md` 2.4 节）。写法仿照 `test_hypothesis.py` 里
`suggest_experiment_design` 相关用例的既有手法（假 `WorkflowStore`/
`WorkflowRunner` + `_agent_output()` 构造 `type: agent` 步骤的原始
文本输出）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from world_simulator import problem_discovery as pd_mod
from world_simulator.state_model import SimManifest
from world_simulator.store import SimStore, now_iso


class _FakeStatus:
    def __init__(self, value: str) -> None:
        self.value = value


def _agent_output(payload: dict) -> str:
    """`type: agent` step 靠 `StepResult.output`（纯 JSON 文本）传结果，
    同 `test_hypothesis.py::_agent_output`。"""
    return json.dumps(payload, ensure_ascii=False)


def _make_sim(data_dir: Path, sim_id: str, *, settings=None) -> SimManifest:
    store = SimStore.for_root(data_dir, sim_id)
    ts = now_iso()
    manifest = SimManifest(
        sim_id=sim_id, template="life_sim", intent="i", title="t",
        created_at=ts, updated_at=ts, settings=settings or {},
    )
    store.save_manifest(manifest)
    return manifest


# ── suggest_problems() ──────────────────────────────────────────────


def test_suggest_problems_assembles_input_and_parses_result(tmp_path, monkeypatch):
    captured_inputs = {}

    class FakeWorkflowStore:
        def __init__(self, root):
            pass

        def load(self, name):
            assert name == "problem_discovery"
            return SimpleNamespace(steps=[SimpleNamespace(id="problem_discovery")])

    class FakeRunner:
        def __init__(self, cfg):
            pass

        def run(self, wf, inputs):
            captured_inputs.update(inputs)
            output = _agent_output(
                {
                    "observed": [
                        {
                            "symptom": "资金不足，无法招聘核心工程师",
                            "blocked_goal": "在 12 个月内完成产品原型",
                            "missing_capabilities": ["种子轮融资"],
                        }
                    ],
                    "latent": [
                        {
                            "symptom": "如果获客成本持续上升，六个月后现金流将转负",
                            "blocked_goal": "",
                            "missing_capabilities": [],
                        }
                    ],
                }
            )
            return SimpleNamespace(
                status="done",
                step_results=[
                    SimpleNamespace(step_id="problem_discovery", status=_FakeStatus("done"), output=output)
                ],
            )

    monkeypatch.setattr("mini_agent.workflow.store.WorkflowStore", FakeWorkflowStore)
    monkeypatch.setattr("mini_agent.workflow.runner.WorkflowRunner", FakeRunner)

    history = [
        SimpleNamespace(step=1, summary="s1", narrative="n1"),
        SimpleNamespace(step=2, summary="s2", narrative="n2"),
    ]
    result = pd_mod.suggest_problems(
        object(), tmp_path, {"cash": 100}, {"conditions": ["financial_independence"]}, history,
    )

    # 输入正确组装：当前 vars + desired_state + 最近历史都传给了 workflow。
    assert json.loads(captured_inputs["current_vars_json"]) == {"cash": 100}
    assert json.loads(captured_inputs["desired_state_json"]) == {
        "conditions": ["financial_independence"]
    }
    assert json.loads(captured_inputs["recent_history_json"]) == [
        {"step": 1, "summary": "s1", "narrative": "n1"},
        {"step": 2, "summary": "s2", "narrative": "n2"},
    ]

    assert result == {
        "observed": [
            {
                "symptom": "资金不足，无法招聘核心工程师",
                "blocked_goal": "在 12 个月内完成产品原型",
                "missing_capabilities": ["种子轮融资"],
            }
        ],
        "latent": [
            {
                "symptom": "如果获客成本持续上升，六个月后现金流将转负",
                "blocked_goal": "",
                "missing_capabilities": [],
            }
        ],
    }


def test_suggest_problems_truncates_recent_history():
    """`recent_steps` 应该只截取最后 N 步历史，不是全量喂给 prompt。"""
    import mini_agent.workflow.store as store_mod
    import mini_agent.workflow.runner as runner_mod

    captured = {}

    class FakeWorkflowStore:
        def __init__(self, root):
            pass

        def load(self, name):
            return SimpleNamespace(steps=[SimpleNamespace(id="problem_discovery")])

    class FakeRunner:
        def __init__(self, cfg):
            pass

        def run(self, wf, inputs):
            captured.update(inputs)
            output = _agent_output({"observed": [], "latent": []})
            return SimpleNamespace(
                status="done",
                step_results=[
                    SimpleNamespace(step_id="problem_discovery", status=_FakeStatus("done"), output=output)
                ],
            )

    orig_store, orig_runner = store_mod.WorkflowStore, runner_mod.WorkflowRunner
    store_mod.WorkflowStore = FakeWorkflowStore
    runner_mod.WorkflowRunner = FakeRunner
    try:
        history = [SimpleNamespace(step=i, summary=f"s{i}", narrative=f"n{i}") for i in range(1, 9)]
        pd_mod.suggest_problems(object(), Path("/tmp/ws"), {}, {}, history, recent_steps=3)
    finally:
        store_mod.WorkflowStore = orig_store
        runner_mod.WorkflowRunner = orig_runner

    recent = json.loads(captured["recent_history_json"])
    assert [item["step"] for item in recent] == [6, 7, 8]


def test_suggest_problems_skips_malformed_items_and_truncates(tmp_path, monkeypatch):
    class FakeWorkflowStore:
        def __init__(self, root):
            pass

        def load(self, name):
            return SimpleNamespace(steps=[SimpleNamespace(id="problem_discovery")])

    class FakeRunner:
        def __init__(self, cfg):
            pass

        def run(self, wf, inputs):
            output = _agent_output(
                {
                    "observed": [
                        {"symptom": f"symptom{i}"} for i in range(6)
                    ] + [
                        "不是字典，应该被跳过",
                        {"blocked_goal": "没有 symptom，应该被跳过"},
                    ],
                    "latent": [],
                }
            )
            return SimpleNamespace(
                status="done",
                step_results=[
                    SimpleNamespace(step_id="problem_discovery", status=_FakeStatus("done"), output=output)
                ],
            )

    monkeypatch.setattr("mini_agent.workflow.store.WorkflowStore", FakeWorkflowStore)
    monkeypatch.setattr("mini_agent.workflow.runner.WorkflowRunner", FakeRunner)

    result = pd_mod.suggest_problems(object(), tmp_path, {}, {}, [], max_observed=3)
    assert len(result["observed"]) == 3
    assert result["latent"] == []


def test_suggest_problems_raises_when_workflow_missing(tmp_path, monkeypatch):
    class FakeWorkflowStore:
        def __init__(self, root):
            pass

        def load(self, name):
            return None

    monkeypatch.setattr("mini_agent.workflow.store.WorkflowStore", FakeWorkflowStore)

    try:
        pd_mod.suggest_problems(object(), tmp_path, {}, {}, [])
        assert False, "应该抛出 ProblemDiscoveryError"
    except pd_mod.ProblemDiscoveryError:
        pass


def test_suggest_problems_raises_when_workflow_status_not_done(tmp_path, monkeypatch):
    class FakeWorkflowStore:
        def __init__(self, root):
            pass

        def load(self, name):
            return SimpleNamespace(steps=[SimpleNamespace(id="problem_discovery")])

    class FakeRunner:
        def __init__(self, cfg):
            pass

        def run(self, wf, inputs):
            return SimpleNamespace(
                status="failed",
                step_results=[
                    SimpleNamespace(step_id="problem_discovery", status=_FakeStatus("failed"), error="boom")
                ],
            )

    monkeypatch.setattr("mini_agent.workflow.store.WorkflowStore", FakeWorkflowStore)
    monkeypatch.setattr("mini_agent.workflow.runner.WorkflowRunner", FakeRunner)

    try:
        pd_mod.suggest_problems(object(), tmp_path, {}, {}, [])
        assert False, "应该抛出 ProblemDiscoveryError"
    except pd_mod.ProblemDiscoveryError:
        pass


# ── adopt_problem_suggestion() / withdraw_confirmed_problem_suggestion() ──


def test_adopt_and_withdraw_problem_suggestion(tmp_path):
    data_dir = tmp_path / "data"
    _make_sim(data_dir, "sim1")

    manifest = pd_mod.adopt_problem_suggestion(
        data_dir, "sim1",
        category="observed",
        symptom="资金不足，无法招聘核心工程师",
        blocked_goal="在 12 个月内完成产品原型",
        missing_capabilities=["种子轮融资"],
    )
    confirmed = manifest.settings["confirmed_problem_suggestions"]
    assert len(confirmed) == 1
    assert confirmed[0]["category"] == "observed"
    assert confirmed[0]["symptom"] == "资金不足，无法招聘核心工程师"
    assert confirmed[0]["blocked_goal"] == "在 12 个月内完成产品原型"
    assert confirmed[0]["missing_capabilities"] == ["种子轮融资"]
    suggestion_id = confirmed[0]["id"]
    assert suggestion_id

    # 落盘后重新读取也应该看到。
    store = SimStore.for_root(data_dir, "sim1")
    reloaded = store.load_manifest()
    assert len(reloaded.settings["confirmed_problem_suggestions"]) == 1

    # 撤回后应该被移除。
    manifest2 = pd_mod.withdraw_confirmed_problem_suggestion(data_dir, "sim1", suggestion_id)
    assert manifest2.settings["confirmed_problem_suggestions"] == []

    # 撤回一个不存在的 id 应该幂等，不报错。
    manifest3 = pd_mod.withdraw_confirmed_problem_suggestion(data_dir, "sim1", "not_exists")
    assert manifest3.settings["confirmed_problem_suggestions"] == []


def test_adopt_problem_suggestion_normalizes_unknown_category(tmp_path):
    data_dir = tmp_path / "data"
    _make_sim(data_dir, "sim1")
    manifest = pd_mod.adopt_problem_suggestion(
        data_dir, "sim1", category="not_a_real_category", symptom="s",
    )
    assert manifest.settings["confirmed_problem_suggestions"][0]["category"] == "observed"


# ── _format_confirmed_problem_suggestions() ─────────────────────────


# ── _safe_auto_scan_problems()（第十轮批次一）────────────────────────


class _FakeStoreForAutoScan:
    def __init__(self, history):
        self._history = history
        self.load_history_calls = []

    def load_history(self, branch):
        self.load_history_calls.append(branch)
        return self._history


def _make_manifest_ns(sim_id="sim1", settings=None):
    return SimpleNamespace(sim_id=sim_id, settings=dict(settings or {}))


def test_auto_scan_noop_when_interval_zero_or_unset(monkeypatch):
    calls = []
    monkeypatch.setattr(
        pd_mod, "suggest_problems", lambda *a, **k: calls.append(1) or {"observed": [], "latent": []}
    )
    manifest = _make_manifest_ns(settings={})
    next_state = SimpleNamespace(step=3, vars={})
    pd_mod._safe_auto_scan_problems(
        object(), Path("/tmp/ws"), _FakeStoreForAutoScan([]), manifest,
        branch="main", next_state=next_state, auto_confirm=False,
    )
    assert calls == []
    assert "last_auto_problem_scan" not in manifest.settings


def test_auto_scan_noop_when_step_not_multiple_of_interval(monkeypatch):
    calls = []
    monkeypatch.setattr(
        pd_mod, "suggest_problems", lambda *a, **k: calls.append(1) or {"observed": [], "latent": []}
    )
    manifest = _make_manifest_ns(settings={"problem_discovery_auto_scan_interval": 3})
    next_state = SimpleNamespace(step=4, vars={})
    pd_mod._safe_auto_scan_problems(
        object(), Path("/tmp/ws"), _FakeStoreForAutoScan([]), manifest,
        branch="main", next_state=next_state, auto_confirm=False,
    )
    assert calls == []
    assert "last_auto_problem_scan" not in manifest.settings


def test_auto_scan_manual_mode_records_result_but_does_not_confirm(monkeypatch):
    fake_result = {
        "observed": [{"symptom": "资金不足", "blocked_goal": "招聘", "missing_capabilities": []}],
        "latent": [],
    }
    monkeypatch.setattr(pd_mod, "suggest_problems", lambda *a, **k: fake_result)
    manifest = _make_manifest_ns(settings={"problem_discovery_auto_scan_interval": 3})
    fake_store = _FakeStoreForAutoScan([SimpleNamespace(step=1, summary="s", narrative="n")])
    next_state = SimpleNamespace(step=3, vars={"cash": 1})
    pd_mod._safe_auto_scan_problems(
        object(), Path("/tmp/ws"), fake_store, manifest,
        branch="main", next_state=next_state, auto_confirm=False,
    )
    assert fake_store.load_history_calls == ["main"]
    recorded = manifest.settings["last_auto_problem_scan"]
    assert recorded["step"] == 3
    assert recorded["source"] == ["sim1", "main"]
    assert recorded["suggestions"] == fake_result
    # 手动挡：只记录，不自动写入 confirmed_problem_suggestions。
    assert "confirmed_problem_suggestions" not in manifest.settings


def test_auto_scan_autopilot_mode_auto_confirms(monkeypatch):
    fake_result = {
        "observed": [{"symptom": "资金不足", "blocked_goal": "招聘", "missing_capabilities": ["融资"]}],
        "latent": [{"symptom": "获客成本上升风险", "blocked_goal": "", "missing_capabilities": []}],
    }
    monkeypatch.setattr(pd_mod, "suggest_problems", lambda *a, **k: fake_result)
    manifest = _make_manifest_ns(settings={"problem_discovery_auto_scan_interval": 2})
    next_state = SimpleNamespace(step=4, vars={})
    pd_mod._safe_auto_scan_problems(
        object(), Path("/tmp/ws"), _FakeStoreForAutoScan([]), manifest,
        branch="main", next_state=next_state, auto_confirm=True,
    )
    confirmed = manifest.settings["confirmed_problem_suggestions"]
    assert len(confirmed) == 2
    assert {c["category"] for c in confirmed} == {"observed", "latent"}
    assert all(c["auto_confirmed"] is True for c in confirmed)
    assert all(c["id"] for c in confirmed)


def test_auto_scan_swallows_exceptions(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(pd_mod, "suggest_problems", _boom)
    manifest = _make_manifest_ns(settings={"problem_discovery_auto_scan_interval": 1})
    next_state = SimpleNamespace(step=1, vars={})
    # 不应该抛出异常。
    pd_mod._safe_auto_scan_problems(
        object(), Path("/tmp/ws"), _FakeStoreForAutoScan([]), manifest,
        branch="main", next_state=next_state, auto_confirm=True,
    )
    assert "last_auto_problem_scan" not in manifest.settings
    assert "confirmed_problem_suggestions" not in manifest.settings


def test_format_confirmed_problem_suggestions():
    assert pd_mod._format_confirmed_problem_suggestions(None) == ""
    assert pd_mod._format_confirmed_problem_suggestions([]) == ""
    formatted = pd_mod._format_confirmed_problem_suggestions(
        [
            {"category": "observed", "symptom": "资金不足", "blocked_goal": "完成原型"},
            {"category": "latent", "symptom": "获客成本上升风险"},
            {"category": "observed", "symptom": ""},  # 空 symptom 应被跳过
            "不是字典，应该被跳过",
        ]
    )
    assert formatted == "- [observed] 资金不足（挡住：完成原型）\n- [latent] 获客成本上升风险"
