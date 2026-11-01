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

from ui.assets_page import page_assets  # noqa: E402
from ui.auto_added_page import page_auto_added  # noqa: E402
from ui.cockpit_page import page_cockpit  # noqa: E402
from ui.final_page import page_final  # noqa: E402
from ui.gender_page import page_gender  # noqa: E402
from ui.gear_page import page_gear  # noqa: E402
from ui.chapter_page import page_chapters  # noqa: E402
from ui.import_page import page_import  # noqa: E402
from ui.projects_page import page_projects  # noqa: E402

PAGES = {
    "🧭 驾驶舱": page_cockpit,
    "📥 导入导出目录": page_import,
    "📚 项目列表": page_projects,
    "🎭 全书素材": page_assets,
    "🎬 章节生产": page_chapters,
    "🚦 挡位与队列": page_gear,
    "🧩 补建角色审核": page_auto_added,
    "🎞️ 全书合成": page_final,
    "⚧ 性别确认": page_gender,
}


def main() -> None:
    st.set_page_config(page_title="novel_to_video", page_icon="🎬", layout="wide")
    with st.sidebar:
        st.markdown("### 🎬 novel_to_video")
        st.caption("小说转短剧工作台 · Phase 5")
        choice = st.radio("导航", list(PAGES), label_visibility="collapsed", key="nav")
    PAGES[choice]()


if __name__ == "__main__":
    main()
