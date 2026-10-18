"""🧩 Skill 库：23 个 skill 在本项目中的角色（只读）。"""

from __future__ import annotations

import streamlit as st

from novel_writer import tool_api
from ui.components import show_df

_EXECUTOR_LABEL = {"python": "Python 代码", "llm": "LLM（skill_agent）", "hybrid": "混合"}


def page_skills() -> None:
    st.header("🧩 Skill 库")
    result = tool_api.list_skills()
    if not result["ok"]:
        st.error(result["error"]["message"])
        return
    rows = result["data"]["skills"]
    st.caption(f"共 {len(rows)} 个 skill。执行器：Python = 确定性工作由代码完成；LLM = 由 mini_agent 的 skill_agent 步骤执行。")
    table = [
        {
            "skill": r["name"],
            "层": r["layer"],
            "执行器": _EXECUTOR_LABEL.get(r["executor"], r["executor"]),
            "使用页面": "、".join(r["pages"]),
            "职责": r["summary"],
            "状态": "✅" if r["present"] and r["catalogued"] and r["name_matches_dir"] else "⚠️",
        }
        for r in rows
    ]
    show_df(table, width="stretch", hide_index=True)
    names = [r["name"] for r in rows if r["present"]]
    pick = st.selectbox("查看 SKILL.md", names)
    row = next(r for r in rows if r["name"] == pick)
    try:
        with open(row["path"], encoding="utf-8") as f:
            st.markdown(f.read())
    except OSError as exc:
        st.error(f"读取失败：{exc}")
