"""ui/export_page.py — 「🎬 合成导出」（P5）：合成设置、分章合成（后台）、前 30 秒预览、成片导出与下载。"""

from __future__ import annotations

import json

import streamlit as st

from article_to_video.compose.config import load_compose_config, save_compose_config
from article_to_video.compose.export import export_final, safe_filename
from article_to_video.compose.ffrun import ComposeError
from article_to_video.compose.part import compose_part, preview_part, video_dir
from article_to_video.paths import projects_root
from article_to_video.planning.runner import load_spine
from article_to_video.project_store import list_projects, load_project
from article_to_video.visual.storyboard import storyboard_status
from ui import jobs

_SUB_LABELS = {"auto": "自动（卡片已呈现则隐藏）", "always": "始终显示", "off": "关闭"}
_TR_LABELS = {"cut": "直接切换", "fade": "黑场淡入淡出", "dissolve": "叠化"}


def _settings(pdir) -> None:
    cfg = load_compose_config(pdir)
    with st.expander("合成设置（转场、字幕、进度条）", expanded=False):
        with st.form("compose_settings"):
            c1, c2 = st.columns(2)
            tr = c1.selectbox("章间转场", list(_TR_LABELS), index=list(_TR_LABELS).index(cfg["transition"]),
                              format_func=lambda k: _TR_LABELS[k])
            tsec = c2.number_input("转场时长（秒）", min_value=0.2, max_value=2.0,
                                   value=float(cfg["transition_sec"]), step=0.1)
            subs = st.selectbox("字幕", list(_SUB_LABELS), index=list(_SUB_LABELS).index(cfg["subtitles"]),
                                format_func=lambda k: _SUB_LABELS[k])
            c3, c4 = st.columns(2)
            bar = c3.checkbox("显示进度条", value=cfg["progress_bar"])
            label = c4.checkbox("显示章标签", value=cfg["chapter_label"])
            if st.form_submit_button("保存合成设置"):
                res = save_compose_config(pdir, {"transition": tr, "transition_sec": tsec, "subtitles": subs,
                                                 "progress_bar": bar, "chapter_label": label})
                for w in res["warnings"]:
                    st.warning(w)
                st.success("已保存。字幕与转场只影响合成，不需要重新渲染画面。")
                st.rerun()


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
    spine = load_spine(pdir)
    if spine is None or not spine.get("confirmed"):
        st.info("请先在「🗺️ 大纲」生成并确认大纲（C0）。")
        return
    _settings(pdir)

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
            if jobs.start(pdir, f"合成 {cid}", run):
                st.rerun()
        if has_video and st.button("预览前 30 秒", key=f"prev_{cid}", disabled=busy):
            try:
                path = preview_part(pdir, cid, 30)
                st.video(str(path))
            except ComposeError as exc:
                st.error(str(exc))

    c1, c2 = st.columns(2)
    if c1.button("合成全部已就绪的章", disabled=busy or not ready_ids):
        def run_all():
            for cid in ready_ids:
                compose_part(pdir, cid)
        if jobs.start(pdir, f"合成 {len(ready_ids)} 章", run_all):
            st.rerun()

    all_ids = [p["id"] for p in spine["parts"]]
    done = all((video_dir(pdir, cid) / "part.mp4").is_file() for cid in all_ids)
    if c2.button("🎞️ 导出成片（后台）", disabled=busy or not done,
                 help="需要所有章都已合成" if not done else None):
        if jobs.start(pdir, "导出成片", lambda: export_final(pdir)):
            st.rerun()

    _downloads(pdir)


def _downloads(pdir) -> None:
    exports = pdir / "exports"
    if not exports.is_dir():
        return
    project = load_project(pdir)
    name = safe_filename(project.get("title") or "video")
    video = exports / f"{name}.mp4"
    if not video.is_file():
        return
    st.subheader("成片")
    st.video(str(video))
    cols = st.columns(3)
    cols[0].download_button("下载视频（mp4）", video.read_bytes(), file_name=video.name, mime="video/mp4")
    srt = exports / f"{name}.srt"
    if srt.is_file():
        cols[1].download_button("下载字幕（srt）", srt.read_bytes(), file_name=srt.name)
    cover = exports / f"{name}_cover.png"
    if cover.is_file():
        cols[2].download_button("下载封面（png）", cover.read_bytes(), file_name=cover.name, mime="image/png")
    report = exports / f"{name}_report.json"
    if report.is_file():
        with st.expander("成片校验报告"):
            st.json(json.loads(report.read_text(encoding="utf-8")))
