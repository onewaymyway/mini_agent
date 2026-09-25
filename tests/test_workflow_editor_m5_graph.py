"""
tests/test_workflow_editor_m5_graph.py

覆盖 next_doc/workflow_visual_editor_plan.md §八 里程碑 M5 在看板侧新增的**纯函数**
（`apps/mini_agent_kanban/workflow_editor.py` §10~§13，不依赖 streamlit）与
`apps/mini_agent_kanban/client.py` 新增的四个方法：

  - 画布边同步：拖线加依赖 / 删边 → depends_on（含成环拒绝、merge 虚线边只读、
    **陈旧回传不能把刚加的依赖误删**——这是最容易出事故的一条）
  - 画布选中解析（节点 / 边）、边选择器
  - 并行批次摘要与高亮（含 allow_parallel=false 单独串行）、DOT 渲染的选中边 / 批次
  - 单步试运行：mock 骨架、JSON 入参解析、结果摘要
  - 新建 / 复制工作流名校验、最近可续跑执行挑选
  - client：请求方法 / 路径 / 请求体

运行方式：
    PYTHONPATH=src python3 -m pytest tests/test_workflow_editor_m5_graph.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "apps" / "mini_agent_kanban"))

import client as client_module  # noqa: E402
import workflow_editor as W  # noqa: E402
from client import AgentClient  # noqa: E402


def draft3() -> dict:
    """a → b → c，另有独立的 d（与 a 同批）。"""
    return {
        "name": "demo",
        "steps": [
            {"id": "a", "name": "A", "prompt": "第一步"},
            {"id": "b", "name": "B", "prompt": "第二步 {a.output}", "depends_on": ["a"]},
            {"id": "c", "name": "C", "prompt": "第三步 {b.output}", "depends_on": ["b"]},
            {"id": "d", "name": "D", "prompt": "独立步骤"},
        ],
    }


def E(src: str, dst: str, eid: str | None = None) -> dict:
    return {"id": eid or f"{src}->{dst}", "source": src, "target": dst}


def edges_of(draft: dict) -> list[dict]:
    return [E(a, b) for a, b in W.dependency_edges(draft)]


# ═══════════════════════════════════════════════════════════════════════════
# 画布边 → 依赖对
# ═══════════════════════════════════════════════════════════════════════════

def test_canvas_depends_pairs_filters_merge_stale_and_duplicates():
    edges = [
        E("a", "b"),
        E("a", "b", "st-flow-edge_a-b"),          # 用户又拖了一条重复的 → 去重
        E("b", "c", "b~>c"),                       # merge 虚线边：只读，不算依赖
        E("a", "gone"),                            # 指向已删节点（陈旧回传）→ 丢弃
        {"id": "x", "source": "", "target": "c"},  # 残缺边 → 丢弃
    ]
    assert W.canvas_depends_pairs(edges, ["a", "b", "c"]) == [("a", "b")]


def test_canvas_depends_pairs_accepts_objects_with_attributes():
    obj = SimpleNamespace(id="a->b", source="a", target="b")
    assert W.canvas_depends_pairs([obj]) == [("a", "b")]


def test_is_merge_edge_id():
    assert W.is_merge_edge_id("a~>b")
    assert not W.is_merge_edge_id("a->b")
    assert not W.is_merge_edge_id("st-flow-edge_a-b")


def test_graph_edge_ids_match_conventions():
    d = draft3()
    d["steps"][2]["merge_sources"] = ["d"]
    g = W.draft_to_graph(d)
    ids = {e["id"] for e in g["edges"]}
    assert "a->b" in ids and "d~>c" in ids
    assert all(W.is_merge_edge_id(e["id"]) == (e["kind"] == "merge") for e in g["edges"])


# ═══════════════════════════════════════════════════════════════════════════
# 相对「最近发送集合」求差
# ═══════════════════════════════════════════════════════════════════════════

def test_push_sent_history_dedupes_and_caps():
    h = W.push_sent_history(None, [("a", "b")])
    h = W.push_sent_history(h, [("a", "b")])
    assert len(h) == 1
    for i in range(10):
        h = W.push_sent_history(h, [("a", f"n{i}")])
    assert len(h) == W.SENT_HISTORY_KEEP
    assert h[-1] == frozenset({("a", "n9")})


def test_detect_edits_echo_is_no_edit():
    hist = [frozenset({("a", "b"), ("b", "c")})]
    assert W.detect_canvas_edits([("a", "b"), ("b", "c")], hist) == ([], [])


def test_detect_edits_draw_and_delete():
    hist = [frozenset({("a", "b"), ("b", "c")})]
    added, removed = W.detect_canvas_edits([("a", "b"), ("b", "c"), ("a", "c")], hist)
    assert (added, removed) == ([("a", "c")], [])
    added, removed = W.detect_canvas_edits([("a", "b")], hist)
    assert (added, removed) == ([], [("b", "c")])


def test_detect_edits_picks_closest_history_entry():
    old = frozenset({("a", "b")})
    new = frozenset({("a", "b"), ("b", "c")})
    # 回传等于较旧的一份 → 是前端还没来得及刷新的回显，不是「用户删了 b→c」
    assert W.detect_canvas_edits([("a", "b")], [old, new]) == ([], [])


def test_detect_edits_without_history_is_conservative():
    assert W.detect_canvas_edits([("a", "b")], []) == ([], [])


def test_detect_edits_ignores_history_edges_of_deleted_nodes():
    hist = [frozenset({("a", "b"), ("b", "gone")})]
    # 回传已不含 gone 节点的边；按 node_ids 过滤基准后二者一致
    assert W.detect_canvas_edits([("a", "b")], hist, node_ids=["a", "b"]) == ([], [])


# ═══════════════════════════════════════════════════════════════════════════
# 应用到草稿
# ═══════════════════════════════════════════════════════════════════════════

def test_sync_draw_edge_adds_dependency():
    d = draft3()
    hist = W.push_sent_history(None, W.dependency_edges(d))
    canvas = edges_of(d) + [E("d", "c", "st-flow-edge_d-c")]
    res = W.sync_canvas_edges(d, canvas, hist)
    assert res.changed and res.added == [("d", "c")] and not res.errors
    assert "d" in W.deps_of(W.find_step(res.draft, "c"))
    assert "d" not in W.deps_of(W.find_step(d, "c"))          # 入参不被修改


def test_sync_delete_edge_removes_dependency_and_cleans_empty_key():
    d = draft3()
    hist = W.push_sent_history(None, W.dependency_edges(d))
    canvas = [e for e in edges_of(d) if (e["source"], e["target"]) != ("a", "b")]
    res = W.sync_canvas_edges(d, canvas, hist)
    assert res.removed == [("a", "b")]
    assert "depends_on" not in W.find_step(res.draft, "b")


def test_sync_rejects_cycle_but_applies_the_rest():
    d = draft3()
    hist = W.push_sent_history(None, W.dependency_edges(d))
    canvas = edges_of(d) + [E("c", "a", "st-flow-edge_c-a"),   # a→b→c 再 c→a：成环
                            E("d", "c", "st-flow-edge_d-c")]    # 合法
    res = W.sync_canvas_edges(d, canvas, hist)
    assert res.added == [("d", "c")]
    assert len(res.errors) == 1 and "循环" in res.errors[0] and res.errors[0].startswith("c → a")
    assert res.needs_canvas_reset
    assert "c" not in W.deps_of(W.find_step(res.draft, "a"))


def test_sync_rejects_self_dependency():
    d = draft3()
    hist = W.push_sent_history(None, W.dependency_edges(d))
    res = W.sync_canvas_edges(d, edges_of(d) + [E("a", "a", "st-flow-edge_a-a")], hist)
    assert res.errors and not res.changed


def test_sync_stale_echo_does_not_delete_freshly_added_dependency():
    """回归（最关键）：面板刚给 c 加了依赖 d，草稿已变；这一轮组件回传的还是**上一轮**的边集。
    直接拿回传和当前草稿求差会把新依赖当成「用户在画布上删了」而误删。"""
    d_old = draft3()
    d_new, err = W.add_dependency(d_old, "d", "c")
    assert err is None
    hist = W.push_sent_history(W.push_sent_history(None, W.dependency_edges(d_old)),
                               W.dependency_edges(d_new))
    stale_canvas = edges_of(d_old)                              # 陈旧回传 = 旧边集
    res = W.sync_canvas_edges(d_new, stale_canvas, hist)
    assert not res.changed and not res.errors
    assert "d" in W.deps_of(W.find_step(res.draft, "c"))       # 新依赖还在


def test_sync_ignores_noop_edits_already_reflected_in_draft():
    d = draft3()
    hist = W.push_sent_history(None, W.dependency_edges(d))
    # 用户删了 a→b，但草稿此前已经被面板删过它了 → 空操作，不报错
    d2 = W.remove_dependency(d, "a", "b")
    canvas = [e for e in edges_of(d) if (e["source"], e["target"]) != ("a", "b")]
    res = W.sync_canvas_edges(d2, canvas, hist)
    assert not res.changed and not res.errors and res.ignored == [("a", "b")]


def test_sync_merge_edges_are_read_only():
    d = draft3()
    d["steps"][2]["merge_sources"] = ["d"]
    g = W.draft_to_graph(d)
    hist = W.push_sent_history(None, W.dependency_edges(d))
    # 画布回传里 merge 虚线边被删掉了 → 不影响 merge_sources
    canvas = [e for e in g["edges"] if e["kind"] == "depends"]
    res = W.sync_canvas_edges(d, canvas, hist)
    assert not res.changed
    assert W.find_step(res.draft, "c")["merge_sources"] == ["d"]


# ═══════════════════════════════════════════════════════════════════════════
# 选中解析 / 边选择器
# ═══════════════════════════════════════════════════════════════════════════

def test_resolve_canvas_selection():
    edges = [E("a", "b"), E("d", "c", "d~>c")]
    ids = ["a", "b", "c", "d"]
    assert W.resolve_canvas_selection("b", ids, edges) == ("b", None)
    assert W.resolve_canvas_selection("a->b", ids, edges) == (None, ("a", "b", "depends"))
    assert W.resolve_canvas_selection("d~>c", ids, edges) == (None, ("d", "c", "merge"))
    assert W.resolve_canvas_selection("zzz", ids, edges) == (None, None)
    assert W.resolve_canvas_selection(None, ids, edges) == (None, None)


def test_edge_choices_and_label():
    d = draft3()
    assert W.edge_choices(d) == [("a", "b"), ("b", "c")]
    assert W.edge_label(("a", "b")) == "a → b"
    d["steps"][1]["depends_on"] = ["a", "ghost"]                # 悬空依赖不进选择器
    assert ("ghost", "b") not in W.edge_choices(d)


def test_insert_between_via_edge_choice_roundtrip():
    d = draft3()
    src, dst = W.edge_choices(d)[0]
    d2, new_id = W.insert_between(d, src, dst, "agent")
    assert new_id and W.deps_of(W.find_step(d2, new_id)) == ["a"]
    assert W.deps_of(W.find_step(d2, "b")) == [new_id]


# ═══════════════════════════════════════════════════════════════════════════
# 并行批次
# ═══════════════════════════════════════════════════════════════════════════

def test_batch_summary_local_layers():
    s = W.batch_summary(draft3())
    assert [x["ids"] for x in s] == [["a", "d"], ["b"], ["c"]]
    assert s[0]["will_run_concurrently"] and "可并发" in s[0]["text"]
    assert not s[1]["will_run_concurrently"]


def test_batch_summary_allow_parallel_false_runs_serial():
    d = draft3()
    d["steps"][3]["allow_parallel"] = False
    s = W.batch_summary(d)
    assert s[0]["serial_ids"] == ["d"] and s[0]["concurrent_ids"] == ["a"]
    assert not s[0]["will_run_concurrently"] and "allow_parallel=false" in s[0]["text"]


def test_batch_summary_inherits_defaults_and_explicit_true_wins():
    d = draft3()
    d["defaults"] = {"allow_parallel": False}
    assert W.batch_summary(d)[0]["serial_ids"] == ["a", "d"]
    d["steps"][0]["allow_parallel"] = True
    d["steps"][3]["allow_parallel"] = True
    assert W.batch_summary(d)[0]["concurrent_ids"] == ["a", "d"]


def test_batch_summary_prefers_backend_batches():
    s = W.batch_summary(draft3(), [["a"], ["b", "d"], ["c"]])
    assert [x["ids"] for x in s] == [["a"], ["b", "d"], ["c"]]


def test_apply_batch_highlight_marks_and_does_not_mutate():
    d = draft3()
    g = W.draft_to_graph(d, batches=W.compute_layers(d))
    h = W.apply_batch_highlight(g, 0)
    by = {n["id"]: n for n in h["nodes"]}
    assert by["a"]["highlighted"] and by["d"]["highlighted"]
    assert by["b"]["dimmed"] and by["c"]["dimmed"]
    assert "highlighted" not in g["nodes"][0]                   # 原图不变


def test_apply_batch_highlight_none_or_unknown_is_noop():
    g = W.draft_to_graph(draft3(), batches=W.compute_layers(draft3()))
    assert W.apply_batch_highlight(g, None) is g
    assert W.apply_batch_highlight(g, 99) is g


def test_to_dot_highlight_selected_edge_and_batches():
    d = draft3()
    g = W.apply_batch_highlight(W.draft_to_graph(d, batches=W.compute_layers(d)), 0)
    dot = W.to_dot(g, selected="a", selected_edge=("b", "c"), show_batches=True)
    assert "批次 1" in dot and "批次 3" in dot
    assert W.HIGHLIGHT_BORDER in dot                             # d 被高亮（a 被选中，用黑框）
    assert "#9E9E9E" in dot                                      # 被淡化节点
    assert '"b" -> "c" [color="#E53935", penwidth=3]' in dot


def test_to_dot_default_output_unchanged_by_new_params():
    g = W.draft_to_graph(draft3())
    assert W.to_dot(g, "a") == W.to_dot(g, "a", None, False)
    assert "penwidth=3];" in W.to_dot(g, "a")


def test_to_dot_error_border_not_overridden_by_highlight():
    d = draft3()
    g = W.draft_to_graph(d, errors_by_step={"d": ["boom"]}, batches=W.compute_layers(d))
    dot = W.to_dot(W.apply_batch_highlight(g, 0))
    line = next(x for x in dot.splitlines() if x.strip().startswith('"d"'))
    assert W.ERROR_BORDER in line and W.HIGHLIGHT_BORDER not in line


# ═══════════════════════════════════════════════════════════════════════════
# 单步试运行
# ═══════════════════════════════════════════════════════════════════════════

def test_suggest_test_mocks_from_placeholders():
    step = {"id": "r", "prompt": "评审 {analyze.output}，评分 {check.score}，主题 {topic}，"
                                 '格式 {"a": 1}，第 {item_index} 项 {item}，文件 {gen.output_file}'}
    m = W.suggest_test_mocks(step)
    assert set(m["mock_step_results"]) == {"analyze", "check"}
    assert "output" in m["mock_step_results"]["analyze"]
    assert m["mock_step_results"]["check"] == {"score": 0.8}
    assert m["mock_inputs"] == {"topic": ""}
    assert m["unmockable"] == ["gen.output_file"]


def test_suggest_test_mocks_scans_nested_args_and_prompt_file_but_not_condition():
    step = {"id": "t", "type": "tool_call", "tool_name": "x", "condition": "up.passed",
            "tool_args": {"q": ["{a.output}"], "n": {"k": "{var1}"}}, "prompt_file": "p.md"}
    m = W.suggest_test_mocks(step, {"p.md": "正文 {from_file.output}"})
    assert set(m["mock_step_results"]) == {"a", "from_file"}
    assert m["mock_inputs"] == {"var1": ""}


def test_suggest_test_mocks_empty():
    m = W.suggest_test_mocks({"id": "x", "prompt": "没有占位符"})
    assert m == {"mock_step_results": {}, "mock_inputs": {}, "unmockable": []}


def test_parse_json_object():
    assert W.parse_json_object("  ") == (None, None)
    assert W.parse_json_object('{"a": 1}') == ({"a": 1}, None)
    obj, err = W.parse_json_object("{bad", "上游")
    assert obj is None and "上游" in err and "合法 JSON" in err
    obj, err = W.parse_json_object("[1]", "入参")
    assert obj is None and "对象" in err


def test_summarize_test_result_kinds():
    ok = W.summarize_test_result({"status": "done", "output": "结果", "duration_seconds": 1.5,
                                  "resolved_prompt_preview": "P", "skipped": False})
    assert ok["kind"] == "ok" and ok["output"] == "结果" and ok["prompt_preview"] == "P"
    bad = W.summarize_test_result({"status": "failed", "error": "炸了", "output": ""})
    assert bad["kind"] == "failed" and "failed" in bad["title"] and bad["error"] == "炸了"
    sk = W.summarize_test_result({"skipped": True, "reason": "human_input 不支持"})
    assert sk["kind"] == "skipped" and "human_input" in sk["error"]
    er = W.summarize_test_result({"_error": "任务失败"})
    assert er["kind"] == "error" and er["error"] == "任务失败"
    assert W.summarize_test_result(None)["kind"] == "error"


# ═══════════════════════════════════════════════════════════════════════════
# 新建 / 复制、续跑
# ═══════════════════════════════════════════════════════════════════════════

def test_validate_workflow_name():
    assert W.validate_workflow_name("my-flow_2", ["other"]) == (True, "")
    assert W.validate_workflow_name("研究流程", []) == (True, "")             # 中文字母允许（与后端 isalnum 同口径）
    assert W.validate_workflow_name("  ", [])[0] is False
    ok, msg = W.validate_workflow_name("a b/c", [])
    assert not ok and "非法字符" in msg
    ok, msg = W.validate_workflow_name("demo", ["demo"])
    assert not ok and "已存在" in msg
    assert not W.validate_workflow_name("x" * 65, [])[0]


def test_pick_resumable_run_latest_and_filters():
    runs = [
        {"workflow_session_id": "r1", "workflow_name": "demo", "status": "failed", "started_at": 100},
        {"workflow_session_id": "r2", "workflow_name": "demo", "status": "partial", "started_at": 300},
        {"workflow_session_id": "r3", "workflow_name": "demo", "status": "done", "started_at": 400},
        {"workflow_session_id": "r4", "workflow_name": "other", "status": "failed", "started_at": 500},
        {"workflow_session_id": "r5", "workflow_name": "demo", "status": "running", "started_at": 600,
         "is_stale": True},
        {"workflow_session_id": "r6", "workflow_name": "demo", "status": "cancelled", "started_at": 200},
    ]
    assert W.pick_resumable_run(runs, "demo")["workflow_session_id"] == "r2"
    assert W.pick_resumable_run(runs, "nope") is None
    assert W.pick_resumable_run([], "demo") is None
    assert W.pick_resumable_run([runs[2], runs[4]], "demo") is None       # done / 孤儿 running 都不算


# ═══════════════════════════════════════════════════════════════════════════
# client
# ═══════════════════════════════════════════════════════════════════════════

def _resp(status=200, json_body=None, text=""):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = json_body
    r.text = text
    return r


def test_client_create_workflow_request_and_conflict_shape(monkeypatch):
    req = MagicMock(return_value=_resp(200, {"status": "created"}))
    monkeypatch.setattr(client_module._HTTP, "request", req)
    c = AgentClient("http://localhost:8000", "tok")
    c.create_workflow("new_flow")
    args, kw = req.call_args
    assert args[0] == "POST" and args[1].endswith("/workflows")
    assert kw["json"] == {"name": "new_flow"}                       # 未复制时不带 copy_from
    c.create_workflow("copy_flow", copy_from="demo")
    assert req.call_args[1]["json"] == {"name": "copy_flow", "copy_from": "demo"}

    detail = {"message": "已存在同名工作流", "code": "already_exists"}
    monkeypatch.setattr(client_module._HTTP, "request", MagicMock(return_value=_resp(409, {"detail": detail})))
    err = W.parse_editor_error(c.create_workflow("demo"))
    assert err.status == 409 and err.code == "already_exists" and "同名" in err.message


def test_client_test_workflow_step_uses_post_and_omits_empty_fields(monkeypatch):
    post = MagicMock(return_value=_resp(200, {"job_id": "j1", "key": "k"}))
    monkeypatch.setattr(client_module._HTTP, "post", post)
    c = AgentClient("http://localhost:8000", "tok")
    assert c.test_workflow_step("demo", "a") == {"job_id": "j1", "key": "k"}
    args, kw = post.call_args
    assert args[0].endswith("/workflows/demo/steps/a/test") and kw["json"] == {}
    c.test_workflow_step("demo", "a", {"up": {"output": "x"}}, {"topic": "t"}, 30)
    assert post.call_args[1]["json"] == {"mock_step_results": {"up": {"output": "x"}},
                                         "mock_inputs": {"topic": "t"}, "timeout_override": 30}


def test_client_backups_methods(monkeypatch):
    req = MagicMock(return_value=_resp(200, {"backups": []}))
    monkeypatch.setattr(client_module._HTTP, "request", req)
    c = AgentClient("http://localhost:8000", "tok")
    c.workflow_backups("demo")
    args, _kw = req.call_args
    assert args[0] == "GET" and args[1].endswith("/workflows/demo/backups")
    c.restore_workflow_backup("demo", "20260101-000000-000")
    args, kw = req.call_args
    assert args[0] == "POST" and args[1].endswith("/workflows/demo/backups/20260101-000000-000/restore")
    assert kw["json"] == {}
    c.restore_workflow_backup("demo", "20260101-000000-000", "hh")
    assert req.call_args[1]["json"] == {"base_hash": "hh"}
