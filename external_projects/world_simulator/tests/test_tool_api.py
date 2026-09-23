"""tests/test_tool_api.py — `world_simulator/tool_api.py`（外部服务的
Layer1 薄封装层）单测。

覆盖两类场景：
1. 不需要调用 LLM 的函数（`list_simulations`/`get_simulation`/
   `set_status`/`set_pilot_config`/`update_settings`/
   `rename_simulation`/`delete_simulation`/结构性变化与因果线建议/
   `export_html`）——用真实 `materialize_simulation()` 落盘的实例做
   fixture，验证返回值结构、以及找不到 `sim_id` 时统一映射成
   `error.type == "not_found"`。
2. 需要调用 LLM 的函数（`create_simulation`/`advance_simulation`/
   `fast_forward_simulation`）——参数校验路径不需要 mock；happy
   path/引擎异常路径复用 `test_spec_and_engine.py` 里已经验证过的
   `FakeRunner`/`FakeStore` mock 手法，重点验证的是"这一层有没有把
   `SimEngineError` 正确转换成 `{"ok": False, "error": {"type":
   "engine_error", ...}}`"，不重复测底层引擎逻辑本身。

**每个用例都断言 `tool_api.py` 承诺的统一契约**：`ok=True` 时一定有
`data`，`ok=False` 时一定有 `error.type`/`error.message`，不直接把
异常抛出来——这是这一层存在的全部意义，所以这个契约本身值得每个
用例都显式断言一次，而不是只测"这次调用对不对"。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_spec_and_engine import _FakeStatus, _FakeStep, _FakeWorkflow, _write_result_file  # noqa: E402

import world_simulator.engine as engine_mod  # noqa: E402
from world_simulator import tool_api  # noqa: E402


def _assert_ok_contract(result):
    assert result["ok"] is True
    assert "data" in result
    assert "error" not in result


def _assert_err_contract(result, error_type: str):
    assert result["ok"] is False
    assert result["error"]["type"] == error_type
    assert result["error"]["message"]


@pytest.fixture(autouse=True)
def _isolate_tool_api(tmp_path, monkeypatch):
    """每个用例独立的 `data_dir`，避免相互污染；同时重置 `_cfg_cache`
    （模块级缓存），确保 `monkeypatch` 对 `load_llm_cfg` 的替换在每个
    用例里都生效，不被上一个用例缓存下来的值挡住。"""
    monkeypatch.setattr(tool_api, "_DATA_DIR", tmp_path)
    monkeypatch.setattr(tool_api, "_cfg_cache", None)
    yield


def _make_manifest(tmp_path, sim_id="sim_a", **overrides):
    kwargs = dict(
        template="life_sim", intent="意图", title="标题", summary="摘要",
        vars={"age": 22}, options=[],
    )
    kwargs.update(overrides)
    return engine_mod.materialize_simulation(tmp_path, **kwargs)


# ── 不需要 LLM 的路径 ────────────────────────────────────────────────

def test_list_simulations_returns_compact_summary(tmp_path):
    _make_manifest(tmp_path, sim_id="sim_a", title="第一个")
    result = tool_api.list_simulations()
    _assert_ok_contract(result)
    sims = result["data"]["simulations"]
    assert len(sims) == 1
    # 精简摘要不应该带上完整 `settings`/`autopilot`，只给列表页需要的字段
    assert set(sims[0].keys()) == {
        "sim_id", "title", "template", "intent", "status",
        "pilot_mode", "branch", "current_step", "created_at", "updated_at",
    }


def test_get_simulation_not_found_maps_to_not_found_error():
    result = tool_api.get_simulation("does_not_exist")
    _assert_err_contract(result, "not_found")


def test_get_simulation_returns_manifest_and_current_state(tmp_path):
    manifest = _make_manifest(tmp_path)
    result = tool_api.get_simulation(manifest.sim_id)
    _assert_ok_contract(result)
    assert result["data"]["manifest"]["sim_id"] == manifest.sim_id
    assert result["data"]["current_state"]["step"] == 0
    assert "history" not in result["data"]  # include_history 默认 False


def test_get_simulation_includes_history_when_requested(tmp_path):
    manifest = _make_manifest(tmp_path)
    result = tool_api.get_simulation(manifest.sim_id, include_history=True)
    _assert_ok_contract(result)
    assert "history" in result["data"]
    assert len(result["data"]["history"]) == 1


def test_get_simulation_empty_sim_id_is_validation_error():
    result = tool_api.get_simulation("")
    _assert_err_contract(result, "validation_error")


def test_set_status_updates_manifest(tmp_path):
    manifest = _make_manifest(tmp_path)
    result = tool_api.set_status(manifest.sim_id, "paused")
    _assert_ok_contract(result)
    assert result["data"]["status"] == "paused"


def test_set_status_invalid_value_maps_to_engine_error(tmp_path):
    manifest = _make_manifest(tmp_path)
    result = tool_api.set_status(manifest.sim_id, "not_a_status")
    _assert_err_contract(result, "engine_error")


def test_set_pilot_config_rejects_invalid_pilot_mode_before_touching_engine(tmp_path):
    manifest = _make_manifest(tmp_path)
    result = tool_api.set_pilot_config(manifest.sim_id, pilot_mode="turbo")
    _assert_err_contract(result, "validation_error")


def test_set_pilot_config_success(tmp_path):
    manifest = _make_manifest(tmp_path)
    result = tool_api.set_pilot_config(manifest.sim_id, pilot_mode="autopilot", autopilot={"risk": "low"})
    _assert_ok_contract(result)
    assert result["data"]["pilot_mode"] == "autopilot"


def test_update_settings_merges_into_manifest(tmp_path):
    manifest = _make_manifest(tmp_path)
    result = tool_api.update_settings(manifest.sim_id, {"options_count": 3})
    _assert_ok_contract(result)
    assert result["data"]["settings"]["options_count"] == 3


def test_update_settings_rejects_non_dict_updates(tmp_path):
    manifest = _make_manifest(tmp_path)
    result = tool_api.update_settings(manifest.sim_id, ["not", "a", "dict"])  # type: ignore[arg-type]
    _assert_err_contract(result, "validation_error")


def test_rename_simulation(tmp_path):
    manifest = _make_manifest(tmp_path)
    result = tool_api.rename_simulation(manifest.sim_id, "新标题")
    _assert_ok_contract(result)
    assert result["data"]["title"] == "新标题"


def test_rename_simulation_empty_title_is_validation_error(tmp_path):
    manifest = _make_manifest(tmp_path)
    result = tool_api.rename_simulation(manifest.sim_id, "  ")
    _assert_err_contract(result, "validation_error")


def test_delete_simulation_then_not_found(tmp_path):
    manifest = _make_manifest(tmp_path)
    result = tool_api.delete_simulation(manifest.sim_id)
    _assert_ok_contract(result)
    assert result["data"]["deleted"] is True
    result2 = tool_api.get_simulation(manifest.sim_id)
    _assert_err_contract(result2, "not_found")


def test_reject_causal_line_suggestion_is_idempotent(tmp_path):
    manifest = _make_manifest(tmp_path)
    # 不存在的 suggestion_id：引擎侧本身是幂等静默成功（见 structural_change.py
    # docstring），这一层原样透传，不额外报错。
    result = tool_api.reject_causal_line_suggestion(manifest.sim_id, "no_such_suggestion")
    _assert_ok_contract(result)


def test_accept_causal_line_suggestion_not_found_suggestion_maps_to_engine_error(tmp_path):
    manifest = _make_manifest(tmp_path)
    result = tool_api.accept_causal_line_suggestion(manifest.sim_id, "no_such_suggestion")
    _assert_err_contract(result, "engine_error")


def test_export_html_returns_html_string(tmp_path):
    manifest = _make_manifest(tmp_path)
    result = tool_api.export_html(manifest.sim_id)
    _assert_ok_contract(result)
    assert "<html" in result["data"]["html"].lower()


def test_export_html_not_found(tmp_path):
    result = tool_api.export_html("does_not_exist")
    _assert_err_contract(result, "not_found")


# ── 需要 LLM 的路径：参数校验（不 mock）──────────────────────────────

def test_create_simulation_empty_intent_is_validation_error():
    result = tool_api.create_simulation(template="life_sim", intent="   ")
    _assert_err_contract(result, "validation_error")


def test_advance_simulation_empty_sim_id_is_validation_error():
    result = tool_api.advance_simulation("")
    _assert_err_contract(result, "validation_error")


def test_fast_forward_simulation_rejects_non_positive_max_steps(tmp_path):
    manifest = _make_manifest(tmp_path)
    result = tool_api.fast_forward_simulation(manifest.sim_id, max_steps=0)
    _assert_err_contract(result, "validation_error")


def test_create_simulation_without_mini_agent_maps_to_environment_error(monkeypatch):
    def _raise_import_error():
        raise ImportError("mini_agent 未安装")

    monkeypatch.setattr(tool_api, "load_llm_cfg", lambda *a, **k: _raise_import_error())
    result = tool_api.create_simulation(template="life_sim", intent="随便一个意图")
    _assert_err_contract(result, "environment_error")


# ── 需要 LLM 的路径：happy path / 引擎异常，复用 FakeRunner 手法 ────

def test_advance_simulation_success_returns_serialized_state(tmp_path, monkeypatch):
    manifest = _make_manifest(tmp_path)
    step_step = _FakeStep("step")

    class FakeStoreForAdvance:
        def __init__(self, root):
            pass

        def load(self, name):
            return _FakeWorkflow([step_step])

    class FakeRunnerForAdvance:
        def __init__(self, cfg):
            pass

        def run(self, wf, inputs):
            result_file = _write_result_file(
                tmp_path, "advance_ok.json",
                {"next_summary": "推进成功", "narrative": "n", "next_vars": {"age": 23}, "options": []},
            )
            return SimpleNamespace(status="done", step_results=[
                SimpleNamespace(step_id="step", status=_FakeStatus("done"), result_file=result_file)
            ])

    monkeypatch.setattr("mini_agent.workflow.store.WorkflowStore", FakeStoreForAdvance)
    monkeypatch.setattr("mini_agent.workflow.runner.WorkflowRunner", FakeRunnerForAdvance)
    monkeypatch.setattr(tool_api, "load_llm_cfg", lambda *a, **k: object())

    result = tool_api.advance_simulation(manifest.sim_id)
    _assert_ok_contract(result)
    assert result["data"]["summary"] == "推进成功"
    assert result["data"]["vars"]["age"] == 23


def test_advance_simulation_engine_failure_maps_to_engine_error(tmp_path, monkeypatch):
    manifest = _make_manifest(tmp_path)
    step_step = _FakeStep("step")

    class FakeStoreForAdvance:
        def __init__(self, root):
            pass

        def load(self, name):
            return _FakeWorkflow([step_step])

    class FakeRunnerForAdvance:
        def __init__(self, cfg):
            pass

        def run(self, wf, inputs):
            return SimpleNamespace(status="failed", step_results=[
                SimpleNamespace(step_id="step", status=_FakeStatus("failed"), result_file=None, error="boom")
            ])

    monkeypatch.setattr("mini_agent.workflow.store.WorkflowStore", FakeStoreForAdvance)
    monkeypatch.setattr("mini_agent.workflow.runner.WorkflowRunner", FakeRunnerForAdvance)
    monkeypatch.setattr(tool_api, "load_llm_cfg", lambda *a, **k: object())

    advance_mod = sys.modules["world_simulator.engine.advance"]
    monkeypatch.setattr(advance_mod.time, "sleep", lambda *_a, **_k: None)

    result = tool_api.advance_simulation(manifest.sim_id)
    _assert_err_contract(result, "engine_error")
