"""ui/projects_page.py — 「📚 项目」：项目列表，并可修改作者名与文字水印（写入 project.json）。"""

from __future__ import annotations

import streamlit as st

from article_to_video.paths import projects_root
from article_to_video.project_store import (
    WATERMARK_MODES,
    WATERMARK_POSITIONS,
    list_projects,
    load_project,
    update_branding,
)
from ui.import_page import POSITION_LABELS
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
    data = load_project(project_dir)
    b = data.get("branding", {})
    wm = b.get("watermark", {})
    cr = b.get("credits", {})

    st.subheader("署名与水印")
    st.caption("修改只写入项目设置，不改动源文章与已解析的块数据。")
    with st.form("branding_form"):
        c1, c2 = st.columns(2)
        author = c1.text_input("作者名", value=b.get("author", ""))
        handle = c2.text_input("作者账号（可选）", value=b.get("author_handle", ""))
        c3, c4 = st.columns(2)
        source_title = c3.text_input("原文标题", value=b.get("source_title", ""))
        source_url = c4.text_input("原文链接（可选）", value=b.get("source_url", ""))

        w1, w2, w3, w4 = st.columns([3, 2, 2, 2])
        wm_text = w1.text_input("水印文字", value=wm.get("text", ""), max_chars=60,
                                help="留空则不加水印")
        wm_pos = w2.selectbox("位置", WATERMARK_POSITIONS,
                              index=WATERMARK_POSITIONS.index(wm.get("position", "top_right")),
                              format_func=POSITION_LABELS.get)
        wm_mode = w3.selectbox("模式", WATERMARK_MODES,
                               index=WATERMARK_MODES.index(wm.get("mode", "static")),
                               help="static 固定；drift 缓慢移动（P6 实现）")
        wm_opacity = w4.slider("透明度", 0.05, 1.0, float(wm.get("opacity", 0.35)), 0.05)

        k1, k2, k3, k4 = st.columns(4)
        cr_title = k1.checkbox("片头标题卡署名", value=cr.get("title_card", True))
        cr_lower = k2.checkbox("下三分之一条", value=cr.get("lower_third", True))
        cr_end = k3.checkbox("片尾卡", value=cr.get("end_card", True))
        cr_corner = k4.checkbox("角标（全程）", value=cr.get("corner_tag", False))
        ai_on = st.checkbox("片尾 AI 生成声明", value=b.get("ai_disclosure", {}).get("enabled", True))

        saved = st.form_submit_button("保存署名与水印", type="primary")

    if saved:
        warnings = update_branding(project_dir, {
            "author": author,
            "author_handle": handle,
            "source_title": source_title,
            "source_url": source_url,
            "credits": {"title_card": cr_title, "lower_third": cr_lower,
                        "end_card": cr_end, "corner_tag": cr_corner},
            "watermark": {"enabled": bool(wm_text.strip()), "text": wm_text, "position": wm_pos,
                          "mode": wm_mode, "opacity": wm_opacity,
                          "scale": wm.get("scale", 0.06), "margin": wm.get("margin", 0.03)},
            "ai_disclosure": {"enabled": ai_on, "text": b.get("ai_disclosure", {}).get("text", "")},
        })
        st.success("已保存")
        for w in warnings:
            st.warning(w)

    with st.expander("项目信息（project.json）"):
        st.json(load_project(project_dir))
