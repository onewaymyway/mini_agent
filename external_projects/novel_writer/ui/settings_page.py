"""⚙ 设置：步骤×闸门矩阵、阶段挡位、检查点、预算与禁用词（M4）。保存后从下一步起生效。"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from novel_writer import tool_api
from novel_writer.gates import GEAR_GATES
from novel_writer.settings import GEARS
from ui.components import GATE_LABEL, show_error

OVERRIDE_CHOICES = ["", "manual", "review", "auto", "skip"]
GEAR_CHOICES = ["", "G0", "G1", "G2", "G3", "G4"]


def render_settings(ws: str) -> None:
    res = tool_api.matrix(ws)
    if not res["ok"]:
        show_error(res)
        return
    m = res["data"]
    st.caption("每一格是这一步的闸门。「覆盖」优先于挡位预设；留空 = 按挡位与阶段挡位决定。检查点只能更严格，不能被放松。")

    st.subheader("阶段挡位")
    stage_cols = st.columns(max(1, len(m["stages"])))
    stage_gears = {}
    for col, stage in zip(stage_cols, m["stages"]):
        current = m["stage_gears"].get(stage, "")
        stage_gears[stage] = col.selectbox(f"阶段 {stage}", GEAR_CHOICES, index=GEAR_CHOICES.index(current)
                                           if current in GEAR_CHOICES else 0,
                                           format_func=lambda g: g or "（跟随全局）", key=f"mx_stage_{stage}")

    st.subheader("步骤 × 闸门")
    df = pd.DataFrame([{
        "步骤": r["id"], "标题": r["title"], "阶段": r["stage"], "类别": r["category"],
        "挡位预设": GATE_LABEL.get(r["preset"], r["preset"]), "覆盖": r["override"],
        "生效": GATE_LABEL.get(r["effective"], r["effective"]), "来源": r["source"],
    } for r in m["rows"]])
    edited = st.data_editor(
        df, key="mx_editor", hide_index=True, width="stretch", height=min(560, 40 + 35 * len(df)),
        disabled=["步骤", "标题", "阶段", "类别", "挡位预设", "生效", "来源"],
        column_config={"覆盖": st.column_config.SelectboxColumn("覆盖", options=OVERRIDE_CHOICES, required=False)},
    )

    st.subheader("检查点与预算")
    cp = m["checkpoints"]
    waive = st.checkbox("豁免蓝图确认（plan_blueprint 完成后自动通过）", value=bool(cp.get("waive_blueprint_confirm")))
    b = m["budget"]
    c1, c2, c3 = st.columns(3)
    max_ch = c1.number_input("每次运行最多章数", min_value=1, max_value=100, value=int(b.get("max_chapters_per_run") or 3))
    max_calls = c2.number_input("每次运行最多 LLM 调用", min_value=1, max_value=2000, value=int(b.get("max_llm_calls_per_run") or 60))
    max_min = c3.number_input("每次运行最多分钟", min_value=1, max_value=1440, value=int(b.get("max_wall_minutes_per_run") or 120))
    banned = st.text_area("禁用词（用逗号或换行分隔）", value="、".join(m["banned_words"]))

    if st.button("保存设置", type="primary", key="mx_save"):
        overrides = {}
        for rec in edited.to_dict("records"):
            step_id = rec["步骤"]
            value = rec["覆盖"]
            value = None if (value is None or value != value or value == "") else value
            overrides[step_id] = value
        words = [w.strip() for w in banned.replace("\n", "，").replace(",", "，").replace("、", "，").split("，") if w.strip()]
        result = tool_api.save_matrix(
            ws, overrides, {k: v for k, v in stage_gears.items() if v},
            {"checkpoints": {"waive_blueprint_confirm": waive},
             "budget": {"max_chapters_per_run": int(max_ch), "max_llm_calls_per_run": int(max_calls),
                        "max_wall_minutes_per_run": int(max_min)},
             "banned_words": words},
        )
        if result["ok"]:
            st.success("已保存，从下一步起生效。")
            st.rerun()
        else:
            show_error(result)

    with st.expander("挡位预设表（参考）", expanded=False):
        rows = []
        for cat, cols in GEAR_GATES.items():
            rows.append({"类别": cat, **{g: GATE_LABEL[cols[g]] for g in GEARS}})
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
