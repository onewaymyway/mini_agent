"""ui/auto_added_page.py — 补建角色审核（Phase 3）。补建实体只有确认后才会推进到定妆图与配音。"""

from __future__ import annotations

import streamlit as st

from novel_to_video import tool_api
from ui.common import DEFAULT_PROJECTS_ROOT
from ui.chapter_page import _pick_project

STATUS = {"missing": "未生成", "pending": "待确认", "approved": "已通过", "stale": "需重审", "failed": "失败"}


def page_auto_added() -> None:
    st.header("补建角色审核")
    st.caption("章节生成过程中出现的新角色会先以草稿进入库中，确认后才会生成定妆图并参与配音。")
    root = st.text_input("项目存放目录", value=DEFAULT_PROJECTS_ROOT, key="auto_root")
    pdir = _pick_project(root)
    if not pdir:
        return
    res = tool_api.auto_added_queue(pdir)
    if not res["ok"]:
        st.error(res["error"]["message"])
        return
    rows = res["data"]
    if not rows:
        st.info("当前没有待审核的补建角色。")
        return
    for r in rows:
        with st.container(border=True):
            st.markdown(f"**{r['id']} · {r['name']}**（来自第 {r['chapter']} 章）")
            st.write(f"文本设定：{STATUS.get(r['text_status'], r['text_status'])}　"
                     f"定妆图：{STATUS.get(r['portrait_status'], r['portrait_status'])}")
            st.caption(r.get("description_zh") or "")
            st.code(r.get("visual_anchor_en") or "", language="text")
            st.caption(f"声音：{r.get('voice_profile') or '—'}")
