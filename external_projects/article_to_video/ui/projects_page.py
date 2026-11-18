"""ui/projects_page.py — 「📚 项目」：项目列表与项目信息；署名与水印设置统一在「⚙️ 设置」中。"""

from __future__ import annotations

import streamlit as st

from article_to_video.paths import projects_root
from article_to_video.project_store import list_projects, load_project
from ui.joblog_panel import render_job_panel


def render_projects_page() -> None:
    st.header("📚 项目")
    root = projects_root()
    projects = list_projects(root)
    if not projects:
        st.info("还没有项目。先到「📥 新建项目」导入一篇文章。")
        return

    st.dataframe(
        [{k: p.get(k, "") for k in ("id", "title", "author", "mode", "stage", "created_at")} for p in projects],
        use_container_width=True,
        hide_index=True,
    )
    broken = [p["id"] for p in projects if "error" in p]
    if broken:
        st.warning(f"以下项目的 project.json 无法读取：{', '.join(broken)}")

    ids = [p["id"] for p in projects if "error" not in p]
    if not ids:
        return
    pid = st.selectbox("选择项目", ids)
    project_dir = root / pid
    try:
        _projects_body(project_dir)
    finally:
        render_job_panel(project_dir, None, title="最近日志", height=180)


def _projects_body(project_dir) -> None:
    st.caption("署名与水印、作者信息已统一到「⚙️ 设置」的「当前项目」标签页，这里只显示项目信息。")

    with st.expander("项目信息（project.json）"):
        st.json(load_project(project_dir))
