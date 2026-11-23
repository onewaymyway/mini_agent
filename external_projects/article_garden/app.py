"""app.py — article_garden 的 Streamlit 看板（端口 8508）。

结构：侧边栏顶部是文章库切换器；页面用 st.navigation 组织；没有任何文章库时只显示新建向导。
当前文章库保存在 session_state 并同步到 URL（?domain=<id>）。
"""

import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

import streamlit as st  # noqa: E402

from article_garden import domains  # noqa: E402
from ui import state  # noqa: E402
from ui.graph_page import render_graph_page  # noqa: E402
from ui.topics_page import render_topics_page  # noqa: E402
from ui.tree_page import render_tree_page  # noqa: E402
from ui.home_page import render_home_page  # noqa: E402
from ui.library_page import render_library_page  # noqa: E402
from ui.review_page import render_review_page  # noqa: E402
from ui.research_page import render_research_page  # noqa: E402
from ui.runs_page import render_runs_page  # noqa: E402
from ui.settings_page import render_settings_page  # noqa: E402
from ui.wizard_page import render_domains_overview_page, render_wizard_page  # noqa: E402

st.set_page_config(page_title="article_garden", page_icon="🌱", layout="wide")


def _build_pages():
    spec = [
        ("home", render_home_page, "首页", "🏠", True),
        ("tree", render_tree_page, "知识树", "🌳", False),
        ("library", render_library_page, "文章库", "📚", False),
        ("graph", render_graph_page, "概念图", "🕸", False),
        ("topics", render_topics_page, "选题", "💡", False),
        ("research", render_research_page, "调研", "🔎", False),
        ("review", render_review_page, "审阅", "✍", False),
        ("runs", render_runs_page, "运行日志", "▶", False),
        ("settings", render_settings_page, "设置", "⚙", False),
        ("domains", render_domains_overview_page, "所有文章库", "🗂", False),
        ("wizard", render_wizard_page, "新建文章库", "➕", False),
    ]
    pages = {}
    for key, fn, title, icon, default in spec:
        pages[key] = st.Page(fn, title=title, icon=icon, url_path=key, default=default)
    return pages


def main() -> None:
    flash = st.session_state.pop("flash", None)
    try:
        ids = [d for d in domains.list_domains()]
    except domains.DomainError as exc:
        st.error(str(exc))
        st.stop()

    with st.sidebar:
        st.title("🌱 article_garden")
        if ids:
            cur = state.current_domain_id()
            choice = st.selectbox("文章库", [d["id"] for d in ids], index=[d["id"] for d in ids].index(cur),
                                  format_func=lambda i: next(d["name"] for d in ids if d["id"] == i))
            if choice != cur:
                state.set_domain(choice)
                st.rerun()
        else:
            st.caption("还没有文章库")
        if flash:
            st.success(flash)

    pages = _build_pages()
    state.PAGES.clear()
    state.PAGES.update(pages)
    if not ids:
        nav = st.navigation([pages["wizard"]], position="sidebar")
    else:
        nav = st.navigation({"工作台": [pages[k] for k in ("home", "tree", "library", "graph")],
                             "生产": [pages[k] for k in ("topics", "research", "review", "runs")],
                             "管理": [pages[k] for k in ("settings", "domains", "wizard")]})
    nav.run()


main()
