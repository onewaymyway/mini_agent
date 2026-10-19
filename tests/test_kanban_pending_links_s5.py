"""S5 看板测试：「🔗 待确认关联」子页的纯函数与接线。

对应 next_doc/goal_output_project_auto_link_plan.md §6.5。

保证：
  1. 来源文案：agent_tool / auto_detect 有明确中文，未知来源原样展示而不报错；
  2. 面包屑：管理项目 ← 触发节点；标题缺失时退回 id，不出现空白；
  3. 管理页新增的第五个子页已接入，且批量操作与逐条操作都走同一组客户端方法。
"""

from __future__ import annotations

import inspect
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "apps" / "mini_agent_kanban"))

import app  # noqa: E402


def test_origin_labels():
    assert app._oreg_origin_label("agent_tool") == "执行中 agent 主动关联"
    assert app._oreg_origin_label("auto_detect") == "执行后目录差异检测"


def test_unknown_origin_shown_as_is():
    assert app._oreg_origin_label("manual") == "manual"
    assert app._oreg_origin_label("") == "未知来源"


def test_breadcrumb_uses_titles():
    item = {"management_title": "做出产品", "goal_title": "第1轮", "management_id": "g1", "goal_id": "o1"}
    assert app._oreg_pending_breadcrumb(item) == "做出产品 ← 第1轮"


def test_breadcrumb_falls_back_to_ids_without_titles():
    item = {"management_id": "g1", "goal_id": "o1"}
    assert app._oreg_pending_breadcrumb(item) == "g1 ← o1"


def test_breadcrumb_placeholder_when_everything_missing():
    assert "未知" in app._oreg_pending_breadcrumb({})


def test_tab_wiring_present():
    src = inspect.getsource(app.render_output_registry_tab)
    assert "🔗 待确认关联" in src
    assert "_oreg_pending_links_section(client)" in src


def test_section_uses_shared_endpoints_for_single_and_batch():
    src = inspect.getsource(app._oreg_pending_links_section)
    assert src.count("output_registry_pending_confirm(") >= 2
    assert src.count("output_registry_pending_reject(") >= 2
    assert "_gt_invalidate_output_links_cache()" in src, "写后需同步失效目标树关联缓存"


def test_batch_actions_require_explicit_confirmation():
    src = inspect.getsource(app._oreg_pending_links_section)
    assert "_oreg_pend_batch_ok" in src
    assert src.count("disabled=not (selected and batch_ok)") == 2
