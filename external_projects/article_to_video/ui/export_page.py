"""ui/export_page.py — 「🎬 合成导出」（P5）：合成设置、分章合成（后台）、前 30 秒预览、成片导出与下载。"""

from __future__ import annotations

import json

import streamlit as st
from ui.joblog_panel import render_job_panel, render_page_status

from article_to_video.compose.export import export_final, safe_filename
from ui.reset_panel import render_reset_panel
from article_to_video.compose.part import compose_part, preview_part, video_dir
from article_to_video.paths import projects_root
from article_to_video.planning.runner import load_spine
from article_to_video.project_store import list_projects, load_project
from article_to_video.visual.storyboard import storyboard_status
from ui import jobs

_SUB_LABELS = {"auto": "自动（卡片已呈现则隐藏）", "always": "始终显示", "off": "关闭"}
_TR_LABELS = {"cut": "直接切换", "fade": "黑场淡入淡出", "dissolve": "叠化"}


def _effective_summary(pdir) -> None:
    from article_to_video import app_settings as S
    cmp_ = S.effective_group(pdir, "compose")
    br = S.effective_group(pdir, "branding")
    bgm = S.effective_group(pdir, "bgm")
    wm = br["watermark"]
    wm_text = (f"水印「{wm['text']}」（{wm['position']}，{wm['mode']}）" if wm["enabled"] else "无水印")
    st.caption(f"生效的合成设置：转场 {cmp_['transition']}（{cmp_['transition_sec']} 秒）　·　字幕 {cmp_['subtitles']}　·　"
               f"{wm_text}　·　背景音乐 {'开' if bgm['enabled'] else '关'}"
               "（在「⚙️ 设置」中修改；修改只影响导出，不重渲染画面）")


_PREVIEW_VIDEO: dict = {}   # {项目|章|章视频修改时间: 预览文件}，章重新合成后旧预览自动失效


def _preview_key(pdir, cid) -> str:
    src = video_dir(pdir, cid) / "part.mp4"
    return f"{pdir}|{cid}|{src.stat().st_mtime if src.is_file() else 0}"


def render_export_page() -> None:
    st.header("🎬 合成导出（P5）")
    root = projects_root()
    projects = [p for p in list_projects(root) if "error" not in p]
    if not projects:
        st.info("还没有项目。先到「📥 新建项目」导入文章。")
        return
    pid = st.selectbox("项目", [p["id"] for p in projects],
                       format_func=lambda i: next(p["title"] for p in projects if p["id"] == i))
    pdir = root / pid
    try:
        _export_body(pdir)
    finally:
        render_job_panel(pdir, ["export"], title="合成导出运行日志")


def _approval_banner(pdir) -> None:
    """流水线停在导出前等待终审时，明确告诉用户原因与做法（否则看起来像“没有导出”）。"""
    from article_to_video.pipeline.run import read_status

    status = read_status(pdir) or {}
    w = status.get("waiting") or {}
    if status.get("state") == "waiting" and w.get("stage") == "export":
        st.warning("流水线停在导出前，等待终审：确认成片后，到「🎬 驾驶舱」勾选「确认终审（允许导出）」，"
                   "再运行流水线，视频会自动导出并显示在本页。")


def _export_body(pdir) -> None:
    render_page_status(pdir, ["export"])
    spine = load_spine(pdir)
    if spine is None or not spine.get("confirmed"):
        st.info("请先在「🗺️ 大纲」生成并确认大纲（C0）。")
        return
    _effective_summary(pdir)
    _approval_banner(pdir)

    job = jobs.status(pdir)
    busy = job is not None and job["state"] == "running"
    if job:
        st.write(f"任务：{job['label']} — **{job['state']}**：{job['message']}")
        if busy:
            if st.button("⏹ 请求停止"):
                jobs.stop(pdir)
                st.rerun()
            if st.button("🔄 刷新状态"):
                st.rerun()

    st.subheader("各章状态")
    ready_ids = []
    for part in spine["parts"]:
        cid = part["id"]
        has_render = (pdir / "parts" / cid / "visuals" / "manifest.json").is_file()
        info_path = video_dir(pdir, cid) / "info.json"
        has_video = info_path.is_file() and (video_dir(pdir, cid) / "part.mp4").is_file()
        st_ = storyboard_status(pdir, cid)
        c = st.columns([3, 1, 1, 2, 2])
        c[0].write(f"**{cid} {part['title'][:22]}**")
        c[1].write("画面 ✅" if has_render else "画面 ⬜")
        c[2].write("分镜确认 ✅" if st_["confirmed"] else "分镜 ⬜")
        if has_video:
            info = json.loads(info_path.read_text(encoding="utf-8"))
            c[3].write(f"视频 ✅ {info['duration']:.1f} 秒")
        else:
            c[3].write("视频 ⬜")
        ready = has_render and st_["confirmed"]
        if ready:
            ready_ids.append(cid)
        if c[4].button("合成本章", key=f"comp_{cid}", disabled=not ready or busy):
            def run(cid=cid):
                compose_part(pdir, cid)
            if jobs.start(pdir, f"合成 {cid}", run, scope="export"):
                st.rerun()
        if has_video and st.button("预览前 30 秒", key=f"prev_{cid}", disabled=busy):
            def run_prev(cid=cid):
                _PREVIEW_VIDEO[_preview_key(pdir, cid)] = preview_part(pdir, cid, 30)

            if jobs.start(pdir, f"预览 {cid}", run_prev, scope="export"):
                st.rerun()
            st.error("已有后台任务在运行，请稍候")
        pv = _PREVIEW_VIDEO.get(_preview_key(pdir, cid)) if has_video else None
        if pv is not None and pv.is_file():
            st.video(str(pv))

    render_reset_panel(pdir, stage="export", key="rst_export", busy=busy,
                       regenerate=lambda p, ids: export_final(p))

    c1, c2 = st.columns(2)
    if c1.button("合成全部已就绪的章", disabled=busy or not ready_ids):
        def run_all():
            for cid in ready_ids:
                compose_part(pdir, cid)
        if jobs.start(pdir, f"合成 {len(ready_ids)} 章", run_all, scope="export"):
            st.rerun()

    all_ids = [p["id"] for p in spine["parts"]]
    done = all((video_dir(pdir, cid) / "part.mp4").is_file() for cid in all_ids)
    if c2.button("🎞️ 导出成片（后台）", disabled=busy or not done,
                 help="需要所有章都已合成" if not done else None):
        if jobs.start(pdir, "导出成片", lambda: export_final(pdir), scope="export"):
            st.rerun()

    _downloads(pdir)




def _downloads(pdir) -> None:
    """成片（两种方案统一放在这里）：每个成片标明方案、生成时间与大小，并提供下载。"""
    import time as _time

    from article_to_video.compose.export import list_all_exports

    rows = list_all_exports(pdir)
    if not rows:
        return
    st.subheader("成片（现有方案与新方案）")
    st.table([{"方案": r["scheme"], "文件": r["name"], "生成时间": _time.strftime("%Y-%m-%d %H:%M", _time.localtime(r["mtime"])),
               "大小(MB)": r["size_mb"]} for r in rows])
    for r in rows:
        video = r["path"]
        name = video.stem
        exports = video.parent
        st.markdown(f"**【{r['scheme']}】{video.name}**")
        st.video(str(video))
        cols = st.columns(4)
        cols[0].download_button("下载视频（mp4）", video.read_bytes(), file_name=video.name, mime="video/mp4",
                                key=f"dl_v_{name}")
        srt = exports / f"{name}.srt"
        if srt.is_file():
            cols[1].download_button("下载字幕（srt）", srt.read_bytes(), file_name=srt.name, key=f"dl_s_{name}")
        cover = exports / f"{name}_cover.png"
        if cover.is_file():
            cols[2].download_button("下载封面（png）", cover.read_bytes(), file_name=cover.name, mime="image/png",
                                    key=f"dl_c_{name}")
        report = exports / f"{name}_report.json"
        if report.is_file():
            with cols[3].expander("导出报告"):
                st.json(json.loads(report.read_text(encoding="utf-8")))
