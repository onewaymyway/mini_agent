"""🎧 听书：浏览器 speechSynthesis 自动连播（M7）。

播放器是 ui/listen_component/index.html 的 Streamlit 自定义组件，始终加载全书的可听章节。
选择框「从第几章开始」只是起点：
- 用户主动改选择框 → 从所选章节开头重新开始（组件重新挂载）。
- 自动连播切章 → 选择框同步到实际所在章节（不重新挂载，不打断朗读）。
位置（章节 + 章内段落 + 段首锚点文字）、语速、音色都写入 _studio/reading_state.json，刷新或重启后恢复。

稳定性（避免进度被重置 / 播放被打断）：
- 起始位置只依据已保存的位置计算（reading_state.resolve_listen_position），与选择框当前值无关；
  保存的章节暂时读不到（正在重写）时，回退到最近的前一章开头，且不会改写已保存的位置。
- 传给播放器的章节载荷在同一会话内冻结：流水线生成新章节或改写正文时不再改变组件参数
  （旧版 Streamlit 里参数变化会让播放器重建），改为提示「有更新」，由用户点击后再载入。
- 选择框的选项与标题也取自这份冻结载荷，而不是实时章节列表：Streamlit 1.5x 起选项/format_func 是控件身份的一部分，
  新增章节或章节标题变化会让选择框被当作新控件而回到第一项。另有保护：选择框的值若与我们最后一次设定的值不一致
  （被重置），就恢复为设定值，绝不据此改写已保存的进度。
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
_NONCE = "listen_nonce"        # 用户改选择框/载入更新时递增，让播放器重新挂载
_LAST_REPORT = "listen_last_report"
_PENDING = "listen_pending_sync"   # 待应用到选择框的章节（播放器切章后记下）
_PAYLOAD = "listen_payload_cache"  # 冻结的播放载荷：{"ws","nonce","sig","payload"}
_FALLBACK = "listen_sel_is_fallback"   # 选择框当前值是回退值（保存的章节暂时不可听）
_EXPECT = "listen_sel_expected"        # 我们最后一次有意设定/同步的选择框值（用来识别选择框被重置）


def _on_pick(ws: str) -> None:
    """用户主动选择起始章节：保存为新位置（章首），并让播放器重新挂载。"""
    chosen = st.session_state[_SEL]
    reading_state.update(ws, last_listen_chapter=chosen, listen_segment=0, listen_anchor="")
    st.session_state[_EXPECT] = chosen
    st.session_state[_FALLBACK] = False
    st.session_state[_NONCE] = st.session_state.get(_NONCE, 0) + 1
    st.session_state[_LAST_REPORT] = None


def _on_reload() -> None:
    """用户点「载入更新」：重建播放器与载荷；起点仍取已保存的位置，所以从原位置继续。"""
    st.session_state[_NONCE] = st.session_state.get(_NONCE, 0) + 1
    st.session_state[_LAST_REPORT] = None


def _signature(readable: list) -> list:
    return [[r["chapter"], r.get("words"), r.get("title")] for r in readable]


def render_listen(ws: str, rows: list) -> None:
    """渲染听书区块。rows 为 tool_api.chapters 返回的章节行。"""
    readable = [r for r in rows if r.get("words")]
    with st.expander("🎧 听书（浏览器朗读，自动连播）", expanded=False):
        if not readable:
            st.info("还没有可朗读的正文。")
            return
        state = reading_state.load(ws)

        # 播放器载荷：同一会话内冻结（见模块说明）。选择框的选项、标题、起点解析都以它为准。
        sig = _signature(readable)
        nonce = st.session_state.get(_NONCE, 0)
        cache = st.session_state.get(_PAYLOAD)
        if not (isinstance(cache, dict) and cache.get("ws") == ws and cache.get("nonce") == nonce):
            chapters = []
            for r in readable:
                detail = tool_api.chapter(ws, r["chapter"])
                if not detail["ok"]:
                    show_error(detail)
                    return
                chapters.append({"chapter": r["chapter"], "title": r["title"], "text": detail["data"]["text"] or ""})
            cache = {"ws": ws, "nonce": nonce, "sig": sig, "payload": build_payload(chapters)}
            st.session_state[_PAYLOAD] = cache
        payload = cache["payload"]
        if not payload["segments"]:
            st.info("没有可朗读的正文。")
            return
        playable = [c["ch"] for c in payload["chapters"]]
        titles = {c["ch"]: c["title"] for c in payload["chapters"]}
        start_ch, start_seg, is_fallback = reading_state.resolve_listen_position(state, playable)

        # 选择框控件渲染之前，才能修改它的 session_state。
        # 播放器切章后只记下待同步的章节（见文末），这里在下一次运行的开头应用。
        pending = st.session_state.pop(_PENDING, None)
        cur, expected = st.session_state.get(_SEL), st.session_state.get(_EXPECT)
        if pending in playable:
            desired = pending
            st.session_state[_FALLBACK] = False
        elif cur not in playable or (st.session_state.get(_FALLBACK) and not is_fallback):
            # 新会话（刷新/重启）、当前章节已不可听，或之前是回退值而保存的章节现在又可听了：回到保存的位置
            desired = start_ch
            st.session_state[_FALLBACK] = is_fallback
        elif expected in playable and cur != expected:
            # 选择框被 Streamlit 重置（控件身份变化会回到第一项）：恢复为我们设定的值；已保存的进度不受影响
            desired = expected
        else:
            desired = cur
        # 无论如何都在创建控件之前显式写入：Streamlit 1.50 里选项变化会换新控件身份，若不写入，
        # 即使 session_state 里原来有值，新控件也会从第一项开始。
        st.session_state[_SEL] = st.session_state[_EXPECT] = desired
        st.selectbox(
            "当前章节（改选则从该章开头重新连播）",
            playable,
            format_func=lambda n: f"第{n}章 {titles[n]}",
            key=_SEL,
            on_change=_on_pick,
            args=(ws,),
        )
        current = st.session_state[_SEL]

        st.caption(f"可听 {len(payload['chapters'])} 章 · 共 {len(payload['segments'])} 段。播放会一直连到末章。")
        if cache["sig"] != sig:
            st.info("章节内容有更新（新增或改写了正文）。为不打断当前朗读，播放器仍是旧内容；需要时点击载入，会从当前位置继续。")
            st.button("🔄 载入更新（从当前位置继续）", key="listen_reload", on_click=_on_reload)
        if is_fallback:
            st.warning(f"上次收听的第 {state.get('last_listen_chapter')} 章暂时无法读取（可能正在重写）。"
                       f"已在第 {start_ch} 章开头待命，保存的位置没有被改动；该章恢复后点「载入更新」即可回到原位置。")

        report = _player(
            payload=payload,
            rate=state.get("listen_rate", 1.0),
            voice=state.get("listen_voice", ""),
            resume_ch=start_ch,
            resume_seg=start_seg,
            resume_anchor=None if is_fallback else (state.get("listen_anchor") or None),
            key=f"novel_listen_{nonce}",
            default=None,
        )
        if isinstance(report, dict) and report != st.session_state.get(_LAST_REPORT):
            st.session_state[_LAST_REPORT] = report
            ch, seg = report.get("ch"), report.get("seg")
            if isinstance(ch, int) and ch in playable and isinstance(seg, int) and seg >= 0:
                reading_state.update(
                    ws,
                    last_listen_chapter=ch,
                    listen_segment=seg,
                    listen_anchor=str(report.get("anchor") or "") or None,
                    listen_rate=float(report.get("rate", 1.0)),
                    listen_voice=str(report.get("voice", "")),
                )
                if ch != current:
                    # 播放器已切到新章节：记下待同步的章节并重跑页面，由下一次运行在选择框渲染前应用。
                    # （不能在选择框渲染之后直接修改它，否则 Streamlit 会报错）
                    st.session_state[_PENDING] = ch
                    st.rerun()
