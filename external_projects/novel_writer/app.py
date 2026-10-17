"""novel_writer — Streamlit 入口（`streamlit run app.py`）。

薄入口：页面逻辑在 `ui/` 下各模块。M2：书架 / 新建向导 / 驾驶舱（总控台·规划·设定库·线索）/ Skill 库。
写作台、审校、阅读等页面按 M3–M4 逐步加入。
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import streamlit as st  # noqa: E402

from ui.cockpit import page_cockpit  # noqa: E402
from ui.shelf import page_shelf  # noqa: E402
from ui.skills_page import page_skills  # noqa: E402
from ui.wizard import page_wizard  # noqa: E402

PAGES = {
    "📚 书架": page_shelf,
    "➕ 新建小说": page_wizard,
    "🧭 驾驶舱": page_cockpit,
    "🧩 Skill 库": page_skills,
}


def main() -> None:
    st.set_page_config(page_title="novel_writer", page_icon="📖", layout="wide")
    with st.sidebar:
        st.markdown("### 📖 novel_writer")
        st.caption("小说生成工作台")
        choice = st.radio("导航", list(PAGES), label_visibility="collapsed", key="nav")
        current = st.session_state.get("ws")
        if current:
            st.caption(f"当前：{Path(current).name}")
    PAGES[choice]()


if __name__ == "__main__":
    main()
