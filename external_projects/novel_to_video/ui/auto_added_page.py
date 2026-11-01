"""ui/auto_added_page.py — 补建角色审核（Phase 3）。补建实体只有确认后才会推进到定妆图与配音。

卡片带操作按钮（通过设定、生成/通过定妆图、编辑设定），与章节页的“补建角色”区使用同一套组件。
"""

from __future__ import annotations

from typing import Optional

import streamlit as st

from novel_to_video import tool_api
from ui.common import DEFAULT_PROJECTS_ROOT
from ui.chapter_page import _pick_project

STATUS = {"missing": "未生成", "pending": "待确认", "approved": "已通过", "stale": "需重审", "failed": "失败"}

STEPS_HELP = ("处理顺序：① 展开「查看与编辑设定」，核对设定并选择性别（模型起草的性别不算确认），点「保存修改」"
              "→ ② 点「通过设定」→ ③ 点「生成定妆图」→ ④ 看图后点「通过定妆图」→ ⑤ 回到「章节生产」页重新运行本章。")


def render_auto_added(pdir: str, chapter: Optional[int] = None, *, key_prefix: str = "aa") -> int:
    """渲染补建角色卡片（chapter 不为空时只显示该章），返回其中还没完全通过的数量。"""
    from ui.assets_page import entity_card
    q = tool_api.auto_added_queue(pdir)
    if not q["ok"]:
        st.error(q["error"]["message"])
        return 0
    ids = [r["id"] for r in q["data"] if chapter is None or r["chapter"] == chapter]
    if not ids:
        return 0
    status = tool_api.get_visual_status(pdir)
    if not status["ok"]:
        st.error(status["error"]["message"])
        return 0
    rows = [r for r in status["data"]["rows"] if r["kind"] == "character" and r["id"] in ids]
    open_n = sum(1 for r in rows if not (r["text_status"] == "approved" and r["portrait_status"] == "approved"))
    st.caption(STEPS_HELP)
    for r in rows:
        entity_card(pdir, r, with_generate=True)
    return open_n


def page_auto_added() -> None:
    st.header("补建角色审核")
    st.caption("章节生成过程中出现的新角色会先以草稿进入库中，确认后才会生成定妆图并参与配音。")
    root = st.text_input("项目存放目录", value=DEFAULT_PROJECTS_ROOT, key="auto_root")
    pdir = _pick_project(root)
    if not pdir:
        return
    from ui.joblog_panel import render_job_panel
    n = render_auto_added(pdir)
    if not tool_api.auto_added_queue(pdir)["data"]:
        st.info("当前没有待审核的补建角色。剧本中出现库里没有的说话人时，运行章节流水线到 C-5 会自动起草。")
    render_job_panel(pdir, ["assets"], title="定妆图生成日志", height=200)
