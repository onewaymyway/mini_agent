"""✍ 写作台：选一章，查看蓝图要求、配额、情节任务、上下文包，编辑正文，给本章作者指导（M3）。"""

from __future__ import annotations

import streamlit as st

from novel_writer import ondemand
from novel_writer import tool_api
from ui.components import ASSIST_LABEL, STATUS_LABEL, show_error


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

    _render_assist(ws, n)

    with st.expander("本章作者指导与运行", expanded=False):
        note = st.text_area("给写作步骤的指导（下次写作时注入 prompt）", key=f"wr_note_{n}")
        if st.button("保存指导", key=f"wr_note_save_{n}"):
            from ui.components import show_error as _se

            r = tool_api.write_author_note(ws, f"ch{n:02d}.write_chapter", note)
            st.success("已保存") if r["ok"] else _se(r)
        st.caption("运行请到「总控台」：可以用 ▶ 运行到检查点 推进本章及之后的步骤。")


def _render_assist(ws: str, n: int) -> None:
    """AI 选段辅助（M5）：粘贴选段 → 选方式 → 后台生成候选稿 → 确认后采用。候选稿不会直接改正文。"""
    with st.expander("✨ AI 辅助（选段：润色 / 扩写 / 续写）", expanded=False):
        sel = st.text_area("要处理的原文段落（需与正文完全一致，从正文复制）", key=f"as_sel_{n}", height=100)
        c1, c2 = st.columns([1, 2])
        mode = c1.selectbox("方式", list(ASSIST_LABEL), format_func=lambda m: ASSIST_LABEL[m], key=f"as_mode_{n}")
        st.caption(ondemand.ASSIST_MODES[mode])
        ins = c2.text_input("补充要求（可选）", key=f"as_ins_{n}")
        if st.button("提交并在后台生成候选稿", key=f"as_go_{n}"):
            req = tool_api.request_assist(ws, n, mode, sel, ins)
            if not req["ok"]:
                show_error(req)
            else:
                run = tool_api.run_novel(ws, mode="single", step_id=req["data"]["step"])
                if run["ok"]:
                    st.toast("已提交，稍后点「刷新」查看候选稿。")
                else:
                    show_error(run)

        rows = (tool_api.assists(ws, n).get("data") or {}).get("assists") or []
        for row in reversed(rows[-5:]):
            st.markdown(f"**{ASSIST_LABEL.get(row['mode'], row['mode'])}**　{STATUS_LABEL.get(row['status'], row['status'])}")
            if row.get("error"):
                st.error(row["error"])
            if row.get("adopted_at"):
                st.caption("已采用。")
            elif row.get("candidate"):
                st.text_area("候选稿（确认后再采用）", value=row["candidate"], height=120,
                             disabled=True, key=f"as_cand_{row['id']}")
                if st.button("采用此候选稿", key=f"as_adopt_{row['id']}"):
                    r = tool_api.adopt_assist(ws, row["id"])
                    if r["ok"]:
                        st.success(f"已采用（{r['data']['words']} 字）。")
                        if r["data"]["stale"]:
                            st.warning("以下已通过的步骤因此需要重跑：" + "、".join(r["data"]["stale"]))
                    else:
                        show_error(r)
