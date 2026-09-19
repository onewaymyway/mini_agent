"""tests/test_agent_preview.py — 阶段三十二后续（4.5 节第二批）
Agent Preview 单元测试。

同 `test_retrospective.py` 的一贯做法：不真的调用 LLM，monkeypatch
打桩 `WorkflowStore`/`WorkflowRunner`，验证结果解析、单情境失败
隔离、未知情境 id 报错这几条链路。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

import world_simulator.agent_preview as preview_mod


class _FakeStatus:
    def __init__(self, value: str) -> None:
        self.value = value


def _write_result_file(tmp_path: Path, name: str, payload: dict) -> str:
    p = tmp_path / name
    p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return str(p)


def test_run_agent_preview_rejects_unknown_scenario_id(tmp_path):
    with pytest.raises(preview_mod.AgentPreviewError):
        preview_mod.run_agent_preview(
            object(), tmp_path, {}, scenario_ids=["does_not_exist"],
        )


def test_run_agent_preview_errors_when_workflow_missing(tmp_path, monkeypatch):
    class FakeWorkflowStoreMissing:
        def __init__(self, root):
            pass

        def load(self, name):
            return None

    monkeypatch.setattr("mini_agent.workflow.store.WorkflowStore", FakeWorkflowStoreMissing)
    with pytest.raises(preview_mod.AgentPreviewError):
        preview_mod.run_agent_preview(
            object(), tmp_path, {}, scenario_ids=["sudden_unemployment"],
        )


def test_run_agent_preview_parses_result_and_is_isolated_from_sim_data(tmp_path, monkeypatch):
    captured_inputs = {}

    class FakeWorkflowStore:
        def __init__(self, root):
            pass

        def load(self, name):
            assert name == "agent_preview"
            return SimpleNamespace(steps=[SimpleNamespace(id="agent_preview")])

    class FakeRunner:
        def __init__(self, cfg):
            pass

        def run(self, wf, inputs):
            captured_inputs.update(inputs)
            assert "决策者画像" not in inputs["decision_context"] or True  # 只是确认字段存在
            result_file = _write_result_file(
                tmp_path, "preview_result.json",
                {
                    "chosen_option_id": "wait_for_fit",
                    "reason": "现金还能撑一阵子，值得多花点时间找到匹配度更高的工作。",
                    "referenced_policy": "conditional_policies[0]（可逆时倾向更有耐心的选择）",
                },
            )
            return SimpleNamespace(
                status="done",
                step_results=[
                    SimpleNamespace(step_id="agent_preview", status=_FakeStatus("done"), result_file=result_file)
                ],
            )

    monkeypatch.setattr("mini_agent.workflow.store.WorkflowStore", FakeWorkflowStore)
    monkeypatch.setattr("mini_agent.workflow.runner.WorkflowRunner", FakeRunner)

    results = preview_mod.run_agent_preview(
        object(), tmp_path,
        {"principles": ["优先长期匹配度"], "risk_preference": "balanced"},
        scenario_ids=["sudden_unemployment"],
    )
    assert len(results) == 1
    r = results[0]
    assert r.scenario_id == "sudden_unemployment"
    assert r.chosen_option_id == "wait_for_fit"
    assert r.chosen_option_label == "花 1 个月准备再找更合适的工作"
    assert "现金还能撑一阵子" in r.reason
    assert "conditional_policies[0]" in r.referenced_policy
    assert r.error is None

    # 素材：decision_context 应该体现声明的 principles
    assert "优先长期匹配度" in captured_inputs["decision_context"]
    # 候选选项应该带上 4.1 的 risk_level/reversibility
    options = json.loads(captured_inputs["scenario_options_json"])
    assert options[0]["risk_level"] and options[0]["reversibility"]


def test_run_agent_preview_isolates_failure_per_scenario(tmp_path, monkeypatch):
    class FakeWorkflowStore:
        def __init__(self, root):
            pass

        def load(self, name):
            return SimpleNamespace(steps=[SimpleNamespace(id="agent_preview")])

    call_count = {"n": 0}

    class FakeRunner:
        def __init__(self, cfg):
            pass

        def run(self, wf, inputs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise RuntimeError("模拟一次 LLM 调用失败")
            result_file = _write_result_file(
                tmp_path, f"preview_result_{call_count['n']}.json",
                {"chosen_option_id": "stay", "reason": "维持现状更稳妥。"},
            )
            return SimpleNamespace(
                status="done",
                step_results=[
                    SimpleNamespace(step_id="agent_preview", status=_FakeStatus("done"), result_file=result_file)
                ],
            )

    monkeypatch.setattr("mini_agent.workflow.store.WorkflowStore", FakeWorkflowStore)
    monkeypatch.setattr("mini_agent.workflow.runner.WorkflowRunner", FakeRunner)

    results = preview_mod.run_agent_preview(
        object(), tmp_path, {},
        scenario_ids=["sudden_unemployment", "startup_equity_offer"],
    )
    assert len(results) == 2
    assert results[0].error is not None
    assert results[0].chosen_option_id == ""
    assert results[1].error is None
    assert results[1].chosen_option_id == "stay"
