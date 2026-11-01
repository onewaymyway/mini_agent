"""ui/projects_page.py — 项目列表与单项目概览（只读，Phase 0）。"""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from novel_to_video import project_store as ps
from novel_to_video import tool_api

from .common import DEFAULT_PROJECTS_ROOT


def page_projects() -> None:
    st.header("项目列表")
    root = st.text_input("项目存放目录", value=DEFAULT_PROJECTS_ROOT, key="proj_root")
    listed = tool_api.list_projects(root)
    if not listed["ok"]:
        st.error(listed["error"]["message"])
        return
    rows = listed["data"]
    if not rows:
        st.info("这里还没有项目。先到「导入导出目录」页面创建一个。")
        return
    st.dataframe(rows, use_container_width=True, hide_index=True)

    choice = st.selectbox("查看项目", [r["dir"] for r in rows if "error" not in r],
                          format_func=lambda p: Path(p).name, key="proj_pick")
    if not choice:
        return
    info = ps.load_project(Path(choice))
    proj = info["project"]
    st.subheader(proj["title"])
    st.caption(f"源目录：{proj['source_dir']}　指纹：{proj['source_fingerprint']}")

    drift = tool_api.check_source_drift(choice)
    if drift["ok"] and drift["data"]["changed"]:
        st.warning("源导出目录自导入后已被修改（指纹不一致）。如需使用新内容，请重新导入。")

    c1, c2 = st.columns(2)
    c1.metric("选中章节", sum(1 for c in info["chapters"] if c["selected"]))
    c2.metric("角色 / 地点", f"{len(info['characters'])} / {len(info['locations'])}")

    st.markdown("#### 章节")
    st.dataframe([{"章号": c["no"], "章名": c["title"], "选中": c["selected"], "状态": c["status"],
                   "字数": c["char_count"]} for c in info["chapters"]],
                 use_container_width=True, hide_index=True)
    with st.expander("项目设置（project.json）"):
        st.json(proj)
    with st.expander("进度日志（PROGRESS.md）"):
        st.markdown((Path(choice) / ps.PROGRESS_FILE).read_text(encoding="utf-8"))
