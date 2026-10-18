"""🗺 规划：蓝图查看与章节表编辑（M2）。只允许改已有章节；保存后下游产物会标为需重跑。"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from novel_writer import tool_api
from ui.components import show_error, show_df

_EDIT_COLS = ["id", "title", "act", "chapter_type", "function", "word_budget", "tension_level", "characters_present"]


def render_planning(ws: str) -> None:
    res = tool_api.planning(ws)
    if not res["ok"]:
        show_error(res)
        return
    view = res["data"]
    if not view["exists"]:
        st.info("还没有蓝图。请到「🧭 总控台」，运行 plan_blueprint 步骤（它是强制检查点，完成后需要你批准）。")
        _docs(view["docs"])
        return

    meta = view["meta"]
    bits = [f"{k}：{meta[k]}" for k in ("title", "theme", "target_length", "platform", "ending_type") if meta.get(k)]
    if bits:
        st.caption("　·　".join(str(b) for b in bits))
    c1, c2 = st.columns(2)
    c1.metric("章节数", view["total_chapters"] or len(view["chapters"]))
    c2.metric("主要角色", "、".join(view["main_characters"]) or "—")

    if view["acts"]:
        with st.expander("幕结构", expanded=False):
            show_df(pd.DataFrame([
                {"幕": a.get("act"), "名称": a.get("name"), "章节": str(a.get("chapter_range")),
                 "核心任务": a.get("core_mission"), "字数预算": a.get("word_budget")}
                for a in view["acts"]
            ]), width="stretch", hide_index=True)

    st.subheader("章节表")
    st.caption("可编辑标题、功能、字数、张力与出场角色（用「、」分隔）。行数不能增减。保存后，受影响的已通过步骤会标为「需重跑」。")
    df = pd.DataFrame([{k: c.get(k) for k in _EDIT_COLS} for c in view["chapters"]], columns=_EDIT_COLS)
    edited = st.data_editor(
        df, key="chapter_editor", hide_index=True, width="stretch", num_rows="fixed",
        disabled=["id", "act", "chapter_type"],
        column_config={
            "title": st.column_config.TextColumn("标题"),
            "function": st.column_config.TextColumn("本章功能", width="large"),
            "word_budget": st.column_config.NumberColumn("字数预算", min_value=0, step=100),
            "tension_level": st.column_config.NumberColumn("张力", min_value=0, max_value=10, step=1),
            "characters_present": st.column_config.TextColumn("出场角色"),
        },
    )
    if st.button("保存章节表", key="plan_save", type="primary"):
        rows = []
        for rec in edited.to_dict("records"):
            rows.append({k: (None if _is_nan(v) else v) for k, v in rec.items()})
        r = tool_api.save_chapters(ws, rows)
        if not r["ok"]:
            show_error(r)
        else:
            d = r["data"]
            if not d["changed_chapters"]:
                st.info("没有改动。")
            else:
                st.success(f"已保存，改动章节：{d['changed_chapters']}")
            if d["main_characters_changed"]:
                st.warning(f"主要角色发生变化：{'、'.join(d['main_characters']) or '无'}。"
                           "新的人设设计步骤会出现在总控台。")
            if d["stale"]:
                st.warning("以下已通过的步骤因此需要重跑：" + "、".join(d["stale"]))

    _docs(view["docs"])


def _is_nan(v) -> bool:
    try:
        return v != v  # NaN
    except Exception:  # noqa: BLE001
        return False


def _docs(docs: dict) -> None:
    labels = {"theme": "主题设计", "pacing": "节奏规划", "opening": "前三章开篇设计", "readable": "章节大纲（可读版）"}
    for key, label in labels.items():
        text = docs.get(key)
        if text:
            with st.expander(label, expanded=False):
                st.markdown(text)
