"""tests/test_skill_version.py — 第八轮批次六：Branch Engine 版本
记录部分，`SimState.skill_version` 的读取与落盘。

对应 `next_doc/world_simulator_c_category_precision_upgrade_
improvement_plan.md` 第 7 节第 2 条。
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from world_simulator.engine.ids import _read_skill_version
from world_simulator.state_model import SimState


# ---------------------------------------------------------------------------
# _read_skill_version()
# ---------------------------------------------------------------------------


def test_read_skill_version_returns_mtime_of_existing_skill_md(tmp_path):
    skill_dir = tmp_path / "skills" / "life-sim-template"
    skill_dir.mkdir(parents=True)
    skill_md = skill_dir / "SKILL.md"
    skill_md.write_text("# life sim skill", encoding="utf-8")

    version = _read_skill_version(tmp_path, "life_sim")
    assert version  # 非空

    # 应该能解析回一个合法的 ISO 时间字符串，且和文件的真实 mtime 接近。
    parsed = datetime.fromisoformat(version)
    expected = datetime.fromtimestamp(skill_md.stat().st_mtime, tz=timezone.utc)
    assert abs((parsed - expected).total_seconds()) < 1


def test_read_skill_version_returns_empty_string_when_skill_md_missing(tmp_path):
    # tmp_path 下什么都没有，对应的 SKILL.md 自然不存在。
    assert _read_skill_version(tmp_path, "life_sim") == ""


def test_read_skill_version_returns_empty_string_for_unknown_template(tmp_path):
    (tmp_path / "skills" / "life-sim-template").mkdir(parents=True)
    (tmp_path / "skills" / "life-sim-template" / "SKILL.md").write_text("x", encoding="utf-8")
    # 模板名对不上任何已存在的 skill 目录。
    assert _read_skill_version(tmp_path, "not_a_real_template") == ""


def test_read_skill_version_different_content_changes_value_after_touch(tmp_path):
    """同一个文件只要被修改过（mtime 变化），`skill_version` 就应该
    跟着变——这是"版本区分"这个功能存在的意义。"""
    import os

    skill_dir = tmp_path / "skills" / "life-sim-template"
    skill_dir.mkdir(parents=True)
    skill_md = skill_dir / "SKILL.md"
    skill_md.write_text("v1", encoding="utf-8")
    version1 = _read_skill_version(tmp_path, "life_sim")

    # 显式往后拨一点 mtime，避免文件系统时间戳精度导致的偶发相等。
    new_mtime = skill_md.stat().st_mtime + 5
    os.utime(skill_md, (new_mtime, new_mtime))
    version2 = _read_skill_version(tmp_path, "life_sim")

    assert version1 != version2


# ---------------------------------------------------------------------------
# state_model.py：SimState.skill_version 的序列化往返
# ---------------------------------------------------------------------------


def test_sim_state_skill_version_round_trips_through_to_dict_from_dict():
    state = SimState(step=1, summary="s", skill_version="2026-01-01T00:00:00+00:00")
    restored = SimState.from_dict(state.to_dict())
    assert restored.skill_version == "2026-01-01T00:00:00+00:00"


def test_sim_state_skill_version_defaults_to_empty_string():
    state = SimState(step=0, summary="s0")
    assert state.skill_version == ""

    # 旧数据（没有 skill_version 字段）反序列化时也应该安全默认为空
    # 字符串，不报错。
    old_data = state.to_dict()
    del old_data["skill_version"]
    restored = SimState.from_dict(old_data)
    assert restored.skill_version == ""


# ---------------------------------------------------------------------------
# engine.advance() 端到端集成：skill_version 正确写入落盘状态
# ---------------------------------------------------------------------------


def test_advance_populates_skill_version_from_real_skill_md(tmp_path, monkeypatch):
    """在真实的 world_simulator 项目根下跑 `advance()`（用假的
    WorkflowStore/Runner 打桩，不真的调用 LLM，手法同
    `test_spec_and_engine.py`），验证 `next_state.skill_version` 确实
    读到了仓库里真实存在的 `skills/life-sim-template/SKILL.md` 的
    修改时间。
    """
    import json as json_mod

    import world_simulator.engine as engine_mod

    project_root = Path(__file__).resolve().parent.parent  # external_projects/world_simulator
    real_skill_md = project_root / "skills" / "life-sim-template" / "SKILL.md"
    assert real_skill_md.exists(), "测试假设仓库里真的有这个 skill 文件"

    data_dir = tmp_path / "data"
    manifest = engine_mod.materialize_simulation(
        data_dir, template="life_sim", intent="i", title="t", summary="s",
        vars={"age": 22}, options=[], settings={},
    )

    class FakeStep:
        def __init__(self, step_id):
            self.id = step_id

    class FakeWorkflow:
        def __init__(self, steps):
            self.steps = steps

    class FakeStatus:
        def __init__(self, value):
            self.value = value

    class FakeStoreForAdvance:
        def __init__(self, root):
            pass

        def load(self, name):
            return FakeWorkflow([FakeStep("step")])

    class FakeRunnerForAdvance:
        def __init__(self, cfg):
            pass

        def run(self, wf, inputs):
            result_file = tmp_path / "advance_result.json"
            result_file.write_text(
                json_mod.dumps(
                    {
                        "next_summary": "继续", "narrative": "...",
                        "next_vars": {"age": 23}, "options": [],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            return SimpleNamespace(
                status="done",
                step_results=[
                    SimpleNamespace(
                        step_id="step", status=FakeStatus("done"),
                        result_file=str(result_file),
                    )
                ],
            )

    monkeypatch.setattr("mini_agent.workflow.store.WorkflowStore", FakeStoreForAdvance)
    monkeypatch.setattr("mini_agent.workflow.runner.WorkflowRunner", FakeRunnerForAdvance)

    next_state = engine_mod.advance(
        cfg=object(), workspace_root=project_root, data_dir=data_dir, sim_id=manifest.sim_id,
    )

    assert next_state.skill_version  # 非空
    expected = datetime.fromtimestamp(
        real_skill_md.stat().st_mtime, tz=timezone.utc
    ).isoformat()
    assert next_state.skill_version == expected
