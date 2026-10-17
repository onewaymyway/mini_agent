"""📊 度量与日志：字数与配额曲线、🔴 与修改轮数、用量与失败、步骤详情（prompt/回执）、worker 日志（M4）。"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from novel_writer import tool_api
from ui.components import show_error


def render_metrics(ws: str) -> None:
    res = tool_api.metrics(ws)
    if not res["ok"]:
        show_error(res)
        return
    m = res["data"]
    if not m["chapters"]:
        st.info("还没有章节数据。")
    else:
        df = pd.DataFrame(m["chapters"]).set_index("chapter")
        c1, c2 = st.columns(2)
        c1.markdown("**字数 与 配额**")
        c1.bar_chart(df[["words", "quota"]])
        c2.markdown("**🔴 硬错误 与 修改轮数**")
        c2.bar_chart(df[["red", "revise_rounds"]])
        st.dataframe(df[["title", "words", "quota", "red", "machine_hard", "revise_rounds", "progress", "current"]],
                     use_container_width=True)
    c3, c4 = st.columns(2)
    c3.metric("LLM / 步骤执行次数", m["total_runs"])
    c4.metric("失败或阻塞", m["total_failed"])
    if m["usage_by_skill"]:
        st.markdown("**按技能的用量**")
        st.dataframe(pd.DataFrame(m["usage_by_skill"]), hide_index=True, use_container_width=True)

    with st.expander("最近的步骤记录", expanded=False):
        st.dataframe(pd.DataFrame([{"步骤": r.get("step"), "状态": r.get("status"), "耗时(秒)": r.get("duration_sec"),
                                    "尝试": r.get("attempt"), "错误": r.get("error") or ""} for r in m["recent"]]),
                     hide_index=True, use_container_width=True)

    st.subheader("步骤详情（prompt 与回执）")
    ids = [r.get("step") for r in m["recent"] if r.get("step")]
    if ids:
        pick = st.selectbox("选择步骤", sorted(set(ids)), key="mt_step")
        d = tool_api.step_detail(ws, pick)
        if d["ok"] and d["data"]["runs"]:
            info = d["data"]
            st.caption(f"共 {info['runs']} 次执行，显示最近一次：{info.get('run_dir')}")
            if info.get("receipt"):
                st.json(info["receipt"])
            if info.get("prompt"):
                with st.expander("prompt", expanded=False):
                    st.code(info["prompt"], language="markdown")
        else:
            st.caption("该步骤还没有执行记录。")

    with st.expander("worker 日志（最后 40 行）", expanded=False):
        st.code(m["log_tail"] or "（暂无）", language=None)
