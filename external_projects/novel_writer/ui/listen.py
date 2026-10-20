"""🎧 听书：浏览器 speechSynthesis 自动连播（M7）。

数据组装在 novel_writer/listen.py（纯逻辑）；播放器是 ui/listen_component/index.html 的
Streamlit 自定义组件。组件把「当前章节、语速、音色」回传给 Python，写入
_studio/reading_state.json，因此刷新浏览器或重启 Streamlit 后会恢复。
播放器完全在浏览器端运行，不生成音频文件，不调用 LLM。
"""

from __future__ import annotations

from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

from novel_writer import reading_state, tool_api
from novel_writer.listen import build_payload
from ui.components import show_error

_COMPONENT_DIR = Path(__file__).resolve().parent / "listen_component"
_player = components.declare_component("novel_listen_player", path=str(_COMPONENT_DIR))


def render_listen(ws: str, rows: list) -> None:
    """渲染听书区块。rows 为 tool_api.chapters 返回的章节行。"""
    readable = [r for r in rows if r.get("words")]
    with st.expander("🎧 听书（浏览器朗读，自动连播）", expanded=False):
        if not readable:
            st.info("还没有可朗读的正文。")
            return
        numbers = [r["chapter"] for r in readable]
        titles = {r["chapter"]: r["title"] for r in readable}
        state = reading_state.load(ws)

        # 新会话（刷新/重启后）从持久化状态恢复起始章节；同一会话内以用户选择为准。
        if "listen_start" not in st.session_state:
            saved = reading_state.chapter_or_none(state.get("last_listen_chapter"), numbers)
            st.session_state["listen_start"] = saved if saved is not None else numbers[0]
        start = st.selectbox(
            "从第几章开始连播",
            numbers,
            format_func=lambda n: f"第{n}章 {titles[n]}",
            key="listen_start",
        )

        chapters = []
        for n in [x for x in numbers if x >= start]:
            detail = tool_api.chapter(ws, n)
            if not detail["ok"]:
                show_error(detail)
                return
            chapters.append({"chapter": n, "title": titles[n], "text": detail["data"]["text"] or ""})
        payload = build_payload(chapters)
        if not payload["segments"]:
            st.info("所选章节没有可朗读的正文。")
            return
        st.caption(f"共 {len(payload['chapters'])} 章 · {len(payload['segments'])} 段。播放会一直连到末章。")

        # key 只随用户选择的起始章节变化：播放中自动切章不会重建 iframe，也不会打断朗读。
        report = _player(
            payload=payload,
            rate=state.get("listen_rate", 1.0),
            voice=state.get("listen_voice", ""),
            key=f"novel_listen_{start}",
            default=None,
        )
        if isinstance(report, dict):
            ch = report.get("ch")
            if isinstance(ch, int) and ch in numbers:
                reading_state.update(
                    ws,
                    last_listen_chapter=ch,
                    listen_rate=float(report.get("rate", 1.0)),
                    listen_voice=str(report.get("voice", "")),
                )
