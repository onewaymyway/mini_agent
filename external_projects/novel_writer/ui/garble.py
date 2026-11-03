"""🧪 乱码体检：扫描章节总结、索引、人物/伏笔/蓝图等关键文件，列出疑似乱码及产出者（只读）。

G1 阶段只提供体检与「重跑产出者」入口（重置为待执行，之后到总控台运行）。
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from novel_writer import tool_api
from ui.components import show_error

_KIND_LABEL = {"declared": "声明产出", "agent": "agent 写入", "author": "作者手改", None: "—"}


_MODE_LABEL = {
    "off": "关闭（默认，不检测）",
    "report": "仅记录（步骤结束后检测，只写日志与收件箱）",
    "rerun": "自动重跑（G2/G3 实现前按「仅记录」处理）",
}


def _render_mode(ws: str) -> None:
    res = tool_api.get_novel(ws)
    if not res["ok"]:
        show_error(res)
        return
    gc = (res["data"].get("settings") or {}).get("garble_check") or {}
    modes = list(_MODE_LABEL)
    current = gc.get("mode", "off")
    choice = st.selectbox("步骤结束后的乱码检测", modes, index=modes.index(current) if current in modes else 0,
                          format_func=lambda m: _MODE_LABEL[m], key="garble_mode_sel")
    if choice != current and st.button("保存检测模式", key="garble_mode_save"):
        r = tool_api.update_settings(ws, {"garble_check": {**gc, "mode": choice}})
        if r["ok"]:
            st.success("已保存，从下一步起生效。")
            st.rerun()
        else:
            show_error(r)


def _render_guard_status(status: dict, max_reruns: int) -> None:
    """统一守卫运行状态：自动重做计数、挂起中的步骤、已接受风险的文件。"""
    counts = status.get("counts") or []
    waiting = status.get("waiting") or []
    accepted = status.get("accepted") or []
    if not (counts or waiting or accepted):
        st.caption("统一守卫：暂无自动重做、挂起或接受记录。")
        return
    st.markdown("**统一守卫状态**")
    if counts:
        st.dataframe(pd.DataFrame([{
            "产出者": c["producer"], "文件": c["file"],
            "已自动重做": f"{c['count']} / {max_reruns}",
        } for c in counts]), use_container_width=True, hide_index=True)
    if waiting:
        st.warning("以下步骤因乱码反复出现已挂起，请到总控台对该步骤作出决定（再重新生成 / 手动修改后重新核查 / 接受风险）：")
        for w in waiting:
            st.write(f"- 步骤 `{w['step']}`（{w['status']}），文件 `{w['file']}`，产出者 `{w['producer']}`")
    if accepted:
        st.caption("作者已接受风险（指纹不变时不再检测）：" + "、".join(accepted))


def render_garble_scan(ws: str) -> None:
    with st.expander("🧪 乱码体检（关键文件）", expanded=False):
        _render_mode(ws)
        st.caption("检查章节总结、全书章节索引、记忆摘要、人物/伏笔/蓝图与规划文档是否含乱码，并标出最后写入它的步骤。"
                   "只读，不修改任何文件。自动重跑需在「乱码检测」设置为 report/rerun 后，由流水线执行。")
        if not st.button("开始体检", key="garble_scan_btn"):
            return
        res = tool_api.scan_garble(ws)
        if not res["ok"]:
            show_error(res)
            return
        data = res["data"]
        if data["garbled_count"] == 0:
            st.success(f"已检查 {data['files_checked']} 个关键文件，未发现乱码。")
            _render_guard_status(data.get("reruns") or {}, data.get("max_reruns", 0))
            return
        st.warning(f"已检查 {data['files_checked']} 个关键文件，{data['garbled_count']} 个疑似乱码。")
        _render_guard_status(data.get("reruns") or {}, data.get("max_reruns", 0))
        rows = [{
            "文件": item["file"],
            "原因": "；".join(item["reasons"]),
            "产出者": item["writer"] or "—",
            "写入方式": _KIND_LABEL.get(item["writer_kind"], item["writer_kind"] or "—"),
            "处理建议": item["note"],
        } for item in data["garbled"]]
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        for idx, item in enumerate(data["garbled"]):
            if not item["auto_rerunnable"]:
                continue
            if st.button(f"重跑产出者 {item['writer']}（{item['file']}）", key=f"garble_rerun_{idx}"):
                r = tool_api.rerun_step(ws, item["writer"])
                if r["ok"]:
                    st.success(f"已将步骤 {item['writer']} 重置为待执行，请到总控台运行。")
                else:
                    show_error(r)
