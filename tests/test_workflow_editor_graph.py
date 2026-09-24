"""
tests/test_workflow_editor_graph.py

覆盖 next_doc/workflow_visual_editor_plan.md §十「图与联动（纯函数）」（里程碑 M3）：
对应 `apps/mini_agent_kanban/workflow_editor.py`（纯函数，不依赖 streamlit）与
`apps/mini_agent_kanban/client.py` 新增的四个编辑器方法。

  - raw → 节点 / 边（含 include 节点、merge 虚线边、徽标、批次、悬空引用）
  - 边差集 → depends_on；环检测（含环路径）
  - 增 / 删 / 复制节点、边上插入节点
  - 改 id 联动（depends_on / merge_sources / condition / 占位符 / prompt_file 正文，
    不误伤同前缀标识符、`inputs.xxx`、字符串字面量、`{param}`）
  - 错误归类、编辑器错误响应解析、变更预览（可喂给 diff_view）、DOT 降级渲染、字段清单
  - client：请求方法 / 路径 / 请求体，以及失败时保留完整结构化 detail

运行方式：
    PYTHONPATH=src python3 -m pytest tests/test_workflow_editor_graph.py -q
"""
from __future__ import annotations

import copy
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "apps" / "mini_agent_kanban"))

import client as client_module  # noqa: E402
import workflow_editor as W  # noqa: E402
from client import AgentClient  # noqa: E402
from diff_view import parse_unified_diff  # noqa: E402


def base_draft() -> dict:
    return {
        "name": "demo",
        "description": "演示",
        "steps": [
            {"id": "analyze", "name": "分析", "prompt": "分析 {inputs_x}"},
            {"id": "review", "name": "评审", "prompt": "评审 {analyze.output}",
             "depends_on": ["analyze"], "condition": "analyze.passed"},
            {"id": "report", "name": "报告", "prompt": "写 {review.output} 与 {analyze.output}",
             "depends_on": ["review", "analyze"], "require_approval": True},
        ],
    }


# ═══════════════════════════════════════════════════════════════════════════
# 图转换
# ═══════════════════════════════════════════════════════════════════════════

def test_graph_nodes_edges_and_badges():
    g = W.draft_to_graph(base_draft())
    assert [n["id"] for n in g["nodes"]] == ["analyze", "review", "report"]
    edges = {(e["source"], e["target"], e["kind"]) for e in g["edges"]}
    assert edges == {("analyze", "review", "depends"), ("review", "report", "depends"),
                     ("analyze", "report", "depends")}
    by = {n["id"]: n for n in g["nodes"]}
    assert "🔀" in by["review"]["badges"] and "🔒" in by["report"]["badges"]
    assert by["analyze"]["badges"] == []
    assert by["analyze"]["type"] == "agent"
    assert g["dangling"] == []


def test_effective_type_matches_backend_rules():
    assert W.effective_type({"id": "a", "prompt": "x"}) == "agent"
    assert W.effective_type({"id": "a", "role": "coach"}) == "role_agent"
    assert W.effective_type({"id": "a", "type": "wait"}) == "wait"
    assert W.effective_type({"id": "a", "include": "snip"}) == "include"


def test_include_node_without_id_uses_snippet_name():
    d = {"name": "x", "steps": [{"include": "common"}, {"id": "t", "prompt": "p", "depends_on": ["common"]}]}
    g = W.draft_to_graph(d)
    assert [n["id"] for n in g["nodes"]] == ["common", "t"]
    assert g["nodes"][0]["is_include"] and "🧩" in g["nodes"][0]["badges"]
    assert {(e["source"], e["target"]) for e in g["edges"]} == {("common", "t")}


def test_merge_sources_drawn_dashed_only_when_not_already_dependency():
    d = base_draft()
    d["steps"].append({"id": "join", "type": "merge", "merge_sources": ["analyze", "review"],
                       "depends_on": ["review"]})
    g = W.draft_to_graph(d)
    kinds = {(e["source"], e["target"]): e["kind"] for e in g["edges"] if e["target"] == "join"}
    assert kinds == {("review", "join"): "depends", ("analyze", "join"): "merge"}


def test_dangling_references_reported_not_drawn():
    d = base_draft()
    d["steps"][1]["depends_on"] = ["analyze", "ghost"]
    g = W.draft_to_graph(d)
    assert {"node": "review", "missing": "ghost", "field": "depends_on"} in g["dangling"]
    assert all(e["source"] != "ghost" for e in g["edges"])


def test_errors_batches_and_border_colors_flow_into_nodes():
    g = W.draft_to_graph(base_draft(), {"report": ["步骤 'report' 出错"]}, {"review": ["warn"]},
                         [["analyze"], ["review"], ["report"]])
    by = {n["id"]: n for n in g["nodes"]}
    assert by["report"]["has_error"] and "⚠️" in by["report"]["badges"]
    assert by["report"]["border"] == W.ERROR_BORDER
    assert by["review"]["has_warning"] and by["review"]["border"] == W.WARNING_BORDER
    assert [by[i]["batch"] for i in ("analyze", "review", "report")] == [0, 1, 2]


def test_compute_layers_local_kahn_and_cycle_tolerant():
    assert W.compute_layers(base_draft()) == [["analyze"], ["review"], ["report"]]
    d = base_draft()
    d["steps"][0]["depends_on"] = ["report"]
    layers = W.compute_layers(d)
    assert sorted(layers[-1]) == ["analyze", "report", "review"]      # 环上节点放最后一层，不丢节点


# ═══════════════════════════════════════════════════════════════════════════
# 环检测 / 依赖编辑
# ═══════════════════════════════════════════════════════════════════════════

def test_find_cycle_none_and_found():
    assert W.find_cycle(base_draft()) is None
    d = base_draft()
    d["steps"][0]["depends_on"] = ["report"]
    cyc = W.find_cycle(d)
    assert cyc and cyc[0] == cyc[-1] and set(cyc) == {"analyze", "review", "report"}


def test_add_dependency_ok_and_original_not_mutated():
    d = base_draft()
    d["steps"].append({"id": "x", "prompt": "p"})
    before = copy.deepcopy(d)
    nd, err = W.add_dependency(d, "report", "x")
    assert err is None
    assert W.find_step(nd, "x")["depends_on"] == ["report"]
    assert d == before                                                 # 入参未被修改


def test_add_dependency_rejects_cycle_with_path():
    d = base_draft()
    nd, err = W.add_dependency(d, "report", "analyze")               # report 依赖链上游 → 成环
    assert nd is d
    assert err.startswith("会形成循环依赖：")
    assert "report" in err and "analyze" in err
    assert W.would_create_cycle(d, "report", "analyze")[0] == "report"


def test_add_dependency_rejects_self_missing_and_duplicate():
    d = base_draft()
    assert W.add_dependency(d, "analyze", "analyze")[1] == "步骤不能依赖自己"
    assert "不存在" in W.add_dependency(d, "ghost", "analyze")[1]
    assert "已经依赖" in W.add_dependency(d, "analyze", "review")[1]


def test_remove_dependency_drops_empty_key():
    d = W.remove_dependency(base_draft(), "analyze", "review")
    assert "depends_on" not in W.find_step(d, "review")
    d2 = W.remove_dependency(base_draft(), "analyze", "report")
    assert W.find_step(d2, "report")["depends_on"] == ["review"]


def test_dependency_diff_and_apply_with_cycle_rejection():
    d = base_draft()
    canvas = [("analyze", "review"), ("review", "report"), ("report", "analyze")]   # 删 analyze→report，加成环边
    added, removed = W.dependency_diff(d, canvas)
    assert added == [("report", "analyze")] and removed == [("analyze", "report")]
    nd, errors = W.apply_dependency_diff(d, added, removed)
    assert len(errors) == 1 and "循环依赖" in errors[0]
    assert W.find_step(nd, "report")["depends_on"] == ["review"]      # 删除生效，成环的新增被拒绝
    assert W.find_cycle(nd) is None


# ═══════════════════════════════════════════════════════════════════════════
# 增删复制 / 插入
# ═══════════════════════════════════════════════════════════════════════════

def test_add_step_defaults_and_after():
    d, nid = W.add_step(base_draft(), "tool_call", after="analyze")
    s = W.find_step(d, nid)
    assert s["type"] == "tool_call" and s["depends_on"] == ["analyze"] and s["tool_name"] == ""
    assert W.step_ids(d).index(nid) == 1                              # 紧跟 after 之后
    d2, nid2 = W.add_step(d, "tool_call")
    assert nid2 != nid and W.find_step(d2, nid2).get("depends_on") is None


def test_unique_ids_never_collide():
    d = base_draft()
    for _ in range(4):
        d, _nid = W.add_step(d, "agent")
    ids = W.step_ids(d)
    assert len(ids) == len(set(ids))


def test_duplicate_step_inlines_prompt_file_and_keeps_deps():
    d = base_draft()
    d["steps"][1]["prompt_file"] = "prompts/r.md"
    del d["steps"][1]["prompt"]
    nd, nid = W.duplicate_step(d, "review", {"prompts/r.md": "正文 {analyze.output}"})
    dup = W.find_step(nd, nid)
    assert dup["depends_on"] == ["analyze"] and "prompt_file" not in dup
    assert dup["prompt"] == "正文 {analyze.output}"
    assert dup["name"].endswith("（副本）")
    assert W.step_ids(nd).index(nid) == W.step_ids(nd).index("review") + 1
    assert W.find_step(nd, "review")["prompt_file"] == "prompts/r.md"  # 原节点不动


def test_delete_step_cleans_refs_and_reports_impact():
    d = base_draft()
    rep = W.analyze_delete(d, "analyze")
    assert sorted(rep.dependents) == ["report", "review"]
    assert {(r["step"], r["where"]) for r in rep.other_refs} >= {("review", "condition"), ("review", "prompt"),
                                                                 ("report", "prompt")}
    nd = W.delete_step(d, "analyze")
    assert W.find_step(nd, "analyze") is None
    assert "depends_on" not in W.find_step(nd, "review")
    assert W.find_step(nd, "report")["depends_on"] == ["review"]
    assert W.find_step(nd, "review")["condition"] == "analyze.passed"  # condition 不擅自改写
    nd2 = W.delete_step(d, "analyze", cleanup_refs=False)
    assert W.find_step(nd2, "review")["depends_on"] == ["analyze"]


def test_delete_cleans_merge_sources():
    d = base_draft()
    d["steps"].append({"id": "join", "type": "merge", "merge_sources": ["review", "analyze"]})
    nd = W.delete_step(d, "review")
    assert W.find_step(nd, "join")["merge_sources"] == ["analyze"]


def test_insert_between_rewires_edge():
    d = base_draft()
    nd, nid = W.insert_between(d, "review", "report", "wait")
    assert W.find_step(nd, nid)["depends_on"] == ["review"]
    assert W.find_step(nd, "report")["depends_on"] == [nid, "analyze"]
    assert W.find_cycle(nd) is None
    same, none_id = W.insert_between(d, "analyze", "analyze", "agent")
    assert same is d and none_id == ""


def test_validate_new_id():
    d = base_draft()
    assert W.validate_new_id(d, "fresh")[0]
    assert not W.validate_new_id(d, "")[0]
    assert not W.validate_new_id(d, "a.b")[0]
    assert not W.validate_new_id(d, "a b")[0]
    assert not W.validate_new_id(d, "review")[0]
    assert W.validate_new_id(d, "review", current="review")[0]
    ok, note = W.validate_new_id(d, "step-2")
    assert ok and "标识符" in note


# ═══════════════════════════════════════════════════════════════════════════
# 改 id 联动
# ═══════════════════════════════════════════════════════════════════════════

def test_rename_updates_deps_condition_and_placeholders():
    r = W.rename_step(base_draft(), "analyze", "scan")
    assert r.error is None and r.renames == {"analyze": "scan"}
    d = r.draft
    assert W.step_ids(d) == ["scan", "review", "report"]
    assert W.find_step(d, "review")["depends_on"] == ["scan"]
    assert W.find_step(d, "review")["condition"] == "scan.passed"
    assert W.find_step(d, "review")["prompt"] == "评审 {scan.output}"
    assert W.find_step(d, "report")["depends_on"] == ["review", "scan"]
    assert W.find_step(d, "report")["prompt"] == "写 {review.output} 与 {scan.output}"
    fields = {(c["step"], c["field"]) for c in r.changes}
    assert {("scan", "id"), ("review", "depends_on"), ("review", "condition"), ("review", "prompt"),
            ("report", "prompt")} <= fields


def test_rename_does_not_touch_prefix_siblings_params_inputs_or_strings():
    d = {"name": "x", "steps": [
        {"id": "a", "prompt": "p"},
        {"id": "ab", "prompt": "p2", "depends_on": ["a"]},
        {"id": "c", "depends_on": ["a", "ab"],
         "prompt": "用 {a.output} 但不动 {ab.output} 和参数 {a} 以及 {data.a}",
         "condition": "inputs.a == 'a' and a.passed and ab.passed and a_x.passed"},
    ]}
    r = W.rename_step(d, "a", "z")
    c = W.find_step(r.draft, "c")
    assert c["prompt"] == "用 {z.output} 但不动 {ab.output} 和参数 {a} 以及 {data.a}"
    assert c["condition"] == "inputs.a == 'a' and z.passed and ab.passed and a_x.passed"
    assert c["depends_on"] == ["z", "ab"]
    assert W.find_step(r.draft, "ab")["depends_on"] == ["z"]


def test_rename_condition_handles_unicode_and_syntax_errors():
    assert W.rename_in_condition("评分.passed and x.y", "评分", "score") == "score.passed and x.y"
    assert W.rename_in_condition("a.passed and (", "a", "b") == "a.passed and ("      # 语法错误原样保留
    assert W.rename_in_condition("a.passed or a.score > 3", "a", "bb") == "bb.passed or bb.score > 3"


def test_rename_rewrites_nested_tool_args_and_prompt_file_bodies():
    d = base_draft()
    d["steps"].append({"id": "t", "type": "tool_call", "tool_name": "x", "prompt": "p",
                       "depends_on": ["analyze"],
                       "tool_args": {"q": "{analyze.output}", "list": ["{analyze.score}", 3], "n": 1}})
    d["steps"][1]["prompt_file"] = "prompts/r.md"
    del d["steps"][1]["prompt"]
    r = W.rename_step(d, "analyze", "scan", {"prompts/r.md": "评审 {analyze.output} 和 {analyzer.output}"})
    t = W.find_step(r.draft, "t")
    assert t["tool_args"] == {"q": "{scan.output}", "list": ["{scan.score}", 3], "n": 1}
    assert r.prompt_files["prompts/r.md"] == "评审 {scan.output} 和 {analyzer.output}"
    assert any(c["field"].startswith("prompt_file(") for c in r.changes)


def test_rename_without_sync_only_changes_id():
    r = W.rename_step(base_draft(), "analyze", "scan", sync_refs=False)
    assert W.find_step(r.draft, "review")["depends_on"] == ["analyze"]
    assert [c["field"] for c in r.changes] == ["id"]


def test_rename_rejects_bad_targets_and_is_pure():
    d = base_draft()
    before = copy.deepcopy(d)
    assert W.rename_step(d, "analyze", "review").error
    assert W.rename_step(d, "analyze", "a.b").error
    assert W.rename_step(d, "ghost", "x").error
    assert W.rename_step(d, "analyze", "analyze").renames == {}
    W.rename_step(d, "analyze", "scan")
    assert d == before


# ═══════════════════════════════════════════════════════════════════════════
# 校验归类 / 错误解析
# ═══════════════════════════════════════════════════════════════════════════

def test_group_messages_local_fallback_including_include_prefix():
    by, rest = W.group_messages(
        ["步骤 'report' 依赖不存在的步骤 'x'", "步骤 'box__inner' 的 prompt 为空", "工作流名称不能修改"],
        ["report", "box"])
    assert by == {"report": ["步骤 'report' 依赖不存在的步骤 'x'"], "box": ["步骤 'box__inner' 的 prompt 为空"]}
    assert rest == ["工作流名称不能修改"]


def test_normalize_validation_prefers_backend_fields_and_falls_back():
    backend = {"ok": False, "errors": ["e"], "errors_by_step": {"a": ["e"]}, "warnings": [],
               "cycle": ["a", "b", "a"], "batches": None}
    n = W.normalize_validation(backend)
    assert n["errors_by_step"] == {"a": ["e"]} and n["cycle"] == ["a", "b", "a"] and not n["ok"]
    fb = W.normalize_validation({"errors": ["步骤 'a' 坏了", "整体问题"]}, ["a"])
    assert fb["errors_by_step"] == {"a": ["步骤 'a' 坏了"]} and fb["workflow_errors"] == ["整体问题"]
    assert W.normalize_validation(None)["ok"] is True


def test_parse_editor_error_kinds():
    assert W.parse_editor_error({"draft": {}}) is None
    e = W.parse_editor_error({"_error": "m", "_status": 409,
                              "_detail": {"code": "conflict", "message": "被改过", "current_hash": "h"}})
    assert (e.kind, e.message, e.current_hash) == ("conflict", "被改过", "h")
    assert W.parse_editor_error({"_error": "x", "_status": 422,
                                 "_detail": {"code": "validation_failed", "errors_by_step": {"a": ["e"]}}}).kind == "validation"
    assert W.parse_editor_error({"_error": "x", "_status": 403, "_detail": {"code": "editor_disabled"}}).kind == "disabled"
    assert W.parse_editor_error({"_error": "x", "_status": 409, "_detail": {"code": "needs_confirm"}}).kind == "needs_confirm"
    assert W.parse_editor_error({"_error": "连接失败"}).kind == "network"
    assert W.parse_editor_error({"_error": "x", "_status": 500}).kind == "other"


# ═══════════════════════════════════════════════════════════════════════════
# 变更预览 / dirty
# ═══════════════════════════════════════════════════════════════════════════

def test_is_dirty_ignores_none_and_key_order():
    o = base_draft()
    d = copy.deepcopy(o)
    assert not W.is_dirty(o, d)
    d["steps"][0]["condition"] = None                         # 表单里的空字段
    assert not W.is_dirty(o, d)
    d["steps"][0]["prompt"] = "改"
    assert W.is_dirty(o, d)
    d2 = copy.deepcopy(o)
    d2["steps"][0] = dict(reversed(list(d2["steps"][0].items())))
    assert not W.is_dirty(o, d2)
    assert W.is_dirty(o, o, {"p": "a"}, {"p": "b"})
    assert not W.is_dirty(o, o, {"p": "a"}, {"p": "a"})


def test_summarize_changes_with_rename_and_reorder():
    o = base_draft()
    r = W.rename_step(o, "analyze", "scan")
    s = W.summarize_changes(o, r.draft, r.renames)
    assert s["added"] == [] and s["removed"] == []
    assert set(s["modified"]) == {"scan", "review", "report"}
    d = copy.deepcopy(o)
    d["steps"].reverse()
    assert W.summarize_changes(o, d)["reordered"] is True
    d2, nid = W.add_step(o, "agent")
    assert W.summarize_changes(o, d2)["added"] == [nid]
    assert W.summarize_changes(o, W.delete_step(o, "report"))["removed"] == ["report"]
    d3 = copy.deepcopy(o)
    d3["description"] = "新"
    assert W.summarize_changes(o, d3)["top_level"] == ["description"]


def test_build_change_diff_parses_with_diff_view():
    o = base_draft()
    d = copy.deepcopy(o)
    d["steps"][0]["prompt"] = "新的分析 prompt"
    text = W.build_change_diff("demo", o, d, {"prompts/a.md": "旧\n"}, {"prompts/a.md": "新\n"})
    files = parse_unified_diff(text)
    assert [f.path for f in files] == ["demo.yaml", "prompts/a.md"]
    assert files[0].additions == 1 and files[0].deletions == 1
    assert W.build_change_diff("demo", o, copy.deepcopy(o)) == ""


# ═══════════════════════════════════════════════════════════════════════════
# 降级渲染 / 字段清单
# ═══════════════════════════════════════════════════════════════════════════

def test_to_dot_structure_selection_and_escaping():
    d = base_draft()
    d["steps"][0]["name"] = '含"引号"的名字'
    d["steps"].append({"id": "join", "type": "merge", "merge_sources": ["analyze"], "depends_on": ["review"]})
    dot = W.to_dot(W.draft_to_graph(d, {"report": ["err"]}), selected="review")
    assert dot.startswith("digraph workflow {") and dot.rstrip().endswith("}")
    assert '\\"引号\\"' in dot                                  # 引号被转义
    assert '"analyze" -> "join" [style=dashed' in dot          # merge 虚线
    assert '"review" [' in dot and "penwidth=3" in dot         # 选中加粗
    assert W.ERROR_BORDER in dot                                # 错误红框


def test_field_specs_are_consistent_with_backend_schema():
    """面板暴露的每个字段都必须是后端 WorkflowStep 真实存在的字段（防止 UI 写出后端不认识的键）。"""
    import dataclasses
    from mini_agent.workflow.schema import STEP_TYPES, WorkflowStep
    known = {f.name for f in dataclasses.fields(WorkflowStep)}
    for stype, specs in W.STEP_FIELD_SPECS.items():
        assert stype in STEP_TYPES
        for sp in specs:
            assert sp.key in known, (stype, sp.key)
    for sp in W.ADVANCED_FIELD_SPECS:
        assert sp.key in known, sp.key
    assert set(STEP_TYPES) <= set(W.STEP_FIELD_SPECS)          # 每种内置类型都有面板清单
    assert set(STEP_TYPES) <= set(W.STEP_TYPE_STYLES)          # 每种内置类型都有样式


def test_skeletons_only_use_known_fields_and_fields_for_include():
    import dataclasses
    from mini_agent.workflow.schema import WorkflowStep
    known = {f.name for f in dataclasses.fields(WorkflowStep)}
    for stype, skel in W._STEP_SKELETONS.items():
        assert set(skel) <= known, stype
    assert W.fields_for({"id": "b", "include": "x"}) == ((), ())
    spec, adv = W.fields_for({"id": "a", "type": "tool_call"})
    assert [s.key for s in spec][:2] == ["prompt", "tool_name"] and adv == W.ADVANCED_FIELD_SPECS
    assert W.INCLUDE_EDITABLE_FIELDS == ("id", "depends_on")


def test_runtime_switch_note():
    assert "script_step_enabled" in W.runtime_switch_note("script", {"script_step_enabled": False})
    assert W.runtime_switch_note("script", {"script_step_enabled": True}) == ""
    assert "python_step_enabled" in W.runtime_switch_note("python_step", None)
    assert W.runtime_switch_note("agent", {}) == ""


def test_new_step_skeletons_get_reported_by_backend_validation(tmp_path):
    """新增出来的空骨架节点不能悄悄通过校验（否则用户会存下一个不能跑的步骤）。"""
    from types import SimpleNamespace
    from mini_agent.workflow import editor_helpers as E
    wdir = tmp_path / ".agent" / "workflows"
    wdir.mkdir(parents=True)
    (wdir / "demo.yaml").write_text("name: demo\nsteps:\n  - {id: a, name: A, prompt: x}\n", encoding="utf-8")
    cfg = SimpleNamespace(project_root=str(tmp_path), workflow=SimpleNamespace(
        script_step_enabled=True, python_step_enabled=True, validate_role_refs_on_save=False))
    d = E.load_for_edit(cfg, "demo")["draft"]
    for stype in ("agent", "tool_call", "sub_workflow", "script", "skill_agent", "python_step", "merge", "foreach"):
        nd, nid = W.add_step(d, stype, after="a")
        v = E.validate_draft(cfg, "demo", nd)
        assert not v["ok"], stype
        assert nid in v["errors_by_step"], stype
    nd, nid = W.add_step(d, "wait", after="a")
    assert E.validate_draft(cfg, "demo", nd)["ok"]              # wait 骨架本身合法


# ═══════════════════════════════════════════════════════════════════════════
# client
# ═══════════════════════════════════════════════════════════════════════════

def _resp(status=200, json_body=None, text=""):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = json_body
    r.text = text
    return r


def test_client_editor_methods_requests(monkeypatch):
    req = MagicMock(return_value=_resp(200, {"ok": True}))
    monkeypatch.setattr(client_module._HTTP, "request", req)
    c = AgentClient("http://localhost:8000", "tok")

    c.workflow_editor_doc("demo")
    args, kw = req.call_args
    assert args[0] == "GET" and args[1].endswith("/workflows/demo/editor")

    c.workflow_editor_meta()
    assert req.call_args[1]["params"] is None
    c.workflow_editor_meta("wf")
    assert req.call_args[1]["params"] == {"workflow": "wf"}
    assert req.call_args[0][1].endswith("/workflow_editor/meta")

    c.validate_workflow_draft("demo", {"name": "demo"})
    args, kw = req.call_args
    assert args[0] == "POST" and args[1].endswith("/workflows/demo/editor/validate")
    assert kw["json"] == {"draft": {"name": "demo"}, "prompt_files": {}}      # 未传 renames 不出现该 key
    c.validate_workflow_draft("demo", {}, {"p": "x"}, {"a": "b"})
    assert req.call_args[1]["json"]["renames"] == {"a": "b"}

    c.save_workflow_draft("demo", {"name": "demo"}, "h1")
    args, kw = req.call_args
    assert args[0] == "PUT" and args[1].endswith("/workflows/demo/editor")
    assert kw["json"] == {"draft": {"name": "demo"}, "base_hash": "h1", "prompt_files": {}}
    assert kw["headers"] == {"Authorization": "Bearer tok"}
    c.save_workflow_draft("demo", {}, "h1", {"p": "x"}, {"p": "hh"}, {"a": "b"}, force=True, confirm_comment_loss=True)
    body = req.call_args[1]["json"]
    assert body["force"] is True and body["confirm_comment_loss"] is True
    assert body["prompt_hashes"] == {"p": "hh"} and body["renames"] == {"a": "b"}


def test_client_editor_error_keeps_full_structured_detail(monkeypatch):
    detail = {"message": "校验未通过", "code": "validation_failed",
              "errors_by_step": {"report": ["x" * 500]}, "cycle": None}
    monkeypatch.setattr(client_module._HTTP, "request", MagicMock(return_value=_resp(422, {"detail": detail})))
    c = AgentClient("http://localhost:8000")
    resp = c.save_workflow_draft("demo", {}, "h")
    assert resp["_error"] == "校验未通过" and resp["_status"] == 422
    assert resp["_detail"]["errors_by_step"]["report"][0] == "x" * 500        # 不像 _put 那样被截成 200 字符
    err = W.parse_editor_error(resp)
    assert err.kind == "validation" and err.detail["errors_by_step"]["report"]


def test_client_editor_error_shapes(monkeypatch):
    c = AgentClient("http://localhost:8000")
    monkeypatch.setattr(client_module._HTTP, "request",
                        MagicMock(return_value=_resp(404, {"detail": "找不到"})))
    r = c.workflow_editor_doc("nope")
    assert r["_error"] == "找不到" and r["_status"] == 404 and r["_detail"] == {}
    monkeypatch.setattr(client_module._HTTP, "request",
                        MagicMock(return_value=_resp(502, None, "<html>bad gateway</html>")))
    monkeypatch.setattr(client_module._HTTP.request.return_value, "json", MagicMock(side_effect=ValueError))
    assert "HTTP 502" in c.workflow_editor_doc("x")["_error"]
    monkeypatch.setattr(client_module._HTTP, "request", MagicMock(side_effect=ConnectionError("拒绝连接")))
    r = c.workflow_editor_doc("x")
    assert r == {"_error": "拒绝连接"}
    assert W.parse_editor_error(r).kind == "network"
