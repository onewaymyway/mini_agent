"""app.py — article_to_video 的 Streamlit 界面（端口 8507）。"""

import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

import streamlit as st  # noqa: E402

from ui.import_page import render_import_page  # noqa: E402
from ui.outline_page import render_outline_page  # noqa: E402
from ui.script_page import render_script_page  # noqa: E402
from ui.voice_page import render_voice_page  # noqa: E402
from ui.storyboard_page import render_storyboard_page  # noqa: E402
from ui.export_page import render_export_page  # noqa: E402
from ui.projects_page import render_projects_page  # noqa: E402

st.set_page_config(page_title="article_to_video", page_icon="🎞️", layout="wide")

PAGES = {
    "📥 新建项目": render_import_page,
    "🗺️ 大纲": render_outline_page,
    "📝 口播稿": render_script_page,
    "🔊 配音": render_voice_page,
    "🎞️ 分镜": render_storyboard_page,
    "🎬 合成导出": render_export_page,
    "📚 项目": render_projects_page,
}

with st.sidebar:
    st.title("article_to_video")
    st.caption("文章 → 讲解视频（P5：合成与导出）")
    page = st.radio("页面", list(PAGES), label_visibility="collapsed")

PAGES[page]()
