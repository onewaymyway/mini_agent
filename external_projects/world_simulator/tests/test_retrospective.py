"""tests/test_retrospective.py — 阶段三十二（4.6 节）模拟复盘 / 经验
教训总结单元测试。

同 `test_spec_and_engine.py` 的一贯做法：不真的调用 LLM，用
monkeypatch 打桩 `WorkflowStore`/`WorkflowRunner`，只验证"素材收集是
否正确" + "workflow 结果如何被解析回 RetrospectiveRecord 并落盘（追加
写入、不覆盖历史版本）"这条链路本身是对的。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import world_simulator.retrospective as retro
from world_simulator.engine.materialize import materialize_simulation
from world_simulator.state_model import ChoiceOption


class _FakeStatus:
    def __init__(self, value: str) -> None:
        self.value = value


def _write_result_file(tmp_path: Path, name: str, payload: dict) -> str:
    p = tmp_path / name
    p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return str(p)


def _make_sim_with_two_steps(data_dir: Path) -> str:
    """创建一个有两步历史的实例，供复盘测试使用。"""
    manifest = materialize_simulation(
        data_dir,
        template="life_sim",
        intent="模拟一个应届生的三年",
        title="应届生三年",
        summary="刚毕业，正在找工作",
        vars={"age": 22, "cash": 1000},
        options=[ChoiceOption(id="a", label="读研", description="继续深造")],
        settings={"objectives": [{"label": "现金", "field": "cash"}]},
    )
    from world_simulator.store import SimStore
    from world_simulator.state_model import SimState

    store = SimStore.for_root(data_dir, manifest.sim_id)
    history = store.load_history("main")
    history.append(
        SimState(
            step=1,
            summary="选择了先工作",
            narrative="找到了一份工作",
            vars={"age": 22, "cash": 1500},
            chosen_option_id="a",
            chosen_by="user",
            causal_links=[
                {"driver": "找到工作", "affected_fields": ["cash"], "effect": "现金增加"}
            ],
        )
    )
    from mini_agent.utils.atomic_write import atomic_write_jsonl

    atomic_write_jsonl(store.state_history_path("main"), [s.to_dict() for s in history])
    return manifest.sim_id


def test_generate_retrospective_requires_existing_history(tmp_path):
    with pytest.raises(retro.RetrospectiveError):
        retro.generate_retrospective(
            object(), tmp_path, tmp_path, "does_not_exist",
        )


def test_generate_retrospective_end_to_end_and_appends_history(tmp_path, monkeypatch):
    sim_id = _make_sim_with_two_steps(tmp_path)
    workspace_root = tmp_path / "workspace"
    (workspace_root / "workflows").mkdir(parents=True)

    captured_inputs = {}

    class FakeWorkflowStore:
        def __init__(self, root):
            pass

        def load(self, name):
            assert name == "retrospective"
            return SimpleNamespace(steps=[SimpleNamespace(id="retrospective")])

    class FakeRunner:
        def __init__(self, cfg):
            pass

        def run(self, wf, inputs):
            captured_inputs.update(inputs)
            history = json.loads(inputs["state_history_json"])
            assert len(history) == 2
            assert history[1]["chosen_option_id"] == "a"
            result_file = _write_result_file(
                tmp_path,
                "retro_result.json",
                {
                    "turning_points": [
                        {"step": 1, "chosen_option": "a", "why": "决定先工作"}
                    ],
                    "what_went_well": [
                        {"point": "现金增长", "evidence": "第1步 cash 从1000增至1500"}
                    ],
                    "what_to_reflect_on": [],
                    "lessons": [{"lesson": "先工作能积累现金", "source": "turning_point 1"}],
                    "caveats": ["这是基于这次模拟内部记录的总结"],
                },
            )
            return SimpleNamespace(
                status="done",
                step_results=[
                    SimpleNamespace(step_id="retrospective", status=_FakeStatus("done"), result_file=result_file)
                ],
            )

    monkeypatch.setattr("mini_agent.workflow.store.WorkflowStore", FakeWorkflowStore)
    monkeypatch.setattr("mini_agent.workflow.runner.WorkflowRunner", FakeRunner)

    record = retro.generate_retrospective(
        object(), workspace_root, tmp_path, sim_id,
    )

    assert record.branch == "main"
    assert record.up_to_step == 1
    assert record.report.turning_points[0]["chosen_option"] == "a"
    assert record.report.lessons[0]["lesson"] == "先工作能积累现金"
    assert record.report.caveats == ["这是基于这次模拟内部记录的总结"]

    # 素材收集：归因摘要应该带上了 objectives 里声明的 "现金" 指标
    attribution_items = json.loads(captured_inputs["attribution_json"])
    assert attribution_items and attribution_items[0]["objective"] == "现金"

    # 落盘：追加写入，不覆盖历史版本
    reloaded = retro.load_for_branch(tmp_path, sim_id, "main")
    assert len(reloaded) == 1
    assert reloaded[0].id == record.id


def test_generate_retrospective_backfills_missing_caveats(tmp_path, monkeypatch):
    sim_id = _make_sim_with_two_steps(tmp_path)
    workspace_root = tmp_path / "workspace"
    (workspace_root / "workflows").mkdir(parents=True)

    class FakeWorkflowStore:
        def __init__(self, root):
            pass

        def load(self, name):
            return SimpleNamespace(steps=[SimpleNamespace(id="retrospective")])

    class FakeRunner:
        def __init__(self, cfg):
            pass

        def run(self, wf, inputs):
            result_file = _write_result_file(
                tmp_path,
                "retro_result_2.json",
                {
                    "turning_points": [],
                    "what_went_well": [],
                    "what_to_reflect_on": [],
                    "lessons": [],
                    "caveats": [],
                },
            )
            return SimpleNamespace(
                status="done",
                step_results=[
                    SimpleNamespace(step_id="retrospective", status=_FakeStatus("done"), result_file=result_file)
                ],
            )

    monkeypatch.setattr("mini_agent.workflow.store.WorkflowStore", FakeWorkflowStore)
    monkeypatch.setattr("mini_agent.workflow.runner.WorkflowRunner", FakeRunner)

    record = retro.generate_retrospective(object(), workspace_root, tmp_path, sim_id)
    assert len(record.report.caveats) == 1
    assert "不是对你个人能力或决策方式的心理分析" in record.report.caveats[0]


def test_generate_retrospective_multiple_calls_keep_history_not_overwrite(tmp_path, monkeypatch):
    sim_id = _make_sim_with_two_steps(tmp_path)
    workspace_root = tmp_path / "workspace"
    (workspace_root / "workflows").mkdir(parents=True)

    class FakeWorkflowStore:
        def __init__(self, root):
            pass

        def load(self, name):
            return SimpleNamespace(steps=[SimpleNamespace(id="retrospective")])

    call_count = {"n": 0}

    class FakeRunner:
        def __init__(self, cfg):
            pass

        def run(self, wf, inputs):
            call_count["n"] += 1
            result_file = _write_result_file(
                tmp_path,
                f"retro_result_{call_count['n']}.json",
                {
                    "turning_points": [], "what_went_well": [], "what_to_reflect_on": [],
                    "lessons": [], "caveats": [f"第{call_count['n']}次复盘"],
                },
            )
            return SimpleNamespace(
                status="done",
                step_results=[
                    SimpleNamespace(step_id="retrospective", status=_FakeStatus("done"), result_file=result_file)
                ],
            )

    monkeypatch.setattr("mini_agent.workflow.store.WorkflowStore", FakeWorkflowStore)
    monkeypatch.setattr("mini_agent.workflow.runner.WorkflowRunner", FakeRunner)

    retro.generate_retrospective(object(), workspace_root, tmp_path, sim_id)
    retro.generate_retrospective(object(), workspace_root, tmp_path, sim_id)

    reloaded = retro.load_for_branch(tmp_path, sim_id, "main")
    assert len(reloaded) == 2
    # 按生成时间倒序，最新的在前——两次调用 caveats 都应该完整保留
    assert {r.report.caveats[0] for r in reloaded} == {"第1次复盘", "第2次复盘"}
