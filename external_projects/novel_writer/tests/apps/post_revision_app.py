"""AppTest 用的最小应用：把环境变量 RV_WS 指向的小说放进会话，再渲染「完稿修改」页。"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import streamlit as st  # noqa: E402

from ui.post_revision import page_post_revision  # noqa: E402

st.session_state.setdefault("ws", os.environ["RV_WS"])
page_post_revision()
