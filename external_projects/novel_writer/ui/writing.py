"""✍ 写作台：选一章，查看蓝图要求、配额、情节任务、上下文包，编辑正文，给本章作者指导（M3）。"""

from __future__ import annotations

import streamlit as st

from novel_writer import tool_api
from ui.components import STATUS_LABEL, show_error


def _pick_chapter(ws: str, key: str):
    res = tool_api.chapters(ws)
    if not res["ok"]:
        show_error(res)
        return None
    rows = res["data"]["chapters"]
    if not rows:
        st.info("还没有章节。请先在总控台完成蓝图与建档。")
        return None
    labels = {r["chapter"]: f"第{r['chapter']}章 {r['title']}（{r['words']} 字）" for r in rows}
    return st.selectbox("章节", list(labels), format_func=lambda n: labels[n], key=key)


def render_writing(ws: str) -> None:
    n = _pick_chapter(ws, "wr_chapter")
    if n is None:
        return
    res = tool_api.chapter(ws, n)
    if not res["ok"]:
        show_error(res)
        return
    d = res["data"]
    c1, c2, c3 = st.columns(3)
    c1.metric("字数", d["words"])
    c2.metric("配额", d["quota"])
    c3.metric("偏离", f"{(d['words'] / d['quota'] - 1):+.0%}" if d["quota"] and d["words"] else "—")

    entry = d["entry"] or {}
    with st.expander("蓝图要求", expanded=True):
        st.markdown(f"**功能**：{entry.get('function') or '—'}")
        st.markdown(f"**出场角色**：{'、'.join(entry.get('characters_present') or []) or '—'}　"
                    f"**张力**：{entry.get('tension_level', '—')}")
        for scene in entry.get("key_scenes") or []:
            st.markdown(f"- {scene}")

    with st.expander("情节任务与步骤", expanded=False):
        tasks = d["tasks"] or {}
        st.markdown(f"待埋设：{len(tasks.get('plant') or [])}　待兑现：{len(tasks.get('resolve') or [])}")
        for t in (tasks.get("plant") or []) + (tasks.get("resolve") or []):
            st.markdown(f"- {t.get('id') or ''} {t.get('name') or ''} {t.get('note') or ''}")
        st.table([{"步骤": s["id"].split(".", 1)[1], "状态": STATUS_LABEL.get(s["status"], s["status"]),
                   "说明": s.get("error") or s.get("summary") or ""} for s in d["steps"]])

    with st.expander("上下文包（写作时注入的内容）", expanded=False):
        st.markdown(d["context"] or "尚未生成（运行本章的上下文步骤后出现）。")

    st.subheader("正文")
    text = st.text_area("正文（Markdown）", value=d["text"], height=420, key=f"wr_text_{n}")
    c_save, c_note = st.columns([1, 3])
    if c_save.button("保存正文", key=f"wr_save_{n}", type="primary"):
        r = tool_api.save_chapter(ws, n, text)
        if r["ok"]:
            st.success(f"已保存（{r['data']['words']} 字）")
            if r["data"]["stale"]:
                st.warning("以下已通过的步骤因此需要重跑：" + "、".join(r["data"]["stale"]))
        else:
            show_error(r)

    with st.expander("本章作者指导与运行", expanded=False):
        note = st.text_area("给写作步骤的指导（下次写作时注入 prompt）", key=f"wr_note_{n}")
        if st.button("保存指导", key=f"wr_note_save_{n}"):
            from ui.components import show_error as _se

            r = tool_api.write_author_note(ws, f"ch{n:02d}.write_chapter", note)
            st.success("已保存") if r["ok"] else _se(r)
        st.caption("运行请到「总控台」：可以用 ▶ 运行到检查点 推进本章及之后的步骤。")
