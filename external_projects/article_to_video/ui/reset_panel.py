"""ui/reset_panel.py — 「清空产出并重新生成」（R18）：每个阶段页面共用的控件。

  * 范围：本章 / 全部章节（大纲与导出固定为全部）；
  * 起点：默认为本页阶段，可选更早或更晚的阶段；
  * 生图缓存：默认保留（重新生图会产生费用），可勾选同时清空；
  * 确认：必须勾选确认才能执行。
若传入 regenerate，且起点等于本页阶段，则清空后立即在后台重新生成；否则只清空，并提示需要手动继续。
"""

from __future__ import annotations

from typing import Callable, List, Optional, Sequence

import streamlit as st

from article_to_video import joblog
from article_to_video.pipeline.gates import PipelineError
from article_to_video.pipeline.reset import PROJECT_ORDER, reset_project
from article_to_video.planning.runner import load_spine
from ui import jobs

STAGE_LABELS = {"outline": "大纲", "script": "口播稿", "voice": "配音", "storyboard": "分镜",
                "render": "画面渲染", "compose": "章合成", "export": "成片导出"}
PER_PART = ("script", "voice", "storyboard", "render", "compose")

RegenFn = Callable[[object, List[str]], None]   # (pdir, 章 ID 列表) → 在后台执行


def _part_ids(pdir) -> List[str]:
    spine = load_spine(pdir)
    return [p["id"] for p in spine["parts"]] if spine else []


def render_reset_panel(pdir, *, stage: Optional[str], key: str, busy: bool = False,
                       regenerate: Optional[RegenFn] = None, confirmed_only: Sequence[str] = ()) -> None:
    """stage：本页阶段（None 表示驾驶舱，全局）；regenerate：清空后重新生成的动作。"""
    part_ids = _part_ids(pdir)
    with st.expander("清空产出并重新生成（会删除产出，需要确认）"):
        st.caption("清空会删除所选范围从起点起的全部产出（包括下游产物）。生图缓存默认保留，"
                   "因为重新生图会产生费用。")
        per_part = stage is None or stage in PER_PART
        if per_part and part_ids:
            scope = st.radio("范围", ["本章", "全部章节"], horizontal=True, key=f"{key}_scope")
        else:
            scope = "全部章节"
        target = None
        if scope == "本章" and part_ids:
            target = st.selectbox("章", part_ids, key=f"{key}_part")
        default_from = stage if stage in PROJECT_ORDER else "script"
        frm = st.selectbox("从此阶段起清空", list(PROJECT_ORDER), index=list(PROJECT_ORDER).index(default_from),
                           format_func=lambda s: f"{STAGE_LABELS[s]}（{s}）", key=f"{key}_from")
        imgs = st.checkbox("同时清空生图缓存（会重新生图，产生费用）", value=False, key=f"{key}_imgs")
        ok = st.checkbox("我确认清空以上范围的产出", value=False, key=f"{key}_ok")

        can_regen = regenerate is not None and frm == stage
        label = "清空并重新生成" if can_regen else "仅清空"
        if frm != stage and regenerate is not None:
            st.caption(f"起点不是本页阶段，只会清空；之后请到「{STAGE_LABELS[frm]}」阶段继续生成。")
        if st.button(label, disabled=not ok or busy, key=f"{key}_go", type="primary"):
            if frm == "outline" or scope == "全部章节" or target is None:
                targets = list(part_ids)
            else:
                targets = [target]
            try:
                removed = reset_project(pdir, from_stage=frm, part_ids=targets, include_images=imgs, confirm=True)
            except PipelineError as exc:
                st.error(str(exc))
                return
            joblog.log(pdir, "pipeline", f"清空：从「{STAGE_LABELS[frm]}」起，范围 {scope}，"
                       f"删除 {len(removed)} 项" + ("（含生图缓存）" if imgs else ""), level="warn")
            st.success(f"已清空 {len(removed)} 项")
            if can_regen:
                run_targets = [t for t in targets if not confirmed_only or t in confirmed_only]
                if jobs.start(pdir, f"清空后重新生成 {STAGE_LABELS[stage]}（{len(run_targets)} 章）",
                              lambda: regenerate(pdir, run_targets), scope=stage or "pipeline"):
                    st.rerun()
                st.error("已有后台任务在运行，请稍候")
            else:
                st.rerun()
