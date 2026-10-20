"""👥 设定库：主要角色的人设卡（可编辑）、人物档案与世界圣经（只读）（M2）。"""

from __future__ import annotations

import streamlit as st

from novel_writer import tool_api
from ui.components import show_error


def render_codex(ws: str) -> None:
    res = tool_api.codex(ws)
    if not res["ok"]:
        show_error(res)
        return
    view = res["data"]
    if not view["main_characters"] and not view["other_cards"]:
        st.info("还没有主要角色。蓝图生成后，出现在 ≥3 章的角色会在这里出现人设卡。")
    for card in view["cards"]:
        st.subheader(card["name"])
        if card["text"] is None:
            st.caption("尚未生成人设卡：请在总控台运行对应的 design_character 步骤。")
            continue
        text = st.text_area("人设卡（Markdown）", value=card["text"], height=260, key=f"card_{card['name']}")
        if st.button("保存人设卡", key=f"card_save_{card['name']}"):
            r = tool_api.save_card(ws, card["name"], text)
            if r["ok"]:
                st.success("已保存")
                if r["data"]["stale"]:
                    st.warning("以下已通过的步骤因此需要重跑：" + "、".join(r["data"]["stale"]))
            else:
                show_error(r)
    for card in view["other_cards"]:
        with st.expander(f"其它人设卡：{card['name']}"):
            st.markdown(card["text"] or "")
    with st.expander("人物档案（角色文件 + 索引）", expanded=False):
        if view.get("characters_error"):
            st.error("人物档案读取失败，请按提示修正对应文件：\n- " + "\n- ".join(view["characters_error"]))
        else:
            st.json(view["characters"] or {"提示": "尚未生成"})
    with st.expander("世界圣经 world_bible.json", expanded=False):
        st.json(view["world_bible"] or {"提示": "尚未生成"})
