"""novel_writer — Streamlit 入口（`streamlit run app.py`）。

薄入口：页面逻辑在 `ui/` 下各模块。M0 只有书架/新建/Skill 库三个页面，
单本小说的驾驶舱（总控台/规划/设定库/写作台…）按里程碑 M2–M4 逐步加入。
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import streamlit as st  # noqa: E402

from ui.shelf import page_create, page_shelf  # noqa: E402
from ui.skills_page import page_skills  # noqa: E402

PAGES = {
    "📚 书架": page_shelf,
    "➕ 新建小说": page_create,
    "🧩 Skill 库": page_skills,
}


def main() -> None:
    st.set_page_config(page_title="novel_writer", page_icon="📖", layout="wide")
    with st.sidebar:
        st.markdown("### 📖 novel_writer")
        st.caption("小说生成工作台")
        choice = st.radio("导航", list(PAGES), label_visibility="collapsed")
    PAGES[choice]()


if __name__ == "__main__":
    main()
