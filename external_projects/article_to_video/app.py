"""app.py — article_to_video 的 Streamlit 界面（端口 8507）。"""

import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

import streamlit as st  # noqa: E402

from ui.batch_page import render_batch_page  # noqa: E402
from ui.import_page import render_import_page  # noqa: E402
from ui.cockpit_page import render_cockpit_page  # noqa: E402
from ui.projects_page import render_projects_page  # noqa: E402
from ui.settings_page import render_settings_page  # noqa: E402

st.set_page_config(page_title="article_to_video", page_icon="🎞️", layout="wide")

# R7：驾驶舱是主入口，大纲、口播稿、配音、分镜、合成导出都在驾驶舱的步骤区内。
# 侧边栏只保留与项目全局相关的入口。
PAGES = {
    "🧭 驾驶舱": render_cockpit_page,
    "📥 新建项目": render_import_page,
    "📦 批量生成": render_batch_page,
    "📚 项目": render_projects_page,
    "⚙️ 设置": render_settings_page,
}

with st.sidebar:
    st.title("article_to_video")
    st.caption("文章 → 讲解视频（R7：驾驶舱为主入口）")
    page = st.radio("页面", list(PAGES), label_visibility="collapsed")

PAGES[page]()
