"""AppTest 用的最小应用：渲染 📚 章节简述页（环境变量 BR_WS 指向小说目录）。"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import streamlit as st  # noqa: E402

from novel_writer import brief as _BR  # noqa: E402,F401
from ui.briefs import page_briefs  # noqa: E402

st.session_state.setdefault("ws", os.environ["BR_WS"])
page_briefs()
