"""app.py — article_to_video 的 Streamlit 界面（端口 8507）。"""

import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

import streamlit as st  # noqa: E402

from ui.import_page import render_import_page  # noqa: E402
from ui.projects_page import render_projects_page  # noqa: E402

st.set_page_config(page_title="article_to_video", page_icon="🎞️", layout="wide")

with st.sidebar:
    st.title("article_to_video")
    st.caption("文章 → 讲解视频（P0：导入与解析）")
    page = st.radio("页面", ["📥 新建项目", "📚 项目"], label_visibility="collapsed")

if page == "📥 新建项目":
    render_import_page()
else:
    render_projects_page()
