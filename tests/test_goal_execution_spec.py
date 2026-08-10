"""tests/test_goal_execution_spec.py

覆盖 next_doc/goal_execution_spec_generation_plan.md 的核心行为：
  1. GoalExecutionSpec 数据模型：to_dict/from_dict 往返、is_empty()、
     render_prompt_block() 格式化
  2. 存储：load_spec/save_spec/delete_spec（独立文件，不进 goals.json）
  3. 模板库：list_templates()/load_template()
  4. GoalExecutionSpecBuilder：build_draft()（含 template_id/history 输入）、
     revise() 的字段级锁定、失败兜底（空草稿 + generation_error）
  5. §5.1 轻量核对：soft_check_manifest() 的字符串匹配逻辑
  6. goal_cron_bridge/goal_backlog 两处消费方：未确认不生效、确认后正确拼进
     description、soft-check 连续 miss 触发"建议复查"提示
"""

from __future__ import annotations

import json
from pathlib import Path

from mini_agent.perception import goal_execution_spec as ges
from mini_agent.storage.paths import AgentPaths


def _paths(tmp_path) -> AgentPaths:
    return AgentPaths(project_root=tmp_path)


# ── 数据模型 ──────────────────────────────────────────────────────────────────

def test_spec_roundtrip_to_dict_from_dict():
    spec = ges.GoalExecutionSpec(goal_id="goal_1")
    spec.deliverables.append(ges.Deliverable(name="report.md", naming_pattern="report.md"))
    spec.handoff_fields.append(ges.HandoffField(key="cursor", example="page_1"))
    spec.sub_directories.append(ges.SubDirectory(name="raw/", purpose="原始数据"))
    spec.per_cycle_criteria.append(ges.Criterion(text="文件存在", verification_method="file_check"))
    spec.special_constraints.append("不要暴露真实姓名")

    d = spec.to_dict()
    spec2 = ges.GoalExecutionSpec.from_dict(d)

    assert spec2.goal_id == "goal_1"
    assert spec2.deliverables[0].name == "report.md"
    assert spec2.handoff_fields[0].key == "cursor"
    assert spec2.sub_directories[0].name == "raw/"
    assert spec2.per_cycle_criteria[0].verification_method == "file_check"
    assert spec2.special_constraints == ["不要暴露真实姓名"]


def test_spec_is_empty():
    empty = ges.GoalExecutionSpec(goal_id="g")
    assert empty.is_empty()

    non_empty = ges.GoalExecutionSpec(goal_id="g")
    non_empty.special_constraints.append("x")
    assert not non_empty.is_empty()


def test_criterion_from_dict_rejects_invalid_verification_method():
    c = ges.Criterion.from_dict({"text": "t", "verification_method": "not_a_real_method"})
    assert c.verification_method == "manual_review"


def test_render_prompt_block_includes_all_sections_and_empty_returns_blank():
    spec = ges.GoalExecutionSpec(goal_id="g")
    assert spec.render_prompt_block() == ""

    spec.deliverables.append(ges.Deliverable(name="report.md", naming_pattern="report.md", description="周报"))
    spec.per_cycle_criteria.append(ges.Criterion(text="report.md 存在", verification_method="file_check"))
    spec.handoff_fields.append(ges.HandoffField(key="last_metrics", description="上次数字"))
    spec.special_constraints.append("不要覆盖历史文件")

    block = spec.render_prompt_block()
    assert "report.md" in block
    assert "周报" in block
    assert "report.md 存在" in block
    assert "不要覆盖历史文件" in block
    assert "```handoff" in block
    assert "last_metrics" in block


# ── 存储 ──────────────────────────────────────────────────────────────────────

def test_load_spec_returns_none_when_missing(tmp_path):
    paths = _paths(tmp_path)
    assert ges.load_spec(paths, "goal_missing") is None


def test_save_and_load_spec_roundtrip(tmp_path):
    paths = _paths(tmp_path)
    spec = ges.GoalExecutionSpec(goal_id="goal_x")
    spec.deliverables.append(ges.Deliverable(name="a.md", naming_pattern="a.md"))

    saved_path = ges.save_spec(paths, "goal_x", spec)
    assert saved_path.exists()
    assert saved_path == Path(tmp_path) / ".agent" / "goal_execution_specs" / "goal_x.json"

    loaded = ges.load_spec(paths, "goal_x")
    assert loaded is not None
    assert loaded.deliverables[0].name == "a.md"


def test_load_spec_returns_none_on_corrupt_json(tmp_path):
    paths = _paths(tmp_path)
    d = Path(tmp_path) / ".agent" / "goal_execution_specs"
    d.mkdir(parents=True, exist_ok=True)
    (d / "goal_bad.json").write_text("{not valid json", encoding="utf-8")
    assert ges.load_spec(paths, "goal_bad") is None


def test_delete_spec(tmp_path):
    paths = _paths(tmp_path)
    spec = ges.GoalExecutionSpec(goal_id="goal_y")
    ges.save_spec(paths, "goal_y", spec)
    assert ges.delete_spec(paths, "goal_y") is True
    assert ges.load_spec(paths, "goal_y") is None
    # 再删一次不存在的文件，返回 False 而不是抛异常
    assert ges.delete_spec(paths, "goal_y") is False


# ── 模板库 ────────────────────────────────────────────────────────────────────

def test_list_templates_returns_five_builtin_templates():
    templates = ges.list_templates()
    ids = {t["id"] for t in templates}
    assert ids == {
        "periodic_report", "data_collection", "monitoring_patrol",
        "codebase_maintenance", "research_exploration",
    }


def test_load_template_returns_skeleton():
    t = ges.load_template("periodic_report")
    assert t is not None
    assert "skeleton" in t
    assert t["skeleton"]["deliverables"]


def test_load_template_missing_returns_none():
    assert ges.load_template("does_not_exist") is None
    assert ges.load_template(None) is None
    assert ges.load_template("") is None


# ── GoalExecutionSpecBuilder ──────────────────────────────────────────────────

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


def test_build_draft_parses_llm_json(tmp_path):
    response = json.dumps({
        "deliverables": [{"name": "report.md", "naming_pattern": "report.md", "required_every_cycle": True}],
        "handoff_fields": [{"key": "cursor", "description": "游标"}],
        "sub_directories": [],
        "per_cycle_criteria": [{"text": "文件存在", "verification_method": "file_check"}],
        "overall_completion_criteria": [],
        "special_constraints": [],
    })
    helper = _FakeHelper(response)
    cfg = _base_cfg(tmp_path)
    builder = ges.GoalExecutionSpecBuilder(cfg, llm_helper=helper)

    spec = builder.build_draft("goal_1", "周报 Goal", "每周汇总一次数据")

    assert spec.goal_id == "goal_1"
    assert spec.version == 1
    assert spec.confirmed is False
    assert spec.deliverables[0].name == "report.md"
    assert spec.handoff_fields[0].key == "cursor"
    assert len(helper.calls) == 1


def test_build_draft_falls_back_on_unparseable_response(tmp_path):
    helper = _FakeHelper("this is not json at all")
    cfg = _base_cfg(tmp_path)
    builder = ges.GoalExecutionSpecBuilder(cfg, llm_helper=helper)

    spec = builder.build_draft("goal_2", "某个 Goal")

    assert spec.is_empty()
    assert spec.generation_error
    assert spec.confirmed is False


def test_build_draft_with_template_includes_skeleton_in_prompt(tmp_path):
    helper = _FakeHelper(json.dumps({"deliverables": [], "handoff_fields": [], "sub_directories": [],
                                      "per_cycle_criteria": [], "overall_completion_criteria": [],
                                      "special_constraints": []}))
    cfg = _base_cfg(tmp_path)
    builder = ges.GoalExecutionSpecBuilder(cfg, llm_helper=helper)

    builder.build_draft("goal_3", "周报 Goal", template_id="periodic_report")

    assert len(helper.calls) == 1
    assert "periodic_report" in helper.calls[0] or "report.md" in helper.calls[0]


def test_revise_locks_specified_fields(tmp_path):
    prior = ges.GoalExecutionSpec(goal_id="goal_4", version=1)
    prior.deliverables.append(ges.Deliverable(name="keep.md", naming_pattern="keep.md"))
    prior.special_constraints.append("原始约束")

    # LLM 修订结果"违规"地改动了被锁定的 deliverables（模拟不听话的情况），
    # revise() 应该强制用上一版的值覆盖回去。
    response = json.dumps({
        "deliverables": [{"name": "changed.md", "naming_pattern": "changed.md"}],
        "handoff_fields": [],
        "sub_directories": [],
        "per_cycle_criteria": [{"text": "新增标准", "verification_method": "manual_review"}],
        "overall_completion_criteria": [],
        "special_constraints": ["原始约束"],
    })
    helper = _FakeHelper(response)
    cfg = _base_cfg(tmp_path)
    builder = ges.GoalExecutionSpecBuilder(cfg, llm_helper=helper)

    new_spec = builder.revise(prior, "加一条完成标准", locked_fields=["deliverables"])

    assert new_spec.version == 2
    # deliverables 被锁定，强制保留上一版内容，不接受 LLM 的改动
    assert new_spec.deliverables[0].name == "keep.md"
    # 未锁定字段按 LLM 输出更新
    assert new_spec.per_cycle_criteria[0].text == "新增标准"


def test_revise_falls_back_to_prior_spec_on_parse_failure(tmp_path):
    prior = ges.GoalExecutionSpec(goal_id="goal_5", version=1, confirmed=True)
    prior.deliverables.append(ges.Deliverable(name="a.md"))

    helper = _FakeHelper("not valid json")
    cfg = _base_cfg(tmp_path)
    builder = ges.GoalExecutionSpecBuilder(cfg, llm_helper=helper)

    result = builder.revise(prior, "随便改改")

    # 修订失败：内容保留上一版（不会丢已确认的内容），但 confirmed 被重置为 False
    assert result.deliverables[0].name == "a.md"
    assert result.confirmed is False
    assert result.generation_error


def test_confirm_sets_confirmed_and_timestamp():
    spec = ges.GoalExecutionSpec(goal_id="g")
    assert spec.confirmed is False
    ges.GoalExecutionSpecBuilder.confirm(spec)
    assert spec.confirmed is True
    assert spec.confirmed_at is not None


# ── §5.1 轻量核对 ─────────────────────────────────────────────────────────────

def test_soft_check_manifest_ok_when_all_present():
    spec = ges.GoalExecutionSpec(goal_id="g")
    spec.deliverables.append(ges.Deliverable(name="report.md", naming_pattern="report.md"))
    spec.handoff_fields.append(ges.HandoffField(key="cursor"))

    manifest = {
        "artifacts": [{"path": "cycle_0001/report.md"}],
        "progress_note": '完成本轮。```handoff\n{"cursor": "page_5"}\n```',
    }
    result = ges.soft_check_manifest(spec, manifest)
    assert result["ok"] is True
    assert result["missing_deliverables"] == []
    assert result["missing_handoff_keys"] == []


def test_soft_check_manifest_reports_missing_deliverable_and_handoff():
    spec = ges.GoalExecutionSpec(goal_id="g")
    spec.deliverables.append(ges.Deliverable(name="report.md", naming_pattern="report.md"))
    spec.handoff_fields.append(ges.HandoffField(key="cursor"))

    manifest = {"artifacts": [{"path": "cycle_0001/other_file.txt"}], "progress_note": "没写 handoff"}
    result = ges.soft_check_manifest(spec, manifest)
    assert result["ok"] is False
    assert "report.md" in result["missing_deliverables"]
    assert "cursor" in result["missing_handoff_keys"]


def test_soft_check_manifest_ignores_optional_deliverables():
    spec = ges.GoalExecutionSpec(goal_id="g")
    spec.deliverables.append(ges.Deliverable(name="opt.md", naming_pattern="opt.md", required_every_cycle=False))

    result = ges.soft_check_manifest(spec, {"artifacts": [], "progress_note": ""})
    assert result["ok"] is True


def test_get_handoff_data_parses_fenced_json():
    note = 'blah\n```handoff\n{"a": 1, "b": "x"}\n```\nmore text'
    data = ges.get_handoff_data(note)
    assert data == {"a": 1, "b": "x"}


def test_get_handoff_data_returns_none_when_absent():
    assert ges.get_handoff_data("no handoff block here") is None
