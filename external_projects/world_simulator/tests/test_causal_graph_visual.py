"""tests/test_causal_graph_visual.py — 第八轮批次四：因果线可视化
整合，"🕸️ 关系图"子视图的 DOT 转换函数单测。

对应 `next_doc/world_simulator_c_category_precision_upgrade_
improvement_plan.md` 第 5 节。方案原文承认"图是否可读需要人工过一遍"，
所以这里只单测 `app.py::_causal_graph_edges_to_dot()` 这一个不依赖
Streamlit 运行时、纯字符串拼接的函数——图形本身的可读性不是自动化
测试能替代的部分。

`app.py` 顶层只有函数/类定义，`main()` 由 `if __name__ == "__main__"`
守卫，所以可以直接 `import app` 而不会触发任何 Streamlit 运行时调用
（同目录内没有其它测试直接 import app.py，这是第一次这么做，写在这里
留个说明供后来者参考）。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app  # noqa: E402  （world_simulator 项目根下的 Streamlit 入口）
from world_simulator.causal_graph import CausalEdge  # noqa: E402


def test_edges_to_dot_empty_list_returns_empty_digraph():
    dot = app._causal_graph_edges_to_dot([])
    assert dot.startswith("digraph G {")
    assert dot.endswith("}")
    # 空图不应该出现任何节点/边定义行。
    assert '";' not in dot
    assert "->" not in dot


def test_edges_to_dot_includes_all_nodes_and_edge_labels():
    edges = [
        CausalEdge(
            source_line="line_tech",
            target_line="line_market",
            relation_counts={"one_way": 2, "feedback_loop": 1},
            examples=[{"driver": "d1", "effect": "e1"}],
        ),
    ]
    dot = app._causal_graph_edges_to_dot(edges)
    assert '"line_tech";' in dot
    assert '"line_market";' in dot
    assert '"line_tech" -> "line_market"' in dot
    # 标签按次数降序，一致于列表视图的排序口径。
    assert "2次单向影响，1次反馈循环" in dot


def test_edges_to_dot_escapes_quotes_and_backslashes_in_node_names():
    """节点 id 理论上可以是任意字符串（比如自发因果线用原始 id），
    含双引号/反斜杠时必须转义，否则生成的 DOT 语法非法。"""
    edges = [
        CausalEdge(
            source_line='weird"line\\a',
            target_line="line_b",
            relation_counts={"one_way": 1},
            examples=[],
        ),
    ]
    dot = app._causal_graph_edges_to_dot(edges)
    assert 'weird\\"line\\\\a' in dot
    # 转义之后，节点定义行应该恰好有 2 个"没有被反斜杠转义"的双引号
    # （开始和结束），不会因为节点名里的原始引号而语法错乱。
    import re
    node_def_line = next(
        line for line in dot.splitlines() if "weird" in line and line.strip().endswith(";")
    )
    unescaped_quotes = re.findall(r'(?<!\\)"', node_def_line)
    assert len(unescaped_quotes) == 2, node_def_line


def test_edges_to_dot_deduplicates_shared_nodes():
    """同一个节点既是某条边的 source 又是另一条边的 target 时，
    只应该出现一次节点定义行。"""
    edges = [
        CausalEdge(
            source_line="line_a", target_line="line_b",
            relation_counts={"one_way": 1}, examples=[],
        ),
        CausalEdge(
            source_line="line_b", target_line="line_c",
            relation_counts={"one_way": 1}, examples=[],
        ),
    ]
    dot = app._causal_graph_edges_to_dot(edges)
    assert dot.count('"line_b";') == 1


def test_edges_to_dot_handles_unassigned_placeholder_node():
    """`(未归属)` 是 `causal_graph.py` 用的特殊占位符，本身带括号，
    转义/拼接逻辑不应该对它报错或产生非法语法。"""
    edges = [
        CausalEdge(
            source_line="(未归属)", target_line="line_b",
            relation_counts={"one_way": 1}, examples=[],
        ),
    ]
    dot = app._causal_graph_edges_to_dot(edges)
    assert '"(未归属)";' in dot
    assert '"(未归属)" -> "line_b"' in dot


# ── 第十一轮 2.2 节：has_delay 边 → 虚线 ────────────────────────────


def test_edges_to_dot_marks_delayed_edge_as_dashed():
    edges = [
        CausalEdge(
            source_line="line_a",
            target_line="line_b",
            relation_counts={"one_way": 1},
            examples=[],
            has_delay=True,
        ),
    ]
    dot = app._causal_graph_edges_to_dot(edges)
    edge_line = next(line for line in dot.splitlines() if "->" in line)
    assert 'style="dashed"' in edge_line
    assert 'label=' in edge_line


def test_edges_to_dot_no_dashed_style_when_not_delayed():
    edges = [
        CausalEdge(
            source_line="line_a",
            target_line="line_b",
            relation_counts={"one_way": 1},
            examples=[],
        ),
    ]
    dot = app._causal_graph_edges_to_dot(edges)
    edge_line = next(line for line in dot.splitlines() if "->" in line)
    assert "dashed" not in edge_line
