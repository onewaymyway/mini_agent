"""tests/test_goal_execution_spec_stage8e.py

覆盖 next_doc/goal_output_directory_and_execution_phase_redesign_plan.md
Stage 8e：`GoalExecutionSpecBuilder` 教会生成 Stage 8a 新字段
（output_mode/execution_routine/cadence/new_topic_discovery/
hardening_target/sub_exploration）的草稿。
"""
from __future__ import annotations

import json

from mini_agent.perception import goal_execution_spec as ges


class _FakeHelper:
    def __init__(self, response: str):
        self.response = response
        self.calls: list[str] = []

    def ask(self, prompt, *, system="", max_retries=3, retry_policy=None,
             override_model=None, override_provider=None, override_temperature=None):
        self.calls.append(prompt)
        return self.response


def _base_cfg(tmp_path):
    from mini_agent.config.loader import load_config
    cfg = load_config(
        project_root=tmp_path, verbose=False, sandbox=True,
        auto_approve=True, model="claude-sonnet-4-6",
    )
    cfg.api_key = "sk-fake"
    return cfg


class TestSpecFromLlmDataParsesStage8Fields:
    def test_parses_all_six_new_fields(self):
        data = {
            "deliverables": [], "handoff_fields": [], "sub_directories": [],
            "per_cycle_criteria": [], "overall_completion_criteria": [], "special_constraints": [],
            "output_mode": "capability_hardening",
            "execution_routine": [{"step": "试验新场景"}, {"step": "验证有效性"}],
            "cadence": "每天一次",
            "new_topic_discovery": "intrinsic",
            "hardening_target": "skills/report_writer/",
            "sub_exploration": "信息源调研",
        }
        spec = ges._spec_from_llm_data(data, "g1", version=1)
        assert spec.output_mode == "capability_hardening"
        assert [r.step for r in spec.execution_routine] == ["试验新场景", "验证有效性"]
        assert spec.cadence == "每天一次"
        assert spec.new_topic_discovery == "intrinsic"
        assert spec.hardening_target == "skills/report_writer/"
        assert spec.sub_exploration == "信息源调研"

    def test_missing_fields_default_to_backward_compatible_values(self):
        data = {"deliverables": [], "handoff_fields": [], "sub_directories": [],
                "per_cycle_criteria": [], "overall_completion_criteria": [], "special_constraints": []}
        spec = ges._spec_from_llm_data(data, "g1", version=1)
        assert spec.output_mode == "converging"
        assert spec.execution_routine == []
        assert spec.cadence == ""
        assert spec.new_topic_discovery == "none"
        assert spec.hardening_target == ""
        assert spec.sub_exploration == ""

    def test_invalid_output_mode_falls_back_to_default(self):
        data = {"output_mode": "not_a_real_mode"}
        spec = ges._spec_from_llm_data(data, "g1", version=1)
        assert spec.output_mode == "converging"

    def test_invalid_new_topic_discovery_falls_back_to_default(self):
        data = {"new_topic_discovery": "sometimes"}
        spec = ges._spec_from_llm_data(data, "g1", version=1)
        assert spec.new_topic_discovery == "none"


class TestBuildDraftEndToEndParsesNewFields:
    def test_build_draft_parses_accretive_response(self, tmp_path):
        response = json.dumps({
            "deliverables": [{"name": "wiki_entry.md", "naming_pattern": "wiki_entry.md"}],
            "handoff_fields": [], "sub_directories": [],
            "per_cycle_criteria": [], "overall_completion_criteria": [], "special_constraints": [],
            "output_mode": "accretive",
            "execution_routine": [{"step": "扫描已有内容"}, {"step": "去重合并"}, {"step": "写入/更新"}],
            "cadence": "",
            "new_topic_discovery": "intrinsic",
            "hardening_target": "",
            "sub_exploration": "",
        })
        helper = _FakeHelper(response)
        cfg = _base_cfg(tmp_path)
        builder = ges.GoalExecutionSpecBuilder(cfg, llm_helper=helper)

        spec = builder.build_draft("goal_wiki", "维护知识库 Goal", "持续追踪新话题并写入 wiki")

        assert spec.output_mode == "accretive"
        assert spec.new_topic_discovery == "intrinsic"
        assert len(spec.execution_routine) == 3


class TestReviseLocksNewFields:
    def test_revise_locks_hardening_target_field(self, tmp_path):
        prior = ges.GoalExecutionSpec(
            goal_id="g1", version=1, hardening_target="skills/report_writer/",
            output_mode="capability_hardening",
        )
        response = json.dumps({
            "deliverables": [], "handoff_fields": [], "sub_directories": [],
            "per_cycle_criteria": [], "overall_completion_criteria": [], "special_constraints": [],
            "output_mode": "converging",  # 模型没有遵守指示试图改动
            "hardening_target": "some/other/path/",  # 模型没有遵守指示试图改动
        })
        helper = _FakeHelper(response)
        cfg = _base_cfg(tmp_path)
        builder = ges.GoalExecutionSpecBuilder(cfg, llm_helper=helper)

        new_spec = builder.revise(prior, "补充一些约束", locked_fields=["hardening_target"])

        # 锁定字段被强制保留为上一版的值，即便 LLM 输出了别的值
        assert new_spec.hardening_target == "skills/report_writer/"
        # 未锁定字段正常采用 LLM 的新输出
        assert new_spec.output_mode == "converging"
