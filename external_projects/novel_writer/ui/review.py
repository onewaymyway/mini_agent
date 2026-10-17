"""🔍 审校：审稿报告、机器校验、修改记录，以及修改循环升级后的人工决定（M3）。"""

from __future__ import annotations

import streamlit as st

from novel_writer import tool_api
from ui.components import STATUS_LABEL, show_error
from ui.writing import _pick_chapter

DECISIONS = ["重新核查（我已手动修改）", "接受风险继续"]


def render_review(ws: str) -> None:
    n = _pick_chapter(ws, "rv_chapter")
    if n is None:
        return
    res = tool_api.chapter(ws, n)
    if not res["ok"]:
        show_error(res)
        return
    d = res["data"]
    red_count = sum(1 for line in (d["review"] or "").splitlines() if line.strip().lstrip("-*|> ").startswith("🔴"))
    c1, c2, c3 = st.columns(3)
    c1.metric("🔴 硬错误", red_count)
    c2.metric("机器硬问题", len((d["machine"] or {}).get("hard") or []))
    c3.metric("机器提示", len((d["machine"] or {}).get("warn") or []))

    waiting = next((s for s in d["steps"] if s["id"].endswith(".revise_loop")
                    and s["status"] == "waiting_user"), None)
    if waiting:
        st.error("修改循环已升级人工：本章三轮修改后仍有硬问题，下一章不会开始。")
        for q in waiting.get("needs_user") or []:
            st.markdown(q.get("question", "").replace("\n", "  \n"))
        c_a, c_b = st.columns(2)
        if c_a.button(DECISIONS[0], key=f"rv_recheck_{n}"):
            _report(tool_api.answer_step(ws, waiting["id"], {"decision": DECISIONS[0]}))
        if c_b.button(DECISIONS[1], key=f"rv_accept_{n}"):
            _report(tool_api.answer_step(ws, waiting["id"], {"decision": DECISIONS[1]}))
        st.caption("「接受风险继续」会把未清零的问题写入修订记录；请确认这是你的决定。")

    st.subheader("审稿报告")
    st.markdown(d["review"] or "尚未审稿。")
    machine = d["machine"] or {}
    with st.expander("机器校验", expanded=False):
        for item in machine.get("hard") or []:
            st.error(item)
        for item in machine.get("warn") or []:
            st.warning(item)
        if not (machine.get("hard") or machine.get("warn")):
            st.success("没有发现问题")
    with st.expander("修改记录", expanded=False):
        st.markdown(d["revision_log"] or "（暂无）")
    with st.expander("本章各步骤", expanded=False):
        st.table([{"步骤": s["id"].split(".", 1)[1], "状态": STATUS_LABEL.get(s["status"], s["status"]),
                   "说明": s.get("error") or s.get("summary") or ""} for s in d["steps"]])


def _report(res: dict) -> None:
    if res["ok"]:
        st.success("已记录。请到总控台「▶ 运行到检查点」继续。")
    else:
        show_error(res)
