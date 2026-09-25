"""tests/test_capability_discovery.py — Capability Discovery Engine
单元测试（第二十一轮，用户反馈"模拟很久了，能力成熟度时间线还是空
的"，用户明确选择"完整对称方案"）。写法仿照 `test_problem_discovery.
py` 的既有手法，因为 `capability_discovery.py` 本来就是它的姐妹
实现，架构逐项对称——测试同样逐项对称，不重新发明写法。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from world_simulator import capability_discovery as cd_mod
from world_simulator.state_model import SimManifest
from world_simulator.store import SimStore, now_iso


class _FakeStatus:
    def __init__(self, value: str) -> None:
        self.value = value


def _agent_output(payload: dict) -> str:
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


# ── suggest_capabilities() ───────────────────────────────────────────


def test_suggest_capabilities_assembles_input_and_parses_result(tmp_path, monkeypatch):
    captured_inputs = {}

    class FakeWorkflowStore:
        def __init__(self, root):
            pass

        def load(self, name):
            assert name == "capability_discovery"
            return SimpleNamespace(steps=[SimpleNamespace(id="capability_discovery")])

    class FakeRunner:
        def __init__(self, cfg):
            pass

        def run(self, wf, inputs):
            captured_inputs.update(inputs)
            output = _agent_output(
                {
                    "suggestions": [
                        {
                            "capability": "能够自动分析潜在客户的付费意愿",
                            "enables": ["更精准的销售话术"],
                            "limitations": ["无法替代真实用户访谈"],
                            "maturity_stage": "developer",
                            "capability_kind": "technology",
                        }
                    ]
                }
            )
            return SimpleNamespace(
                status="done",
                step_results=[
                    SimpleNamespace(step_id="capability_discovery", status=_FakeStatus("done"), output=output)
                ],
            )

    monkeypatch.setattr("mini_agent.workflow.store.WorkflowStore", FakeWorkflowStore)
    monkeypatch.setattr("mini_agent.workflow.runner.WorkflowRunner", FakeRunner)

    history = [
        SimpleNamespace(step=1, summary="s1", narrative="n1"),
        SimpleNamespace(step=2, summary="s2", narrative="n2"),
    ]
    result = cd_mod.suggest_capabilities(
        object(), tmp_path, {"cash": 100}, history, already_recorded=["已有能力A"],
    )

    assert json.loads(captured_inputs["current_vars_json"]) == {"cash": 100}
    assert json.loads(captured_inputs["recent_history_json"]) == [
        {"step": 1, "summary": "s1", "narrative": "n1"},
        {"step": 2, "summary": "s2", "narrative": "n2"},
    ]
    assert json.loads(captured_inputs["already_recorded_json"]) == ["已有能力A"]

    assert result == [
        {
            "capability": "能够自动分析潜在客户的付费意愿",
            "enables": ["更精准的销售话术"],
            "limitations": ["无法替代真实用户访谈"],
            "maturity_stage": "developer",
            "capability_kind": "technology",
        }
    ]


def test_suggest_capabilities_normalizes_invalid_enum_values_to_none(monkeypatch):
    class FakeWorkflowStore:
        def __init__(self, root):
            pass

        def load(self, name):
            return SimpleNamespace(steps=[SimpleNamespace(id="capability_discovery")])

    class FakeRunner:
        def __init__(self, cfg):
            pass

        def run(self, wf, inputs):
            output = _agent_output(
                {
                    "suggestions": [
                        {
                            "capability": "能力X",
                            "maturity_stage": "not_a_real_stage",
                            "capability_kind": "not_a_real_kind",
                        }
                    ]
                }
            )
            return SimpleNamespace(
                status="done",
                step_results=[
                    SimpleNamespace(step_id="capability_discovery", status=_FakeStatus("done"), output=output)
                ],
            )

    monkeypatch.setattr("mini_agent.workflow.store.WorkflowStore", FakeWorkflowStore)
    monkeypatch.setattr("mini_agent.workflow.runner.WorkflowRunner", FakeRunner)

    result = cd_mod.suggest_capabilities(object(), Path("/tmp/ws"), {}, [])
    assert result[0]["maturity_stage"] is None
    assert result[0]["capability_kind"] is None


def test_suggest_capabilities_skips_items_without_capability(monkeypatch):
    class FakeWorkflowStore:
        def __init__(self, root):
            pass

        def load(self, name):
            return SimpleNamespace(steps=[SimpleNamespace(id="capability_discovery")])

    class FakeRunner:
        def __init__(self, cfg):
            pass

        def run(self, wf, inputs):
            output = _agent_output({"suggestions": [{"capability": ""}, {"enables": ["x"]}]})
            return SimpleNamespace(
                status="done",
                step_results=[
                    SimpleNamespace(step_id="capability_discovery", status=_FakeStatus("done"), output=output)
                ],
            )

    monkeypatch.setattr("mini_agent.workflow.store.WorkflowStore", FakeWorkflowStore)
    monkeypatch.setattr("mini_agent.workflow.runner.WorkflowRunner", FakeRunner)

    result = cd_mod.suggest_capabilities(object(), Path("/tmp/ws"), {}, [])
    assert result == []


def test_suggest_capabilities_raises_when_workflow_missing(monkeypatch):
    class FakeWorkflowStore:
        def __init__(self, root):
            pass

        def load(self, name):
            return None

    monkeypatch.setattr("mini_agent.workflow.store.WorkflowStore", FakeWorkflowStore)

    try:
        cd_mod.suggest_capabilities(object(), Path("/tmp/ws"), {}, [])
        assert False, "应该抛出 CapabilityDiscoveryError"
    except cd_mod.CapabilityDiscoveryError:
        pass


# ── adopt_capability_suggestion() / withdraw_confirmed_capability_suggestion() ──


def test_adopt_capability_suggestion_appends_to_settings(tmp_path):
    _make_sim(tmp_path, "sim1")
    manifest = cd_mod.adopt_capability_suggestion(
        tmp_path, "sim1",
        capability="能力X", enables=["用途A"], limitations=["局限B"],
        maturity_stage="expert", capability_kind="technology",
    )
    confirmed = manifest.settings["confirmed_capability_suggestions"]
    assert len(confirmed) == 1
    assert confirmed[0]["capability"] == "能力X"
    assert confirmed[0]["enables"] == ["用途A"]
    assert confirmed[0]["maturity_stage"] == "expert"
    assert confirmed[0]["capability_kind"] == "technology"
    assert confirmed[0]["id"].startswith("confirmed_capability_")


def test_adopt_capability_suggestion_normalizes_invalid_enums(tmp_path):
    _make_sim(tmp_path, "sim1")
    manifest = cd_mod.adopt_capability_suggestion(
        tmp_path, "sim1", capability="能力X",
        maturity_stage="bogus", capability_kind="bogus",
    )
    confirmed = manifest.settings["confirmed_capability_suggestions"][0]
    assert confirmed["maturity_stage"] is None
    assert confirmed["capability_kind"] is None


def test_withdraw_confirmed_capability_suggestion_removes_by_id(tmp_path):
    _make_sim(tmp_path, "sim1")
    cd_mod.adopt_capability_suggestion(tmp_path, "sim1", capability="能力X")
    manifest = SimStore.for_root(tmp_path, "sim1").load_manifest()
    suggestion_id = manifest.settings["confirmed_capability_suggestions"][0]["id"]

    manifest2 = cd_mod.withdraw_confirmed_capability_suggestion(tmp_path, "sim1", suggestion_id)
    assert manifest2.settings["confirmed_capability_suggestions"] == []


def test_withdraw_confirmed_capability_suggestion_is_idempotent(tmp_path):
    _make_sim(tmp_path, "sim1")
    # 不存在的 id：不应该抛异常，静默返回。
    manifest = cd_mod.withdraw_confirmed_capability_suggestion(tmp_path, "sim1", "does_not_exist")
    assert manifest.settings.get("confirmed_capability_suggestions") == []


# ── get_effective_auto_scan_interval() ───────────────────────────────


def test_effective_interval_defaults_when_key_absent():
    assert cd_mod.get_effective_auto_scan_interval({}) == cd_mod.DEFAULT_AUTO_SCAN_INTERVAL
    assert cd_mod.get_effective_auto_scan_interval(None) == cd_mod.DEFAULT_AUTO_SCAN_INTERVAL


def test_effective_interval_respects_explicit_zero():
    assert cd_mod.get_effective_auto_scan_interval({"capability_discovery_auto_scan_interval": 0}) == 0


def test_effective_interval_respects_explicit_positive_value():
    assert cd_mod.get_effective_auto_scan_interval({"capability_discovery_auto_scan_interval": 10}) == 10


def test_effective_interval_falls_back_to_default_on_malformed_value():
    assert (
        cd_mod.get_effective_auto_scan_interval({"capability_discovery_auto_scan_interval": "nope"})
        == cd_mod.DEFAULT_AUTO_SCAN_INTERVAL
    )


# ── _collect_recorded_capability_names() ─────────────────────────────


def test_collect_recorded_capability_names_extracts_across_history():
    history = [
        SimpleNamespace(step=1, capabilities_gained=[{"capability": "A"}, {"capability": "B"}]),
        SimpleNamespace(step=2, capabilities_gained=[]),
        SimpleNamespace(step=3, capabilities_gained=[{"capability": "C"}, {"capability": ""}]),
    ]
    assert cd_mod._collect_recorded_capability_names(history) == ["A", "B", "C"]


def test_collect_recorded_capability_names_handles_missing_field():
    history = [SimpleNamespace(step=1)]  # 没有 capabilities_gained 属性
    assert cd_mod._collect_recorded_capability_names(history) == []


# ── _safe_auto_scan_capabilities() ───────────────────────────────────


class _FakeStoreForAutoScan:
    def __init__(self, history):
        self._history = history
        self.load_history_calls = []

    def load_history(self, branch):
        self.load_history_calls.append(branch)
        return self._history


def _make_manifest_ns(sim_id="sim1", settings=None):
    return SimpleNamespace(sim_id=sim_id, settings=dict(settings or {}))


def test_auto_scan_noop_when_interval_explicitly_zero(monkeypatch):
    calls = []
    monkeypatch.setattr(cd_mod, "suggest_capabilities", lambda *a, **k: calls.append(1) or [])
    manifest = _make_manifest_ns(settings={"capability_discovery_auto_scan_interval": 0})
    next_state = SimpleNamespace(step=5, vars={})
    cd_mod._safe_auto_scan_capabilities(
        object(), Path("/tmp/ws"), _FakeStoreForAutoScan([]), manifest,
        branch="main", next_state=next_state, auto_confirm=False,
    )
    assert calls == []
    assert "last_auto_capability_scan" not in manifest.settings


def test_auto_scan_triggers_by_default_when_unset(monkeypatch):
    calls = []
    monkeypatch.setattr(cd_mod, "suggest_capabilities", lambda *a, **k: calls.append(1) or [])
    manifest = _make_manifest_ns(settings={})
    next_state = SimpleNamespace(step=cd_mod.DEFAULT_AUTO_SCAN_INTERVAL, vars={})
    cd_mod._safe_auto_scan_capabilities(
        object(), Path("/tmp/ws"), _FakeStoreForAutoScan([]), manifest,
        branch="main", next_state=next_state, auto_confirm=False,
    )
    assert calls == [1]
    assert "last_auto_capability_scan" in manifest.settings


def test_auto_scan_noop_when_step_not_multiple_of_interval(monkeypatch):
    calls = []
    monkeypatch.setattr(cd_mod, "suggest_capabilities", lambda *a, **k: calls.append(1) or [])
    manifest = _make_manifest_ns(settings={"capability_discovery_auto_scan_interval": 3})
    next_state = SimpleNamespace(step=4, vars={})
    cd_mod._safe_auto_scan_capabilities(
        object(), Path("/tmp/ws"), _FakeStoreForAutoScan([]), manifest,
        branch="main", next_state=next_state, auto_confirm=False,
    )
    assert calls == []
    assert "last_auto_capability_scan" not in manifest.settings


def test_auto_scan_passes_already_recorded_names_deduped_from_history_and_confirmed(monkeypatch):
    captured = {}

    def _fake_suggest(cfg, workspace_root, vars_, history, *, already_recorded=None, **kw):
        captured["already_recorded"] = already_recorded
        return []

    monkeypatch.setattr(cd_mod, "suggest_capabilities", _fake_suggest)
    manifest = _make_manifest_ns(
        settings={
            "capability_discovery_auto_scan_interval": 1,
            "confirmed_capability_suggestions": [{"capability": "已确认能力"}],
        }
    )
    fake_history = [SimpleNamespace(step=1, capabilities_gained=[{"capability": "历史能力"}])]
    next_state = SimpleNamespace(step=1, vars={})
    cd_mod._safe_auto_scan_capabilities(
        object(), Path("/tmp/ws"), _FakeStoreForAutoScan(fake_history), manifest,
        branch="main", next_state=next_state, auto_confirm=False,
    )
    assert set(captured["already_recorded"]) == {"历史能力", "已确认能力"}


def test_auto_scan_manual_mode_records_result_but_does_not_confirm(monkeypatch):
    fake_result = [{"capability": "新能力", "enables": [], "limitations": [], "maturity_stage": None, "capability_kind": None}]
    monkeypatch.setattr(cd_mod, "suggest_capabilities", lambda *a, **k: fake_result)
    manifest = _make_manifest_ns(settings={"capability_discovery_auto_scan_interval": 3})
    fake_store = _FakeStoreForAutoScan([SimpleNamespace(step=1, summary="s", narrative="n", capabilities_gained=[])])
    next_state = SimpleNamespace(step=3, vars={"cash": 1})
    cd_mod._safe_auto_scan_capabilities(
        object(), Path("/tmp/ws"), fake_store, manifest,
        branch="main", next_state=next_state, auto_confirm=False,
    )
    assert fake_store.load_history_calls == ["main"]
    recorded = manifest.settings["last_auto_capability_scan"]
    assert recorded["step"] == 3
    assert recorded["source"] == ["sim1", "main"]
    assert recorded["suggestions"] == fake_result
    assert "confirmed_capability_suggestions" not in manifest.settings


def test_auto_scan_autopilot_mode_auto_confirms(monkeypatch):
    fake_result = [
        {"capability": "能力A", "enables": ["用途1"], "limitations": [], "maturity_stage": "lab", "capability_kind": "technology"},
        {"capability": "能力B", "enables": [], "limitations": [], "maturity_stage": None, "capability_kind": None},
    ]
    monkeypatch.setattr(cd_mod, "suggest_capabilities", lambda *a, **k: fake_result)
    manifest = _make_manifest_ns(settings={"capability_discovery_auto_scan_interval": 2})
    next_state = SimpleNamespace(step=4, vars={})
    cd_mod._safe_auto_scan_capabilities(
        object(), Path("/tmp/ws"), _FakeStoreForAutoScan([]), manifest,
        branch="main", next_state=next_state, auto_confirm=True,
    )
    confirmed = manifest.settings["confirmed_capability_suggestions"]
    assert len(confirmed) == 2
    assert {c["capability"] for c in confirmed} == {"能力A", "能力B"}
    assert all(c["auto_confirmed"] is True for c in confirmed)
    assert all(c["id"] for c in confirmed)


def test_auto_scan_swallows_exceptions(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(cd_mod, "suggest_capabilities", _boom)
    manifest = _make_manifest_ns(settings={"capability_discovery_auto_scan_interval": 1})
    next_state = SimpleNamespace(step=1, vars={})
    cd_mod._safe_auto_scan_capabilities(
        object(), Path("/tmp/ws"), _FakeStoreForAutoScan([]), manifest,
        branch="main", next_state=next_state, auto_confirm=True,
    )
    assert "last_auto_capability_scan" not in manifest.settings
    assert "confirmed_capability_suggestions" not in manifest.settings


# ── _format_confirmed_capability_suggestions() ───────────────────────


def test_format_confirmed_capability_suggestions():
    assert cd_mod._format_confirmed_capability_suggestions(None) == ""
    assert cd_mod._format_confirmed_capability_suggestions([]) == ""
    formatted = cd_mod._format_confirmed_capability_suggestions(
        [
            {"capability": "能力A", "enables": ["用途1", "用途2"]},
            {"capability": "能力B"},
            {"capability": ""},  # 空 capability 应被跳过
        ]
    )
    lines = formatted.split("\n")
    assert lines == ["- 能力A（可能带来：用途1、用途2）", "- 能力B"]
