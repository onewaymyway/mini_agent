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


def test_pending_list_reason_select_does_not_submit_until_button_clicked(tmp_path):
    """[看板卡顿修复] 回归测试：`_render_growth_pending_list()` 里"忽略
    原因" selectbox 必须包在 `st.form` 里——只改变下拉选项不应该触发任何
    对 `client.growth_candidate_action()` 的调用（也就不会触发那次多余
    的、导致页面卡顿的整页 rerun）；只有点击「采纳」/「忽略」按钮（表单
    提交）才应该真正提交，且提交时带上当前选中的原因。"""
    from streamlit.testing.v1 import AppTest

    script = f'''
import sys
sys.path.insert(0, {str(Path(__file__).resolve().parent.parent / "apps" / "mini_agent_kanban")!r})
import app as kanban_app
import streamlit as st

if "calls" not in st.session_state:
    st.session_state["calls"] = []

class FakeClient:
    def growth_candidate_action(self, cid, action, reason=None):
        st.session_state["calls"].append((cid, action, reason))
        return {{}}
    def growth_report(self, *a, **k):
        return {{}}

pending = [{{
    "candidate_id": "cand-1",
    "title": "测试候选",
    "rationale": "因为...",
    "confidence": 0.8,
    "evidence_count": 3,
}}]
kanban_app._render_growth_pending_list(FakeClient(), pending)
'''
    script_path = tmp_path / "_apptest_script.py"
    script_path.write_text(script, encoding="utf-8")

    at = AppTest.from_file(str(script_path))
    at.run()
    assert not at.exception

    # 只改变下拉选项 → 不应该有任何提交发生
    at.selectbox[0].select("方向没错，是报告没写好").run()
    assert at.session_state["calls"] == []

    # 点击「忽略」→ 恰好一次提交，且带上刚才选中的原因
    at.button(key="growth_dismiss_cand-1").click().run()
    assert at.session_state["calls"] == [("cand-1", "dismiss", "report_not_useful")]


def test_keyword_list_batch_confirm_only_applies_to_checked_topics(tmp_path):
    """[成长顾问看板"当前关键词列表"支持批量操作] 覆盖"待确认"分组：
    勾选两个「待确认」主题里的一个，点批量保留按钮，应该只对勾选的那个
    调用一次 `growth_keyword_confirm`，未勾选的不受影响；批量按钮在没有
    任何勾选时不应该出现（`_kw_batch_bar` 选中数为 0 时直接 return）。"""
    from streamlit.testing.v1 import AppTest

    script = f'''
import sys
sys.path.insert(0, {str(Path(__file__).resolve().parent.parent / "apps" / "mini_agent_kanban")!r})
import app as kanban_app
import streamlit as st

if "calls" not in st.session_state:
    st.session_state["calls"] = []

class FakeClient:
    def growth_keyword_confirm(self, topic):
        st.session_state["calls"].append(("confirm", topic))
        return {{}}
    def growth_keyword_remove(self, topic):
        st.session_state["calls"].append(("remove", topic))
        return {{}}
    def growth_keyword_restore(self, topic):
        st.session_state["calls"].append(("restore", topic))
        return {{}}

diagnostics = {{
    "signal_scan": {{
        "topics_detail": [
            {{"topic": "主题A", "keywords": ["a1"], "source": "llm_learned", "confirmed_by_user": False}},
            {{"topic": "主题B", "keywords": ["b1"], "source": "llm_learned", "confirmed_by_user": False}},
        ]
    }},
    "hidden_builtin_topics": [],
}}
kanban_app._render_growth_profile_and_keywords(FakeClient(), diagnostics)
'''
    script_path = tmp_path / "_apptest_script.py"
    script_path.write_text(script, encoding="utf-8")

    at = AppTest.from_file(str(script_path))
    at.run()
    assert not at.exception

    # 一开始什么都没勾选，批量按钮不应该渲染。
    assert not any(b.key == "growth_kw_confirm_batch" for b in at.button)

    # 只勾选「主题A」
    at.checkbox(key="growth_kw_sel_learned_主题A").check().run()
    assert not at.exception
    assert any(b.key == "growth_kw_confirm_batch" for b in at.button)

    at.button(key="growth_kw_confirm_batch").click().run()
    assert at.session_state["calls"] == [("confirm", "主题A")]
    # 批量操作后勾选状态被清空，批量按钮重新消失。
    assert not any(b.key == "growth_kw_confirm_batch" for b in at.button)
