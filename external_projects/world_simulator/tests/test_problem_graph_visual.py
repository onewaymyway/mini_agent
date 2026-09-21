"""tests/test_problem_graph_visual.py — 第十轮批次三：问题关系图，
"🕸️ 问题关系图" 折叠区的节点收集/DOT 转换纯函数单测。

写法仿照 `test_causal_graph_visual.py`（同样只测不依赖 Streamlit 运行
时的纯字符串/纯数据处理部分，图形本身的可读性留给人工验收）：
`app.py::_collect_problem_graph_nodes()` 负责"从历史里按 id 去重、
保留最新 status"，`app.py::_problem_graph_edges_to_dot()` 负责"节点
列表 → Graphviz DOT 字符串"，两者拆开单测，互不依赖 Streamlit。
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app  # noqa: E402  （world_simulator 项目根下的 Streamlit 入口）


def _state(problems):
    return SimpleNamespace(problems=problems)


# ── _collect_problem_graph_nodes() ──────────────────────────────────


def test_collect_nodes_empty_history_returns_empty():
    assert app._collect_problem_graph_nodes([]) == []


def test_collect_nodes_skips_malformed_and_empty_symptom():
    history = [
        _state([
            "不是字典，应该被跳过",
            {"id": "p1", "blocked_goal": "没有 symptom，应该被跳过"},
            {"id": "p2", "symptom": "资金不足"},
        ])
    ]
    nodes = app._collect_problem_graph_nodes(history)
    assert [n["_key"] for n in nodes] == ["p2"]


def test_collect_nodes_dedupes_by_id_and_keeps_latest_status():
    history = [
        _state([{"id": "p1", "symptom": "资金不足", "status": "emerging"}]),
        _state([{"id": "p1", "symptom": "资金不足加剧", "status": "active"}]),
    ]
    nodes = app._collect_problem_graph_nodes(history)
    assert len(nodes) == 1
    assert nodes[0]["status"] == "active"
    assert nodes[0]["symptom"] == "资金不足加剧"
    assert nodes[0]["_key"] == "p1"


def test_collect_nodes_without_id_do_not_collapse_into_each_other():
    history = [
        _state([
            {"symptom": "问题甲，没有 id"},
            {"symptom": "问题乙，没有 id"},
        ])
    ]
    nodes = app._collect_problem_graph_nodes(history)
    assert len(nodes) == 2
    assert nodes[0]["_key"] != nodes[1]["_key"]


def test_collect_nodes_preserves_first_seen_order():
    history = [
        _state([{"id": "p2", "symptom": "第二个出现的问题"}]),
        _state([{"id": "p1", "symptom": "后来又出现的问题"}]),
    ]
    nodes = app._collect_problem_graph_nodes(history)
    assert [n["_key"] for n in nodes] == ["p2", "p1"]


# ── _problem_graph_edges_to_dot() ───────────────────────────────────


def test_problem_graph_dot_empty_nodes_returns_empty_digraph():
    dot = app._problem_graph_edges_to_dot([])
    assert dot.startswith("digraph G {")
    assert dot.endswith("}")
    assert '";' not in dot
    assert "->" not in dot


def test_problem_graph_dot_includes_nodes_status_color_and_edges():
    nodes = [
        {"_key": "p1", "symptom": "资金不足", "status": "active", "depends_on": ["p2"]},
        {"_key": "p2", "symptom": "现金流预测不准", "status": "solved", "depends_on": []},
    ]
    dot = app._problem_graph_edges_to_dot(nodes)
    assert '"p1"' in dot
    assert '"p2"' in dot
    assert app._PROBLEM_STATUS_COLORS["active"] in dot
    assert app._PROBLEM_STATUS_COLORS["solved"] in dot
    assert '"p1" -> "p2"' in dot
    assert "[active]" in dot
    assert "[solved]" in dot


def test_problem_graph_dot_truncates_long_symptom_label():
    long_symptom = "一" * 40
    nodes = [{"_key": "p1", "symptom": long_symptom, "status": "", "depends_on": []}]
    dot = app._problem_graph_edges_to_dot(nodes)
    assert "一" * 24 + "…" in dot
    assert long_symptom not in dot


def test_problem_graph_dot_unknown_status_falls_back_to_default_color():
    nodes = [{"_key": "p1", "symptom": "未知状态的问题", "status": "weird_value", "depends_on": []}]
    dot = app._problem_graph_edges_to_dot(nodes)
    assert "#eef2ff" in dot


def test_problem_graph_dot_edge_to_nonexistent_key_still_rendered():
    """`depends_on` 引用一个当前节点集合里不存在的 key 时，不应该报错
    ——直接画一条指向那个 key 的边，Graphviz 会把它当新的孤立节点渲染，
    不在展示层做引用存在性校验（同 `depends_on` 字段本身"提示而非
    强制"的取舍）。"""
    nodes = [{"_key": "p1", "symptom": "问题甲", "status": "", "depends_on": ["没声明过的问题"]}]
    dot = app._problem_graph_edges_to_dot(nodes)
    assert '"p1" -> "没声明过的问题"' in dot


def test_problem_graph_dot_skips_empty_depends_on_entries():
    nodes = [{"_key": "p1", "symptom": "问题甲", "status": "", "depends_on": ["", "  ", "p2"]}]
    dot = app._problem_graph_edges_to_dot(nodes)
    assert dot.count("->") == 1
    assert '"p1" -> "p2"' in dot
