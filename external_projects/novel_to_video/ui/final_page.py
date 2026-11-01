"""ui/final_page.py — 全书合成（Phase 4：F-1 检查、封面、人工确认、合成与成片校验）。"""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from novel_to_video import tool_api
from ui.common import DEFAULT_PROJECTS_ROOT
from ui.chapter_page import _pick_project

LAYOUT_LABEL = {"center": "居中（默认）", "bottom_bar": "底部色条", "top_classic": "上方描边"}


def page_final() -> None:
    st.header("全书合成（F-1 至 F-4）")
    st.caption("先确认所有章节完成，再设置封面与转场，然后人工确认，最后合成。任何挡位都需要人工确认。")
    root = st.text_input("项目存放目录", value=DEFAULT_PROJECTS_ROOT, key="final_root")
    pdir = _pick_project(root)
    if not pdir:
        return

    chk = tool_api.final_check(pdir)
    if not chk["ok"]:
        st.error(chk["error"]["message"])
        return
    d = chk["data"]

    st.markdown("#### F-1 章节检查")
    if d["ready"]:
        st.success(f"选中的 {len(d['chapters'])} 章均已完成。")
    else:
        for p in d["problems"]:
            st.warning(p)
    if d["final_stale"]:
        st.warning("全书成片已过期（某章节内容已变更），请重新确认并合成。")

    st.markdown("#### F-2 封面（可选）")
    cov = d["cover"]
    enabled = st.checkbox("启用片头封面", value=cov["enabled"], key="cov_on")
    title = st.text_input("封面标题", value=cov.get("title") or "", key="cov_title")
    layout = st.selectbox("布局", list(LAYOUT_LABEL), index=list(LAYOUT_LABEL).index(cov["layout"]),
                          format_func=lambda k: LAYOUT_LABEL[k], key="cov_layout")
    dur = st.number_input("封面时长（秒）", min_value=1.0, max_value=8.0, value=float(cov["duration_sec"]),
                          step=0.5, key="cov_dur")
    if st.button("保存封面设置"):
        r = tool_api.set_cover(pdir, enabled=enabled, title=title, layout=layout, duration_sec=dur)
        (st.success if r["ok"] else st.error)("已保存" if r["ok"] else r["error"]["message"])
    if enabled and st.button("生成 / 更新封面"):
        _start(tool_api.start_cover_background(pdir), "封面生成已开始")
    if cov["cover_exists"]:
        st.image(str(Path(pdir) / "global" / "assets" / "cover.png"), width=240)

    st.markdown("#### F-3 转场与合成")
    transition = st.radio("章节间转场", ["cut", "fade"], format_func=lambda t: "硬切" if t == "cut" else "淡化（0.5 秒）",
                          horizontal=True, key="final_tr")
    if d["ready"]:
        conf = d["confirm"]
        if conf.get("confirmed"):
            st.info(f"已于 {conf['at']} 确认，可以合成。")
        else:
            note = st.text_input("确认备注（可选）", key="final_note")
            if st.button("我已审看成片，确认合成"):
                r = tool_api.confirm_final(pdir, note)
                (st.success if r["ok"] else st.error)("已确认" if r["ok"] else r["error"]["message"])
                st.rerun()
    if d["ready"] and d["confirm"].get("confirmed") and st.button("合成全书"):
        _start(tool_api.start_compose_background(pdir, transition=transition), "全书合成已开始，进度见下方日志")

    from ui.joblog_panel import render_job_panel
    render_job_panel(pdir, ["final"], title="全书合成日志", height=280)

    out = Path(pdir) / "exports" / "video.mp4"
    if out.is_file():
        st.markdown("#### 全书成片")
        st.video(str(out))


def _start(res: dict, ok_msg: str) -> None:
    if res["ok"]:
        st.toast(ok_msg)
    else:
        st.error(res["error"]["message"])
