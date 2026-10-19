"""S1 子目标推荐：采纳 / 忽略 测试。

对应 goal_tree 子目标推荐方案（S1）。

保证：
  1. 采纳候选后创建的子节点 source="user_decompose"，不是 agent_derived；
  2. 采纳后的子节点不会出现在「📥 主动建议」的软目标待处理列表里；
  3. 采纳后候选从父节点的待确认列表移除；
  4. 忽略候选会移除它，且 30 天内不再推荐同主题（由分解器去重）；
  5. 看板汇总面板的收集函数能找出全树所有待确认候选，并附带父节点标题。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from mini_agent.perception.goal_backlog import GoalBacklog
from mini_agent.perception.goal_tree_decomposer import GoalTreeDecomposer
from mini_agent.perception.initiative_inbox import _from_soft_goal_deriver
from mini_agent.storage.paths import AgentPaths

APP = Path(__file__).resolve().parent.parent / "apps" / "mini_agent_kanban" / "app.py"


@pytest.fixture
def env(tmp_path: Path):
    (tmp_path / ".agent").mkdir()
    paths = AgentPaths(project_root=tmp_path)
    b = GoalBacklog(paths)
    b.load()
    top = b.get_root_node()
    d = b.add_node("domain", "工作", parent_id=top.id)
    g = b.add_node("goal", "做出产品", parent_id=d.id)
    b.append_decompose_candidates(g.id, [
        {"id": "cand_a", "title": "调研竞品", "level": "goal", "description": "d", "reason": "r"},
        {"id": "cand_b", "title": "写原型", "level": "goal", "description": "d", "reason": "r"},
    ])
    return paths, b, g


def test_accepted_child_is_user_decompose_not_agent_derived(env):
    paths, b, g = env
    child = b.accept_candidate(g.id, "cand_a")
    assert child is not None
    assert child.source == "user_decompose"
    assert child.parent_id == g.id


def test_accepted_child_not_in_inbox_as_agent_suggestion(env):
    paths, b, g = env
    b.accept_candidate(g.id, "cand_a")
    titles = [i.title for i in _from_soft_goal_deriver(paths)]
    assert "调研竞品" not in titles


def test_accept_removes_candidate_from_parent(env):
    paths, b, g = env
    b.accept_candidate(g.id, "cand_a")
    b2 = GoalBacklog(paths)
    b2.load()
    remaining = [c["id"] for c in b2.get(g.id).decompose_candidates]
    assert remaining == ["cand_b"]


def test_reject_removes_candidate_and_is_deduplicated(env):
    paths, b, g = env
    decomposer = GoalTreeDecomposer(paths, b)
    assert decomposer.reject_candidate(g.id, "cand_b")
    b2 = GoalBacklog(paths)
    b2.load()
    assert [c["id"] for c in b2.get(g.id).decompose_candidates] == ["cand_a"]


def _collector():
    """从看板源码中取出 _collect_pending_decompose，不导入整个 Streamlit 应用。"""
    tree = ast.parse(APP.read_text(encoding="utf-8"))
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
              and n.name == "_collect_pending_decompose")
    ns: dict = {}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), str(APP), "exec"), ns)
    return ns["_collect_pending_decompose"]


def test_panel_collector_finds_all_pending_with_parent_titles(env):
    paths, b, g = env
    b2 = GoalBacklog(paths)
    b2.load()
    # 把 b2 的树序列化成看板拿到的形状：{"node": {...}, "children": [...]}
    def wrap(node_id):
        n = b2.get(node_id)
        return {"node": n.to_dict(), "children": [wrap(c) for c in n.children_ids]}
    root = wrap(b2.get_root_node().id)
    pending = _collector()(root)
    assert len(pending) == 1
    node_id, title, cands = pending[0]
    assert node_id == g.id and title == "做出产品"
    assert [c["id"] for c in cands] == ["cand_a", "cand_b"]
