"""🎧 听书：浏览器 speechSynthesis 自动连播（M7）。

播放器是 ui/listen_component/index.html 的 Streamlit 自定义组件，始终加载全书的可听章节。
选择框「从第几章开始」只是起点：
- 用户主动改选择框 → 从所选章节开头重新开始（组件重新挂载）。
- 自动连播切章 → 选择框同步到实际所在章节（不重新挂载，不打断朗读）。
位置（章节 + 章内段落）、语速、音色都写入 _studio/reading_state.json，刷新或重启后恢复。
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

_SEL = "listen_start"          # 选择框的 session key（表示当前所在/起始章节）
_NONCE = "listen_nonce"        # 用户改选择框时递增，让播放器重新挂载到新起点
_LAST_REPORT = "listen_last_report"
_PENDING = "listen_pending_sync"   # 待应用到选择框的章节（播放器切章后记下）


def _on_pick(ws: str) -> None:
    """用户主动选择起始章节：保存为新位置（章首），并让播放器重新挂载。"""
    chosen = st.session_state[_SEL]
    reading_state.update(ws, last_listen_chapter=chosen, listen_segment=0)
    st.session_state[_NONCE] = st.session_state.get(_NONCE, 0) + 1
    st.session_state[_LAST_REPORT] = None


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

        # 选择框控件渲染之前，才能修改它的 session_state。
        # 播放器切章后只记下待同步的章节（见文末），这里在下一次运行的开头应用。
        pending = st.session_state.pop(_PENDING, None)
        if pending in numbers:
            st.session_state[_SEL] = pending
        elif st.session_state.get(_SEL) not in numbers:
            # 新会话（刷新/重启），或当前章节已不可听：回退到上次收听位置（或不晚于它的最近章节）
            st.session_state[_SEL] = reading_state.resolve_chapter(state.get("last_listen_chapter"), numbers)
        st.selectbox(
            "当前章节（改选则从该章开头重新连播）",
            numbers,
            format_func=lambda n: f"第{n}章 {titles[n]}",
            key=_SEL,
            on_change=_on_pick,
            args=(ws,),
        )
        current = st.session_state[_SEL]

        # 播放器始终加载全书可听章节，计数与进度都按全书计算。
        chapters = []
        for n in numbers:
            detail = tool_api.chapter(ws, n)
            if not detail["ok"]:
                show_error(detail)
                return
            chapters.append({"chapter": n, "title": titles[n], "text": detail["data"]["text"] or ""})
        payload = build_payload(chapters)
        if not payload["segments"]:
            st.info("没有可朗读的正文。")
            return
        st.caption(f"可听 {len(payload['chapters'])} 章 · 共 {len(payload['segments'])} 段。播放会一直连到末章。")

        resume_seg = reading_state.resume_segment(state, current)
        report = _player(
            payload=payload,
            rate=state.get("listen_rate", 1.0),
            voice=state.get("listen_voice", ""),
            resume_ch=current if resume_seg is not None else None,
            resume_seg=resume_seg or 0,
            key=f"novel_listen_{st.session_state.get(_NONCE, 0)}",
            default=None,
        )
        if isinstance(report, dict) and report != st.session_state.get(_LAST_REPORT):
            st.session_state[_LAST_REPORT] = report
            ch, seg = report.get("ch"), report.get("seg")
            if isinstance(ch, int) and ch in numbers and isinstance(seg, int) and seg >= 0:
                reading_state.update(
                    ws,
                    last_listen_chapter=ch,
                    listen_segment=seg,
                    listen_rate=float(report.get("rate", 1.0)),
                    listen_voice=str(report.get("voice", "")),
                )
                if ch != current:
                    # 播放器已切到新章节：记下待同步的章节并重跑页面，由下一次运行在选择框渲染前应用。
                    # （不能在选择框渲染之后直接修改它，否则 Streamlit 会报错）
                    st.session_state[_PENDING] = ch
                    st.rerun()
