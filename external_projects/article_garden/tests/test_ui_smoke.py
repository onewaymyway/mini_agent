"""看板冒烟测试：用 Streamlit 自带的 AppTest 真实跑页面脚本（无浏览器），检查不抛异常并出现关键元素。"""

import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

from article_garden import domains  # noqa: E402

APP = str(__import__("pathlib").Path(__file__).resolve().parent.parent / "app.py")


PAGE_FUNCS = {
    "home": ("ui.home_page", "render_home_page"), "tree": ("ui.tree_page", "render_tree_page"),
    "library": ("ui.library_page", "render_library_page"), "graph": ("ui.graph_page", "render_graph_page"),
    "topics": ("ui.topics_page", "render_topics_page"), "research": ("ui.research_page", "render_research_page"),
    "review": ("ui.placeholder_pages", "render_review_page"), "runs": ("ui.runs_page", "render_runs_page"),
    "settings": ("ui.settings_page", "render_settings_page"), "domains": ("ui.wizard_page", "render_domains_overview_page"),
    "wizard": ("ui.wizard_page", "render_wizard_page"),
}


def _page_script(module, func, domain, project_dir):
    # AppTest.from_function 会把本函数源码单独执行：所有依赖都必须在函数内导入
    import importlib
    import sys
    if project_dir not in sys.path:
        sys.path.insert(0, project_dir)
    import streamlit as st
    st.session_state["domain_id"] = domain
    getattr(importlib.import_module(module), func)()


def run_app(url_path=None, domain=None):
    """url_path 为 None：跑完整的 app.py（默认首页，含侧边栏）；否则直接渲染该页面函数（AppTest 不支持按名切换可调用页面）。"""
    if url_path is None:
        at = AppTest.from_file(APP, default_timeout=30)
        if domain:
            at.query_params["domain"] = domain
        return at.run()
    module, func = PAGE_FUNCS[url_path]
    project_dir = str(__import__("pathlib").Path(APP).parent)
    at = AppTest.from_function(_page_script, default_timeout=30, args=(module, func, domain, project_dir))
    return at.run()


def texts(at):
    out = []
    for coll in (at.markdown, at.caption, at.subheader, at.info, at.success, at.warning, at.error, at.title):
        out += [e.value for e in coll]
    return "\n".join(out)


def test_first_run_without_domains_shows_wizard_only():
    at = run_app()
    assert not at.exception, at.exception
    assert "新建文章库" in texts(at)


def test_wizard_creates_domain():
    at = run_app()
    at.text_input[0].set_value("人工智能")
    at.text_input[1].set_value("ai")
    at.button[0].click()
    at.run()
    assert not at.exception, at.exception
    assert [d["id"] for d in domains.list_domains()] == ["ai"]
    assert domains.DomainContext("ai").load_charter()[0]["scope"]["in"]  # 默认选中 ai 模板


def test_wizard_shows_error_for_bad_id():
    at = run_app()
    at.text_input[0].set_value("x")
    at.text_input[1].set_value("Bad Id")
    at.button[0].click()
    at.run()
    assert at.error and "id" in at.error[0].value
    assert domains.list_domains() == []


@pytest.mark.parametrize("page", ["home", "tree", "library", "graph", "topics", "research", "review", "runs",
                                  "settings", "domains", "wizard"])
def test_every_page_renders_with_a_domain(page):
    domains.create_domain("ai", "人工智能", "ai")
    at = run_app(page, domain="ai")
    assert not at.exception, f"{page}: {at.exception}"


def test_home_shows_pipeline_and_next_steps():
    domains.create_domain("ai", "人工智能", "ai")
    at = run_app("home", domain="ai")
    t = texts(at)
    assert "流水线" in t and "导入文章库" in t and "没有待处理事项" in t


def test_library_lists_manually_placed_markdown():
    ctx = domains.create_domain("ai", "人工智能", "ai")
    (ctx.imported_dir / "a.md").write_text("# 标题", encoding="utf-8")
    at = run_app("library", domain="ai")
    assert not at.exception and at.dataframe


def test_switching_domain_via_query_param_shows_that_domain():
    domains.create_domain("ai", "人工智能", "ai")
    domains.create_domain("fin", "理财")
    at = run_app(None, domain="fin")
    assert not at.exception, at.exception
    assert at.selectbox[0].value == "fin"


def test_settings_charter_save_roundtrip():
    ctx = domains.create_domain("ai", "人工智能", "ai")
    at = run_app("settings", domain="ai")
    assert not at.exception, at.exception
    # 章程表单里第 1 个 text_input 是「领域名称」
    at.text_input[0].set_value("AI 改名")
    at.button[0].click()    # 第一个表单提交按钮：保存章程
    at.run()
    assert not at.exception, at.exception
    assert ctx.load_charter()[0]["domain"] == "AI 改名"


def test_settings_automation_toggle_saves_to_this_domain_only():
    from article_garden import settings
    a = domains.create_domain("a1", "A", "ai")
    b = domains.create_domain("b1", "B", "ai")
    at = run_app("settings", domain="a1")
    at.checkbox(key="domain:automation:open_topics").check()
    at.button[1].click()        # 第 2 个表单提交按钮：自动化「保存」
    at.run()
    assert not at.exception, at.exception
    assert settings.effective_settings(a)[0]["automation"]["open_topics"] is True
    assert settings.effective_settings(b)[0]["automation"]["open_topics"] is False
    # 再进一次设置页：该项标出“本库覆盖”，可一键恢复继承
    at2 = run_app("settings", domain="a1")
    assert any("本库覆盖" in c.value for c in at2.caption)
