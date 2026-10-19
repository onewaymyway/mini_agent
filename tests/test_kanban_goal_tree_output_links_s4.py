"""S4 看板测试：目标树产出关联的纯函数、缓存与参数透传。

对应 next_doc/goal_output_project_auto_link_plan.md §6（看板界面）。

保证：
  1. 标题徽标：🗂️N 为生效关联数，⏳N 为待确认数，无关联时不出现任何徽标；
  2. 单条关联的展示包含 slug、角色、最新版本（未发布时显示占位）与产出路径；
  3. 写操作结果：返回 _error 时视为失败，不刷新；
  4. 批量视图缓存：TTL 内复用；错误结果不缓存；失效后重新请求；
  5. output_links_by_node 参数从树根一路透传到每个递归子节点（否则子节点徽标会丢失）。
"""

from __future__ import annotations

import inspect
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "apps" / "mini_agent_kanban"))

import app  # noqa: E402


# ── 1. 徽标 ───────────────────────────────────────────────────────────────────

def test_no_badge_without_links_or_pending():
    assert app._gt_output_badges(None) == ""
    assert app._gt_output_badges({"linked": [], "pending": []}) == ""


def test_badges_count_linked_and_pending():
    entry = {"linked": [{"slug": "a"}, {"slug": "b"}], "pending": [{"id": "pl_1"}]}
    assert app._gt_output_badges(entry) == " 🗂️2 ⏳1"


def test_badge_only_linked_when_no_pending():
    assert app._gt_output_badges({"linked": [{"slug": "a"}], "pending": []}) == " 🗂️1"


# ── 2. 单条关联展示 ───────────────────────────────────────────────────────────

def test_link_label_contains_slug_role_version_and_path():
    label = app._gt_output_link_label({
        "slug": "alpha", "role": "build", "latest_version": "1.2.0",
        "output_dir": "output_projects/alpha/output",
    })
    for part in ("alpha", "build", "1.2.0", "output_projects/alpha/output"):
        assert part in label


def test_link_label_placeholder_when_unpublished():
    label = app._gt_output_link_label({"slug": "alpha", "role": "build", "latest_version": None})
    assert "未发布" in label


# ── 3. 写操作结果 ─────────────────────────────────────────────────────────────

def test_action_result_error_is_failure(monkeypatch):
    shown = []
    monkeypatch.setattr(app, "st", SimpleNamespace(error=lambda msg: shown.append(msg)))
    assert app._gt_output_action_result({"_error": "HTTP 400: x"}) is False
    assert shown and "HTTP 400" in shown[0]


def test_action_result_success(monkeypatch):
    monkeypatch.setattr(app, "st", SimpleNamespace(error=lambda msg: None))
    assert app._gt_output_action_result({"ok": True}) is True


# ── 4. 批量视图缓存 ───────────────────────────────────────────────────────────

class _FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def goal_tree_output_links(self):
        self.calls += 1
        return self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]


@pytest.fixture
def fake_st(monkeypatch):
    fake = SimpleNamespace(session_state={})
    monkeypatch.setattr(app, "st", fake)
    return fake


def test_cache_reused_within_ttl(fake_st):
    client = _FakeClient([{"by_node": {"g1": {}}}])
    first = app._gt_output_links_cached(client)
    second = app._gt_output_links_cached(client)
    assert first == second and client.calls == 1


def test_error_result_is_not_cached(fake_st):
    client = _FakeClient([{"_error": "boom"}, {"by_node": {}}])
    assert app._gt_output_links_cached(client).get("_error") == "boom"
    assert app._gt_output_links_cached(client) == {"by_node": {}}
    assert client.calls == 2


def test_invalidate_forces_refetch(fake_st):
    client = _FakeClient([{"by_node": {"g1": {}}}, {"by_node": {"g2": {}}}])
    app._gt_output_links_cached(client)
    app._gt_invalidate_output_links_cache()
    assert app._gt_output_links_cached(client) == {"by_node": {"g2": {}}}
    assert client.calls == 2


# ── 5. 参数透传 ───────────────────────────────────────────────────────────────

def test_node_body_accepts_output_links_parameter():
    sig = inspect.signature(app._render_goal_tree_node_body)
    assert "output_links_by_node" in sig.parameters
    assert sig.parameters["output_links_by_node"].default is None


def test_node_renderer_threads_parameter_to_recursion_and_body():
    src = inspect.getsource(app._render_goal_tree_node)
    assert src.count("output_links_by_node") >= 3, "包装层与子节点递归都必须透传"
    src_body_call = inspect.getsource(app._render_goal_tree_node)
    assert "_render_goal_tree_node_body(" in src_body_call


def test_recursive_child_call_passes_map():
    """真正的递归调用点（子节点循环）必须带上 output_links_by_node，否则子节点徽标丢失。"""
    src = inspect.getsource(app._render_goal_tree_node_body)
    recursion = src[src.index("for child in children:"):]
    assert "output_links_by_node=output_links_by_node" in recursion


def test_manage_section_is_called_with_entry():
    src = inspect.getsource(app._render_goal_tree_node_body)
    assert "_render_goal_tree_output_links_section(" in src
