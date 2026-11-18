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
from article_to_video.compose.branding import resolve_bgm, save_bgm
from article_to_video.project_store import load_project as _lp, update_branding
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


_POS_LABELS = {"top_right": "右上", "top_left": "左上", "bottom_right": "右下", "bottom_left": "左下",
               "center": "居中", "diagonal_tile": "斜向平铺（防裁剪）"}
_MODE_LABELS = {"static": "固定", "drift": "缓慢漂移"}


def _branding_form(pdir) -> None:
    project = _lp(pdir)
    from article_to_video.project_store import normalize_branding
    b, _w = normalize_branding(project.get("branding"))
    bgm_cfg, bgm_warn = resolve_bgm(pdir, project)
    with st.expander("署名与水印（最后一步，不重渲染画面）", expanded=False):
        with st.form("branding_form"):
            author = st.text_input("作者名（留空则不署名）", value=b["author"])
            url = st.text_input("来源链接（片尾可选）", value=b["source_url"])
            c1, c2 = st.columns(2)
            cr = b["credits"]
            t_card = c1.checkbox("片头标题卡", value=cr["title_card"])
            lower = c2.checkbox("下三分之一条", value=cr["lower_third"])
            end = c1.checkbox("片尾卡", value=cr["end_card"])
            corner = c2.checkbox("角标（作者）", value=cr["corner_tag"])
            wm = b["watermark"]
            st.markdown("**水印**")
            c3, c4 = st.columns(2)
            wm_on = c3.checkbox("启用水印", value=wm["enabled"])
            wm_text = c4.text_input("水印文字", value=wm["text"])
            c5, c6, c7 = st.columns(3)
            pos = c5.selectbox("位置", list(_POS_LABELS), index=list(_POS_LABELS).index(wm["position"]),
                               format_func=lambda k: _POS_LABELS[k])
            mode = c6.selectbox("模式", list(_MODE_LABELS), index=list(_MODE_LABELS).index(wm["mode"]),
                                format_func=lambda k: _MODE_LABELS[k])
            opacity = c7.slider("透明度", 0.05, 1.0, float(wm["opacity"]), 0.05)
            ai_on = st.checkbox("AI 生成声明（片尾与元数据）", value=b["ai_disclosure"]["enabled"])
            ai_text = st.text_input("AI 声明文字", value=b["ai_disclosure"]["text"])
            st.markdown("**背景音乐（可选，默认关闭；需要你提供本地音频）**")
            c8, c9, c10 = st.columns([1, 2, 1])
            bgm_on = c8.checkbox("启用 BGM", value=bool(bgm_cfg["enabled"]))
            bgm_path = c9.text_input("音频路径（项目内，如 assets/bgm.mp3）",
                                     value=str(project.get("bgm", {}).get("path") or ""))
            bgm_vol = c10.number_input("音量", min_value=0.0, max_value=1.0, value=float(bgm_cfg["volume"]),
                                       step=0.05)
            if st.form_submit_button("保存署名与水印"):
                update_branding(pdir, {
                    "author": author, "source_url": url,
                    "credits": {"title_card": t_card, "lower_third": lower, "end_card": end, "corner_tag": corner},
                    "watermark": {**wm, "enabled": wm_on, "text": wm_text, "position": pos, "mode": mode,
                                  "opacity": opacity},
                    "ai_disclosure": {"enabled": ai_on, "text": ai_text},
                })
                res = save_bgm(pdir, enabled=bgm_on, rel_path=bgm_path, volume=bgm_vol)
                for w in list(res["warnings"]) + _w:
                    st.warning(w)
                st.success("已保存。下次导出时只重做最后的水印步骤（画面与章视频不重算）。")
                st.rerun()
        if bgm_warn:
            st.caption(bgm_warn[0])


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
    _branding_form(pdir)

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
