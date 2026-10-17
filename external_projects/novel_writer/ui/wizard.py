"""➕ 新建小说：新建向导（M2）。收集意图 → 创建工作目录 → 保存意图 → 直接进驾驶舱。"""

from __future__ import annotations

import streamlit as st

from novel_writer import intake as I
from novel_writer import tool_api
from novel_writer.settings import GEARS, QUALITIES
from ui.components import open_novel

_GENRE_HINT = "例：东方玄幻 / 悬疑推理 / 都市情感"


def page_wizard() -> None:
    st.header("➕ 新建小说")
    st.caption("第一步只收集意图；蓝图由「总控台」里的 plan_blueprint 步骤生成，你可以在批准前看到结果。")
    with st.form("novel_wizard"):
        st.subheader("基本信息")
        title = st.text_input("书名 *")
        workspace = st.text_input("工作目录（留空 = 默认 data/novels/<书名>_<时间戳>/）")
        c1, c2 = st.columns(2)
        total = c1.number_input("计划章节数", min_value=0, max_value=400, value=20, step=1,
                                help="0 表示由蓝图决定；蓝图生成后以蓝图为准")
        gear = c2.selectbox("控制挡位", list(GEARS), format_func=lambda g: f"{g} {GEARS[g]}", index=0)
        quality = st.selectbox("质量档位", list(QUALITIES), format_func=lambda q: QUALITIES[q], index=1)

        st.subheader("创作意图")
        values = {}
        for key, label, multiline in I.INTAKE_FIELDS:
            if key == "ending":
                values[key] = st.selectbox(label, I.ENDING_OPTIONS, index=0)
            elif multiline:
                values[key] = st.text_area(label, height=90, key=f"w_{key}")
            else:
                values[key] = st.text_input(label, key=f"w_{key}", placeholder=_GENRE_HINT if key == "genre" else "")
        submitted = st.form_submit_button("创建小说", type="primary")

    if not submitted:
        return
    if not title.strip():
        st.warning("请填写书名")
        return
    created = tool_api.create_novel(
        title.strip(), workspace=workspace.strip() or None, gear=gear, quality=quality,
        total_chapters_planned=int(total),
    )
    if not created["ok"]:
        st.error(created["error"]["message"])
        return
    path = created["data"]["workspace"]
    saved = tool_api.save_intake(path, values)
    if not saved["ok"]:
        st.warning(f"小说已创建，但意图保存失败：{saved['error']['message']}")
    st.success(f"已创建：{path}")
    st.button("进入驾驶舱 →", key="wizard_enter", on_click=open_novel, args=(path,))
