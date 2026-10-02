"""tests/test_html_export_mechanisms.py — P10：静态 HTML 导出接入第二十二轮新机制
（`world_simulator/html_export_mechanisms.py`）。

覆盖：无数据不输出（旧实例导出不变）/ 每步提示 / 体检 / 技术树 / 事件先验（未确认标记）/
因果引擎（着色图、到期时间线、树声明）/ 树接地 / 转义 / 按分支取动态状态 / 区块失败降级。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from world_simulator import html_export as he
from world_simulator import html_export_mechanisms as hm
from world_simulator.state_model import SimManifest, SimState
from world_simulator.store import SimStore, now_iso

SIM = "sim_p10"
MARKERS = ("ws-mech-", "真实性体检", "技术发展模型", "外生事件先验", "因果引擎（导出", "因果树接地")


def _manifest(**settings) -> SimManifest:
    return SimManifest(
        sim_id=SIM, template="life_sim", intent="意图", title="标题",
        created_at=now_iso(), updated_at=now_iso(), settings=dict(settings),
    )


def _seed(tmp_path, settings, states, branch="main", manifest_branch="main"):
    store = SimStore.for_root(tmp_path, SIM)
    m = _manifest(**settings)
    m.branch = manifest_branch
    store.save_manifest(m)
    for s in states:
        store.append_state(s, branch)
    return store


def _s(step, **kw):
    kw.setdefault("summary", f"第{step}步")
    kw.setdefault("vars", {})
    return SimState(step=step, **kw)


def _node(tid="t1", stage="lab", progress=0.4, **extra):
    n = {"id": tid, "name": tid, "stage": stage, "progress": progress,
         "typical_dwell_days": {k: 100 for k in ("lab", "expert", "developer", "consumer", "cheap_at_scale", "infrastructure")},
         "dwell_source": "user"}
    n.update(extra)
    return n


# ── 无数据：不输出任何新内容 ─────────────────────────────────────────


def test_no_mechanism_data_renders_nothing_new(tmp_path):
    _seed(tmp_path, {}, [_s(0), _s(1)])
    html = he.export_simulation_html(tmp_path, SIM)
    for marker in MARKERS:
        assert marker not in html
    assert hm.EXTRA_CSS not in html


def test_enabled_switches_without_data_render_nothing(tmp_path):
    """开关开着但没有节点/先验/边：不应出现空壳区块。"""
    _seed(tmp_path, {"tech_model_enabled": True, "event_sampling_enabled": True,
                     "causal_engine_enabled": True, "tree_grounding_enabled": True}, [_s(0), _s(1)])
    html = he.export_simulation_html(tmp_path, SIM)
    for marker in ("技术发展模型", "外生事件先验", "因果引擎（导出", "因果树接地：当前"):
        assert marker not in html


# ── 每步提示 ─────────────────────────────────────────────────────────


def test_step_notes_cover_all_mechanisms_and_load_css(tmp_path):
    st = _s(
        1,
        consistency_warnings=[{"code": "C1", "message": "技术跳级"}],
        tech_violations=[{"code": "T4", "message": "提前迁移被驳回"}],
        tech_repair={"status": "accepted", "violations_before": [{"code": "T4", "message": "旧"}]},
        sampled_events=[{"id": "e1", "description": "金融危机", "severity": "high", "probability": 0.12,
                         "verified": False, "confirmed": False}],
        causal_queued=[{"action": "queued", "edge_id": "a->b", "trigger_reason": "line_progress"}],
        effect_dispositions=[{"edge_id": "a->b", "disposition": "realized", "reason": "增长了"}],
        causal_violations=[{"code": "E2", "message": "未知 pending"}],
        tree_grounding=[{"action": "downgraded", "code": "G1", "message": "前置未满足"}],
    )
    _seed(tmp_path, {}, [_s(0), st])
    html = he.export_simulation_html(tmp_path, SIM)
    for needle in ("一致性提示 C1", "技术模型 T4", "技术违规修复调用", "外生事件：金融危机", "先验未核对",
                   "AI 提议、用户未确认", "因果入队：a-&gt;b", "因果交代 a-&gt;b", "AI 自报", "因果引擎 E2", "树接地 G1"):
        assert needle in html, needle
    assert hm.EXTRA_CSS in html


def test_step_notes_escape_free_text(tmp_path):
    st = _s(1, consistency_warnings=[{"code": "C1", "message": "<script>alert(1)</script>"}])
    _seed(tmp_path, {}, [_s(0), st])
    html = he.export_simulation_html(tmp_path, SIM)
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html


def test_suppressed_by_cap_and_confirmed_default(tmp_path):
    ev = [{"id": "e1", "description": "事件甲", "severity": "low", "probability": 0.5, "verified": True,
           "suppressed_by_cap": True}]
    _seed(tmp_path, {}, [_s(0), _s(1, sampled_events=ev)])
    html = he.export_simulation_html(tmp_path, SIM)
    assert "未注入" in html and "先验未核对" not in html and "用户未确认" not in html


# ── 体检 ─────────────────────────────────────────────────────────────


def test_health_only_when_guard_traces_exist(tmp_path):
    _seed(tmp_path, {}, [_s(0), _s(1)])
    assert "真实性体检" not in he.export_simulation_html(tmp_path, SIM)


def test_health_rendered_with_warnings_and_disclaimer(tmp_path):
    w = [{"code": "C1", "message": "跳级", "step": 1}]
    _seed(tmp_path, {}, [_s(0), _s(1, consistency_warnings=w)])
    html = he.export_simulation_html(tmp_path, SIM)
    assert "真实性体检" in html and "非真实性评分" in html
    assert "结构性代理指标" in html and "存在误报" in html


# ── 技术树 ───────────────────────────────────────────────────────────


def test_tech_tree_rendered_from_branch_snapshot(tmp_path):
    snap = {"tech_state": {"nodes": [_node("a", "developer", 0.7),
                                      _node("b", "lab", 0.2, requires=[{"tech_id": "a"}], bottleneck="算力")]}}
    _seed(tmp_path, {"tech_model_enabled": True}, [_s(0), _s(1, dynamic_snapshot=snap)])
    html = he.export_simulation_html(tmp_path, SIM)
    assert "技术发展模型（2 个节点" in html
    assert "瓶颈：算力" in html and "70%" in html
    assert "典型时长未验证" not in html  # 用户给的时长，已验证


def test_tech_unverified_dwell_is_flagged(tmp_path):
    n = _node("a", dwell_source="llm_estimate", dwell_verified=False)
    _seed(tmp_path, {"tech_model_enabled": True, "tech_state": {"nodes": [n]}}, [_s(0)])
    assert "典型时长未验证" in he.export_simulation_html(tmp_path, SIM)


def test_tech_tree_escapes_names(tmp_path):
    n = _node("a", name="<b>X</b>")
    _seed(tmp_path, {"tech_model_enabled": True, "tech_state": {"nodes": [n]}}, [_s(0)])
    html = he.export_simulation_html(tmp_path, SIM)
    assert "<b>X</b>" not in html and "&lt;b&gt;X&lt;/b&gt;" in html


def test_tech_tree_hidden_when_switch_off(tmp_path):
    _seed(tmp_path, {"tech_state": {"nodes": [_node("a")]}}, [_s(0)])
    assert "技术发展模型" not in he.export_simulation_html(tmp_path, SIM)


def test_tech_graph_falls_back_silently_without_graphviz(tmp_path, monkeypatch):
    monkeypatch.setattr(hm, "_dot_to_svg", lambda dot: None)
    nodes = [_node("a"), _node("b", requires=[{"tech_id": "a"}])]
    _seed(tmp_path, {"tech_model_enabled": True, "tech_state": {"nodes": nodes}}, [_s(0)])
    html = he.export_simulation_html(tmp_path, SIM)
    assert "技术发展模型（2 个节点" in html and "<svg" not in html


# ── 分支作用域 ───────────────────────────────────────────────────────


def test_other_branch_without_snapshot_does_not_borrow_working_copy(tmp_path):
    """导出非活跃分支且没有快照：不能拿活跃分支的工作副本冒充。"""
    settings = {"tech_model_enabled": True, "tech_state": {"nodes": [_node("main_only")]}}
    store = _seed(tmp_path, settings, [_s(0)], branch="main", manifest_branch="main")
    store.append_state(_s(0), "alt")
    html = he.export_simulation_html(tmp_path, SIM, branch="alt")
    assert "main_only" not in html
    assert "main_only" in he.export_simulation_html(tmp_path, SIM, branch="main")


def test_snapshot_wins_over_working_copy(tmp_path):
    settings = {"tech_model_enabled": True, "tech_state": {"nodes": [_node("stale")]}}
    snap = {"tech_state": {"nodes": [_node("fresh")]}}
    _seed(tmp_path, settings, [_s(0, dynamic_snapshot=snap)])
    html = he.export_simulation_html(tmp_path, SIM)
    assert "fresh" in html and "stale" not in html


# ── 事件先验 ─────────────────────────────────────────────────────────


def test_event_priors_flag_unconfirmed_and_unverified(tmp_path):
    priors = [
        {"id": "p_user", "description": "手写事件", "rate_per_year": 0.5, "verified": True},
        {"id": "p_llm", "description": "提议事件", "rate_per_year": 2, "source": "llm_estimate",
         "rationale": "<i>我猜的</i>"},
    ]
    _seed(tmp_path, {"event_sampling_enabled": True, "event_priors": priors}, [_s(0)])
    html = he.export_simulation_html(tmp_path, SIM)
    assert "外生事件先验（2 条）" in html
    assert "AI 提议、你还没确认（不参与抽样）" in html
    assert html.count("未核对") >= 1
    assert "<i>我猜的</i>" not in html and "&lt;i&gt;我猜的&lt;/i&gt;" in html
    assert "下一步概率" not in html  # 不剧透


# ── 因果引擎 ─────────────────────────────────────────────────────────


def _engine_settings(**extra):
    s = {"causal_engine_enabled": True,
         "declared_causal_graph": [{"from_line_id": "econ", "to_line_id": "industry", "mechanism": "拉动投资"}],
         "causal_lines": [{"id": "econ"}, {"id": "industry"}]}
    s.update(extra)
    return s


def test_causal_engine_status_table_and_self_report_note(tmp_path):
    disp = {"pending_id": "econ->industry@1", "edge_id": "econ->industry", "from_line_id": "econ",
            "to_line_id": "industry", "disposition": "realized", "reason": "投资上升", "closed": True,
            "auto": False, "triggered_at_step": 1}
    _seed(tmp_path, _engine_settings(), [_s(0), _s(1, effect_dispositions=[disp])])
    html = he.export_simulation_html(tmp_path, SIM)
    assert "因果引擎（导出分支末状态）" in html
    assert "已观察" in html and "拉动投资" in html
    assert "AI 自报" in html and "不等于世界里被验证" in html
    assert "最近的处置记录" in html and "投资上升" in html


def test_causal_engine_due_timeline_lists_open_items(tmp_path):
    pending = [{"pending_id": "econ->industry@1", "edge_id": "econ->industry", "from_line_id": "econ",
                "to_line_id": "industry", "triggered_at_step": 1, "delay_steps": 0,
                "trigger_reason": "line_progress"}]
    snap = {"causal_pending": pending}
    _seed(tmp_path, _engine_settings(), [_s(0), _s(1, dynamic_snapshot=snap)])
    html = he.export_simulation_html(tmp_path, SIM)
    assert "到期因果时间线" in html
    assert "待兑现（已到期的在前）" in html
    assert "econ-&gt;industry@1" in html  # 待兑现项 id（已转义）
    assert "已到期，下一步会提醒" in html


def test_causal_engine_hidden_when_switch_off(tmp_path):
    s = _engine_settings()
    s.pop("causal_engine_enabled")
    _seed(tmp_path, s, [_s(0)])
    assert "因果引擎（导出" not in he.export_simulation_html(tmp_path, SIM)


def test_tree_effect_declarations_listed(tmp_path):
    lines = [{"id": "econ", "future_tree": {"branches": [
        {"id": "boom", "status": "emerging",
         "effects_if_active": [{"to_line_id": "industry", "mechanism": "需求扩张"}]}]}}]
    s = _engine_settings(tree_effects_enabled=True, causal_lines=lines)
    _seed(tmp_path, s, [_s(0)])
    html = he.export_simulation_html(tmp_path, SIM)
    assert "树分支声明的影响" in html and "econ/boom" in html and "需求扩张" in html
    assert "假设不是事实" in html


def test_causal_graph_falls_back_without_graphviz(tmp_path, monkeypatch):
    monkeypatch.setattr(hm, "_dot_to_svg", lambda dot: None)
    _seed(tmp_path, _engine_settings(), [_s(0)])
    html = he.export_simulation_html(tmp_path, SIM)
    assert "因果引擎（导出分支末状态）" in html and "着色关系图" not in html
    assert "边状态与兑现率" in html  # 表格仍在


# ── 树接地 ───────────────────────────────────────────────────────────


def test_tree_grounding_suggestions_listed_with_auto_off_note(tmp_path):
    lines = [{"id": "econ", "future_tree": {"branches": [
        {"id": "b2", "status": "emerging", "prerequisites": ["b1"]},
        {"id": "b1", "status": "dormant"}]}}]
    _seed(tmp_path, {"tree_grounding_enabled": True, "causal_lines": lines}, [_s(0)])
    html = he.export_simulation_html(tmp_path, SIM)
    assert "因果树接地：当前建议/约束" in html
    assert "自动迁移未开启" in html and "前置未满足" in html


# ── 降级 ─────────────────────────────────────────────────────────────


def test_section_failure_degrades_to_note_and_export_survives(tmp_path, monkeypatch):
    _seed(tmp_path, {"tech_model_enabled": True, "tech_state": {"nodes": [_node("a")]}}, [_s(0)])

    def boom(settings):
        raise RuntimeError("坏了")

    monkeypatch.setattr(hm.tech_model, "summarize", boom)
    html = he.export_simulation_html(tmp_path, SIM)
    assert "技术树渲染失败" in html and "STEP 0" in html


def test_step_notes_failure_returns_empty(monkeypatch):
    def boom(state):
        raise RuntimeError("坏了")

    monkeypatch.setattr(hm, "_step_notes", boom)
    assert hm.step_notes_html(_s(1)) == ""


def test_svg_paths_embed_graph_and_legend(tmp_path, monkeypatch):
    """Graphviz 可用时：技术 DAG 与因果着色图都内嵌 SVG，并带状态图例。"""
    monkeypatch.setattr(hm, "_dot_to_svg", lambda dot: "<svg><title>stub</title></svg>")
    s = _engine_settings(tech_model_enabled=True,
                         tech_state={"nodes": [_node("a"), _node("b", requires=[{"tech_id": "a"}])]})
    _seed(tmp_path, s, [_s(0)])
    html = he.export_simulation_html(tmp_path, SIM)
    assert html.count("<svg><title>stub</title></svg>") == 2
    assert "着色关系图" in html and "已证伪" in html  # 图例列出全部状态


def test_tech_dot_marks_soft_requires_dashed():
    rows = [{"node": {"id": "a", "name": "A", "stage": "lab", "progress": 0.1, "requires": []}, "status": "progressing"},
            {"node": {"id": "b", "name": "B", "stage": "lab", "progress": 0.1,
                      "requires": [{"tech_id": "a", "mode": "soft"}]}, "status": "blocked"}]
    dot = hm._tech_dot(rows)
    assert '"a" -> "b" [style="dashed"]' in dot
    assert hm._tech_dot([rows[0], {**rows[1], "node": {**rows[1]["node"], "requires": [{"tech_id": "a", "mode": "hard"}]}}]
                        ).count('[style="solid"]') == 1


def test_multi_step_notes_keep_chronological_position(tmp_path):
    """提示块跟着各自那一步的卡片走，不串步。"""
    _seed(tmp_path, {}, [
        _s(0), _s(1, consistency_warnings=[{"code": "C1", "message": "甲"}]),
        _s(2, consistency_warnings=[{"code": "C6", "message": "乙"}]),
    ])
    html = he.export_simulation_html(tmp_path, SIM)
    assert html.index("STEP 1") < html.index("一致性提示 C1") < html.index("STEP 2") < html.index("一致性提示 C6")


def test_event_priors_hidden_when_switch_off(tmp_path):
    priors = [{"id": "p", "description": "事件", "rate_per_year": 1}]
    _seed(tmp_path, {"event_priors": priors}, [_s(0)])
    assert "外生事件先验" not in he.export_simulation_html(tmp_path, SIM)


def test_tree_grounding_hidden_when_switch_off(tmp_path):
    lines = [{"id": "econ", "future_tree": {"branches": [
        {"id": "b2", "status": "emerging", "prerequisites": ["b1"]}, {"id": "b1", "status": "dormant"}]}}]
    _seed(tmp_path, {"causal_lines": lines}, [_s(0)])
    assert "因果树接地：当前" not in he.export_simulation_html(tmp_path, SIM)
