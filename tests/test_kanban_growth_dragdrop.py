"""tests/test_kanban_growth_dragdrop.py — 成长顾问 tab 的拖拽式看板视图
（next_doc/growth_advisor_design.md P3 最后一项）。

只测纯函数部分（卡片标签生成、可选依赖探测），不驱动 Streamlit 的
渲染/组件交互——`_render_growth_kanban_dragdrop` 依赖真实的
ScriptRunContext 和前端拖拽事件，不在无头单测的覆盖范围内。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "apps" / "mini_agent_kanban"))

import app  # noqa: E402


def test_sortable_available_reflects_installed_package():
    # streamlit-sortables 在本仓库 requirements 里是可选依赖；这里只
    # 断言探测函数不抛异常、返回值是 bool，不断言具体安装状态（CI 环境
    # 可能没装）。
    assert isinstance(app._sortable_available(), bool)


def test_growth_card_label_is_unique_for_same_title_different_id():
    c1 = {"title": "数据分析", "confidence": 0.6, "candidate_id": "aaaaaaaa-1111"}
    c2 = {"title": "数据分析", "confidence": 0.6, "candidate_id": "bbbbbbbb-2222"}
    label1 = app._growth_card_label(c1)
    label2 = app._growth_card_label(c2)
    assert label1 != label2
    assert "数据分析" in label1 and "数据分析" in label2


def test_growth_card_label_includes_confidence():
    c = {"title": "系统设计", "confidence": 0.42, "candidate_id": "cccccccc-3333"}
    label = app._growth_card_label(c)
    assert "0.42" in label


def test_growth_kanban_columns_cover_all_backlog_statuses():
    statuses = {s for s, _ in app._GROWTH_KANBAN_COLUMNS}
    assert statuses == {"pending", "accepted", "dismissed"}


def test_render_growth_pending_list_is_fallback_when_sortable_missing(monkeypatch):
    # 模拟未安装 streamlit-sortables 的场景：render_growth_tab 应该走
    # _render_growth_pending_list 而不是 _render_growth_kanban_dragdrop。
    monkeypatch.setattr(app, "_sortable_available", lambda: False)
    assert app._sortable_available() is False
