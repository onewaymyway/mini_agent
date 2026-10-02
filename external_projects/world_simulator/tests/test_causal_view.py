"""tests/test_causal_view.py — WP3 P5e（第二十二轮 / 阶段 P5e）：因果引擎界面收尾（3e）。

`causal_view.py` 是纯函数视图层：边状态（假设/已观察/已证伪/未定/已停用）、着色 DOT、到期因果时间线。
状态完全由 **AI 自报**的兑现统计推出，所以这里重点验证：阈值边界、不被单次自报带偏、
树边/已删除声明的树边、DOT 转义与着色、时间线排序与精度降级，以及 `app.py` 的两个渲染函数不抛。
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from world_simulator import causal_view as cv
from world_simulator import tree_effects as te
from world_simulator.state_model import SimState


def _stat(realized=0, dampened=0, countered=0):
    concluded = realized + dampened + countered
    return {"queued": concluded, "realized": realized, "dampened": dampened, "countered": countered,
            "postponed": 0, "expired": 0, "unaddressed": 0, "concluded": concluded,
            "realized_rate": round(realized / concluded, 3) if concluded else None}


def _disp(edge_id, disposition, *, frm="econ", to="industry", reason="", closed=True, auto=False, tri=1):
    return {"pending_id": f"{edge_id}@{tri}", "edge_id": edge_id, "from_line_id": frm, "to_line_id": to,
            "disposition": disposition, "reason": reason, "closed": closed, "auto": auto,
            "triggered_at_step": tri}


def _hist(*steps):
    """`steps` 里每项 `(step, [dispositions], elapsed_days)`。"""
    out = []
    for n, disps, elapsed in steps:
        out.append(SimState(step=n, summary="x", vars={}, effect_dispositions=list(disps), elapsed_days=elapsed))
    return out


def _settings(*edges, **extra):
    s = {"causal_engine_enabled": True, "declared_causal_graph": list(edges)}
    s.update(extra)
    return s


def _edge(src="econ", dst="industry", **extra):
    e = {"from_line_id": src, "to_line_id": dst}
    e.update(extra)
    return e


# ── 状态分类 ────────────────────────────────────────────────────────


def test_never_triggered_or_no_conclusion_is_hypothesis():
    assert cv.classify_edge(None) == cv.STATUS_HYPOTHESIS
    assert cv.classify_edge(_stat()) == cv.STATUS_HYPOTHESIS
    # 只有 postponed / 自动结案：没有"有结论"的处置，仍是假设
    s = _stat()
    s.update(postponed=2, expired=1, queued=3)
    assert cv.classify_edge(s) == cv.STATUS_HYPOTHESIS


def test_observed_needs_a_realization_and_a_majority():
    assert cv.classify_edge(_stat(realized=1)) == cv.STATUS_OBSERVED
    assert cv.classify_edge(_stat(realized=2, countered=2)) == cv.STATUS_OBSERVED  # 刚好 50%
    assert cv.classify_edge(_stat(realized=1, countered=2)) == cv.STATUS_CONTESTED  # 33%
    assert cv.classify_edge(_stat(realized=3, dampened=1)) == cv.STATUS_OBSERVED


def test_refuted_needs_repeated_self_reported_counters_and_no_realization():
    assert cv.classify_edge(_stat(countered=2)) == cv.STATUS_REFUTED
    assert cv.classify_edge(_stat(countered=5)) == cv.STATUS_REFUTED
    # 单次抵消证据太薄，不判证伪
    assert cv.classify_edge(_stat(countered=1)) == cv.STATUS_CONTESTED
    # 只有一次兑现就不能算证伪，哪怕抵消很多
    assert cv.classify_edge(_stat(realized=1, countered=4)) == cv.STATUS_CONTESTED


def test_dampened_only_is_not_a_refutation():
    """部分兑现 = 效果发生了但更弱，不是证伪；也还不够判"已观察"。"""
    assert cv.classify_edge(_stat(dampened=3)) == cv.STATUS_CONTESTED


def test_min_realized_threshold_is_independent_of_the_rate_threshold():
    """默认 min_realized=1 时被 rate 阈值遮住；调高后单独起作用。"""
    p = cv.get_view_params({"causal_view_params": {"observed_min_realized": 2}})
    assert cv.classify_edge(_stat(realized=1), params=p) == cv.STATUS_CONTESTED  # 100% 但只有 1 次
    assert cv.classify_edge(_stat(realized=2), params=p) == cv.STATUS_OBSERVED


def test_disabled_wins_over_everything():
    assert cv.classify_edge(_stat(realized=5), enabled=False) == cv.STATUS_DISABLED
    assert cv.classify_edge(None, enabled=False) == cv.STATUS_DISABLED


def test_params_override_and_invalid_values_ignored():
    p = cv.get_view_params({"causal_view_params": {"refuted_min_countered": 3, "observed_min_rate": 0.8}})
    assert p["refuted_min_countered"] == 3 and p["observed_min_rate"] == 0.8
    assert cv.classify_edge(_stat(countered=2), params=p) == cv.STATUS_CONTESTED
    assert cv.classify_edge(_stat(realized=3, countered=1), params=p) == cv.STATUS_CONTESTED  # 75% < 80%
    bad = cv.get_view_params({"causal_view_params": {
        "refuted_min_countered": 0, "observed_min_rate": 1.5, "recent_limit": "x", "observed_min_realized": True}})
    assert bad == cv.DEFAULT_VIEW_PARAMS
    assert cv.get_view_params(None) == cv.DEFAULT_VIEW_PARAMS
    assert cv.get_view_params({"causal_view_params": "nope"}) == cv.DEFAULT_VIEW_PARAMS


# ── 边视图 ──────────────────────────────────────────────────────────


def test_edge_view_covers_declared_edges_in_order_with_stats_and_status():
    s = _settings(_edge("a", "b"), _edge("b", "c", enabled=False), _edge("c", "d", mechanism="降息"))
    hist = _hist((2, [_disp("a->b", "realized", frm="a", to="b"), _disp("c->d", "countered", frm="c", to="d")], None),
                 (3, [_disp("c->d", "countered", frm="c", to="d")], None))
    rows = cv.build_edge_view(s, hist)
    assert [r["edge_id"] for r in rows] == ["a->b", "b->c", "c->d"]
    by = {r["edge_id"]: r for r in rows}
    assert by["a->b"]["status"] == cv.STATUS_OBSERVED and by["a->b"]["rate_text"] == "100%（1/1）"
    assert by["b->c"]["status"] == cv.STATUS_DISABLED and by["b->c"]["rate_text"] == ""
    assert by["c->d"]["status"] == cv.STATUS_REFUTED and by["c->d"]["mechanism"] == "降息"
    assert all(r["kind"] == "declared" for r in rows)


def test_edge_view_uses_display_names_and_falls_back_to_ids():
    s = _settings(_edge("econ", "ghost"), causal_lines=[{"id": "econ", "label": "经济线"}])
    row = cv.build_edge_view(s, [])[0]
    assert row["from_name"] == "经济线" and row["to_name"] == "ghost" and row["status"] == cv.STATUS_HYPOTHESIS


def test_edge_view_includes_declared_tree_edges_and_orphaned_tree_history():
    line = {"id": "econ", "label": "经济线", "future_tree": {"branches": [
        {"id": "b1", "status": "emerging", "effects_if_active": [{"to_line_id": "industry", "mechanism": "m"}]}]}}
    s = _settings(causal_lines=[line])
    declared_id = te.edge_id_for("econ", "b1", "industry")
    orphan_id = te.edge_id_for("econ", "gone", "industry")
    hist = _hist((2, [_disp(declared_id, "realized", frm="econ"), _disp(orphan_id, "countered", frm="econ"),
                      _disp(orphan_id, "countered", frm="econ")], None))
    rows = cv.build_edge_view(s, hist)
    by = {r["edge_id"]: r for r in rows}
    assert by[declared_id]["kind"] == "tree" and by[declared_id]["from_id"] == "econ/b1"
    assert by[declared_id]["from_name"] == "经济线/b1" and by[declared_id]["status"] == cv.STATUS_OBSERVED
    # 声明已被删掉但历史里有兑现记录：仍然画出来，免得记录凭空消失
    assert by[orphan_id]["kind"] == "tree" and by[orphan_id]["status"] == cv.STATUS_REFUTED
    assert by[orphan_id]["from_id"] == "econ/gone" and by[orphan_id]["to_id"] == "industry"


def test_edge_view_is_empty_without_edges_and_never_mutates_inputs():
    assert cv.build_edge_view(None, []) == [] and cv.build_edge_view({}, None) == []
    s = _settings(_edge("a", "b"))
    before = repr(s)
    cv.build_edge_view(s, _hist((2, [_disp("a->b", "realized", frm="a", to="b")], None)))
    assert repr(s) == before


# ── DOT ─────────────────────────────────────────────────────────────


def test_dot_colors_each_status_and_labels_rate():
    s = _settings(_edge("a", "b"), _edge("c", "d"), _edge("e", "f"), _edge("g", "h"), _edge("i", "j", enabled=False))
    hist = _hist((2, [
        _disp("a->b", "realized", frm="a", to="b"),
        _disp("c->d", "countered", frm="c", to="d"), _disp("c->d", "countered", frm="c", to="d"),
        _disp("e->f", "realized", frm="e", to="f"), _disp("e->f", "countered", frm="e", to="f"),
        _disp("e->f", "countered", frm="e", to="f"),
    ], None))
    dot = cv.edges_to_dot(cv.build_edge_view(s, hist))
    assert dot.startswith("digraph G {") and dot.endswith("}")
    line = {ln.split(" [")[0].strip(): ln for ln in dot.splitlines() if "->" in ln}
    assert 'color="#16a34a"' in line['"a" -> "b"'] and "penwidth=2" in line['"a" -> "b"']
    assert "已观察 · 100%（1/1）" in line['"a" -> "b"']
    assert 'color="#dc2626"' in line['"c" -> "d"'] and "已证伪 · 0%（0/2）" in line['"c" -> "d"']
    assert 'color="#d97706"' in line['"e" -> "f"'] and "未定" in line['"e" -> "f"']
    assert 'color="#94a3b8"' in line['"g" -> "h"'] and 'style="dashed"' in line['"g" -> "h"']
    assert "假设" in line['"g" -> "h"'] and "·" not in line['"g" -> "h"']
    assert 'color="#cbd5e1"' in line['"i" -> "j"'] and 'style="dotted"' in line['"i" -> "j"']


def test_dot_empty_escapes_and_marks_tree_nodes():
    assert "->" not in cv.edges_to_dot([]) and cv.edges_to_dot([]).startswith("digraph G {")
    s = _settings(_edge('we"ird', "b\\x"), causal_lines=[{
        "id": "econ", "future_tree": {"branches": [
            {"id": "b1", "effects_if_active": [{"to_line_id": "industry"}]}]}}])
    dot = cv.edges_to_dot(cv.build_edge_view(s, []))
    assert '"we\\"ird" -> "b\\\\x"' in dot
    assert '"econ/b1" [label="econ/b1", shape=ellipse' in dot  # 树分支节点用椭圆区分
    assert "shape=ellipse" not in dot.split('"industry"')[1].split(";")[0]


# ── 到期时间线 ──────────────────────────────────────────────────────


def _pend(edge_id="a->b", step=1, **extra):
    p = {"pending_id": f"{edge_id}@{step}", "edge_id": edge_id, "from_line_id": edge_id.split("->")[0],
         "to_line_id": edge_id.split("->")[-1], "triggered_at_step": step, "delay_days": None, "delay_steps": 0,
         "retrigger_count": 0, "postponed_count": 0, "ignored_count": 0, "trigger_reason": "line_advanced"}
    p.update(extra)
    return p


def test_timeline_sorts_due_first_then_by_remaining_days():
    pending = [
        _pend("a->b", 1, delay_days=100), _pend("c->d", 1, delay_days=30),
        _pend("e->f", 1), _pend("g->h", 1, ignored_count=2),
    ]
    s = _settings(causal_pending=pending)
    hist = _hist((1, [], 10), (2, [], 10))  # 触发于第 1 步；第 2 步过去 10 天
    tl = cv.build_due_timeline(s, hist, step=3)
    ids = [r["pending_id"] for r in tl["open"]]
    # e->f(无延迟)与 g->h 已到期，g->h 被忽略过更靠前；然后 30 天的，再 100 天的
    assert ids == ["g->h@1", "e->f@1", "c->d@1", "a->b@1"]
    by = {r["pending_id"]: r for r in tl["open"]}
    assert by["g->h@1"]["due"] and by["g->h@1"]["text"].startswith("已到期")
    assert by["c->d@1"]["precision"] == "days" and by["c->d@1"]["remaining"] == 20
    assert by["c->d@1"]["text"] == "还差约 20 天" and by["a->b@1"]["remaining"] == 90
    assert by["a->b@1"]["reason_label"] == "源头线有进展"


def test_timeline_puts_day_precision_before_step_precision_regardless_of_magnitude():
    """"还差 2 步"和"还差 20 天"量纲不同，不能直接比大小：天精度的排在前面。"""
    pending = [_pend("e->f", 1, delay_steps=3), _pend("c->d", 1, delay_days=30)]
    hist = _hist((1, [], 10), (2, [], 10))
    ids = [r["pending_id"] for r in cv.build_due_timeline(_settings(causal_pending=pending), hist, step=3)["open"]]
    assert ids == ["c->d@1", "e->f@1"]


def test_timeline_degrades_to_steps_when_elapsed_is_missing():
    s = _settings(causal_pending=[_pend("a->b", 1, delay_days=30, delay_steps=4)])
    hist = _hist((1, [], 5), (2, [], None), (3, [], 5))  # 区间里第 2 步缺 elapsed_days
    row = cv.build_due_timeline(s, hist, step=4)["open"][0]
    assert row["precision"] == "steps" and row["due"] is False
    assert row["remaining"] == 2 and "按步计" in row["text"]  # delay_steps 4，已过 2 步


def test_timeline_open_is_empty_when_engine_off_but_recent_still_reads_history():
    pending = [_pend()]
    off = {"declared_causal_graph": [], "causal_pending": pending}
    hist = _hist((2, [_disp("a->b", "realized", frm="a", to="b")], None))
    tl = cv.build_due_timeline(off, hist, step=3)
    assert tl["open"] == [] and len(tl["recent"]) == 1


def test_timeline_recent_is_newest_first_and_capped():
    hist = _hist(*[(n, [_disp("a->b", "realized", frm="a", to="b", reason=f"r{n}")], None) for n in range(1, 31)])
    tl = cv.build_due_timeline(_settings(), hist, step=31)
    assert len(tl["recent"]) == 20 and [r["step"] for r in tl["recent"]][:3] == [30, 29, 28]
    tl5 = cv.build_due_timeline(_settings(causal_view_params={"recent_limit": 5}), hist, step=31)
    assert len(tl5["recent"]) == 5
    # 同一步里的多条处置也是倒序，且保留 reason/auto/closed
    two = _hist((4, [_disp("a->b", "countered", reason="first"),
                     _disp("c->d", "expired", frm="c", to="d", reason="自动", auto=True)], None))
    rec = cv.build_due_timeline(_settings(), two, step=5)["recent"]
    assert [r["edge_id"] for r in rec] == ["c->d", "a->b"] and rec[0]["auto"] is True and rec[1]["reason"] == "first"


def test_timeline_tree_rows_show_line_slash_branch():
    eid = te.edge_id_for("econ", "b1", "industry")
    entry = _pend(eid, 1, from_line_id="econ", to_line_id="industry", source="tree", branch_id="b1",
                  trigger_reason="tree_effect")
    s = _settings(causal_pending=[entry], causal_lines=[{"id": "econ", "label": "经济线"}])
    hist = _hist((2, [_disp(eid, "dampened", frm="econ", reason="被政策压住")], None))
    tl = cv.build_due_timeline(s, hist, step=3)
    assert tl["open"][0]["from_name"] == "经济线/b1" and tl["open"][0]["source"] == "tree"
    assert tl["recent"][0]["from_name"] == "经济线/b1" and tl["recent"][0]["disposition"] == "dampened"


def test_timeline_tolerates_garbage_entries():
    s = _settings(causal_pending=["x", None, _pend()])
    hist = _hist((2, [], None))
    hist[0].effect_dispositions = ["bad", None, _disp("a->b", "realized", frm="a", to="b")]
    tl = cv.build_due_timeline(s, hist, step=3)
    assert len(tl["open"]) == 1 and len(tl["recent"]) == 1


# ── app.py 渲染（用记录型 st 桩，不需要 Streamlit 运行时）────────────────


class _FakeSt:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        def _record(*args, **kwargs):
            self.calls.append((name, args, kwargs))
        return _record

    def text(self, kind):
        return " ".join(str(a[0]) for n, a, _ in self.calls if n == kind and a)


def test_app_status_graph_renders_dot_and_rows(monkeypatch):
    import app
    fake = _FakeSt()
    monkeypatch.setattr(app, "st", fake)
    s = _settings(_edge("a", "b"))
    hist = _hist((2, [_disp("a->b", "realized", frm="a", to="b")], None))
    app._render_causal_status_graph(s, hist)
    dots = [a[0] for n, a, _ in fake.calls if n == "graphviz_chart"]
    assert len(dots) == 1 and 'color="#16a34a"' in dots[0]
    assert "已观察" in fake.text("markdown") and "AI 自报" in fake.text("caption")


def test_app_status_graph_empty_and_timeline_render_without_error(monkeypatch):
    import app
    fake = _FakeSt()
    monkeypatch.setattr(app, "st", fake)
    app._render_causal_status_graph(_settings(), [])
    assert not [c for c in fake.calls if c[0] == "graphviz_chart"] and "还没有可画的边" in fake.text("caption")
    fake.calls.clear()
    s = _settings(causal_pending=[_pend("a->b", 1, postponed_count=1)])
    hist = _hist((2, [_disp("a->b", "countered", frm="a", to="b", reason="政策")], None))
    app._render_causal_due_timeline(s, hist, 3)
    md = fake.text("markdown")
    assert "⏰" in md and "推迟 1 次" in md and "❌" in md and "政策" in md
    fake.calls.clear()
    app._render_causal_due_timeline(_settings(), [], 1)
    assert "当前没有未结案" in fake.text("caption") and "还没有处置记录" in fake.text("caption")
