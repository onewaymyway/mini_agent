"""novel_to_video — Streamlit 入口（`streamlit run app.py`）。

Phase 0 页面：导入导出目录、项目列表。后续阶段逐步加入素材、章节工作台、全书合成等页面。
页面逻辑全部在 ui/ 下；本文件只做导航。
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import streamlit as st  # noqa: E402

from ui.import_page import page_import  # noqa: E402
from ui.projects_page import page_projects  # noqa: E402

PAGES = {
    "📥 导入导出目录": page_import,
    "📚 项目列表": page_projects,
}


def main() -> None:
    st.set_page_config(page_title="novel_to_video", page_icon="🎬", layout="wide")
    with st.sidebar:
        st.markdown("### 🎬 novel_to_video")
        st.caption("小说转短剧工作台 · Phase 0")
        choice = st.radio("导航", list(PAGES), label_visibility="collapsed", key="nav")
    PAGES[choice]()


if __name__ == "__main__":
    main()
