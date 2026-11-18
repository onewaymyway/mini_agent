"""ui/voice_page.py — 「🔊 配音」（S5）：音色与引擎设置、后台配音（可停止）、时间轴、试听。"""

from __future__ import annotations

import streamlit as st

from article_to_video.audio import synth as sy
from article_to_video.audio.config import ENGINES, NARRATION_VOICES, QUOTE_VOICES, normalize_tts
from article_to_video.paths import projects_root
from article_to_video.planning.errors import PlanError
from article_to_video.planning.runner import load_spine
from article_to_video.project_store import list_projects
from article_to_video.providers.tts import SilenceTTS, build_chain
from article_to_video.script.writer import load_script
from ui import jobs


def _tts_for(pdir, stub: bool):
    if stub:
        return SilenceTTS()
    cfg = sy.project_tts_config(pdir)
    return build_chain(cfg["engine_chain"], edge_proxy=cfg["edge_proxy"])


def render_voice_page() -> None:
    st.header("🔊 配音（S5）")
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
        st.info("请先确认大纲（C0）并确认口播稿（C1）。")
        return
    confirmed_parts = [p["id"] for p in spine["parts"]
                       if (load_script(pdir, p["id"]) or {}).get("confirmed")]
    if not confirmed_parts:
        st.info("还没有已确认的口播稿（C1）。")
        return

    cfg = sy.project_tts_config(pdir)
    with st.expander("配音设置"):
        with st.form("tts_settings"):
            c1, c2 = st.columns(2)
            narr = c1.selectbox("主讲音色", NARRATION_VOICES,
                                index=NARRATION_VOICES.index(cfg["narration_voice"]))
            quote = c2.selectbox("引文音色", QUOTE_VOICES, index=QUOTE_VOICES.index(cfg["quote_voice"]))
            chain_text = st.text_input("引擎顺序（逗号分隔，前一个失败用下一个）",
                                       value=",".join(cfg["engine_chain"]),
                                       help=f"可选：{', '.join(ENGINES)}")
            proxy = st.text_input("edge-tts 代理", value=cfg["edge_proxy"] or "",
                                  help="留空=系统代理；填 direct=强制直连；或填代理地址")
            max_q = st.slider("每章引文最多几句", 0, 10, cfg["max_quotes_per_part"])
            if st.form_submit_button("保存设置"):
                chain = [x.strip() for x in chain_text.split(",") if x.strip()]
                _, warns = sy.update_tts_settings(pdir, {
                    "narration_voice": narr, "quote_voice": quote, "engine_chain": chain,
                    "edge_proxy": proxy or None, "max_quotes_per_part": max_q})
                for w_ in warns:
                    st.warning(w_)
                st.success("已保存")
                st.rerun()

    st.subheader("后台配音")
    job = jobs.status(pdir)
    running = bool(job and job["state"] == "running")
    if job:
        st.write(f"任务：{job['label']} — **{job['state']}**：{job['message']}")
        if running and st.button("⏹ 请求停止"):
            jobs.stop(pdir)
            st.rerun()
    stub = st.checkbox("仅流程验证：生成静音（不能作为正式成片）", value=False)
    force = st.checkbox("重新配音（忽略缓存）", value=False)
    part = st.selectbox("章", confirmed_parts)
    c1, c2 = st.columns(2)
    if c1.button("配音本章", disabled=running, type="primary"):
        jobs.start(pdir, f"配音 {part}",
                   lambda: sy.synthesize_part(pdir, part, _tts_for(pdir, stub), force=force))
        st.rerun()
    if c2.button("配音全部已确认的章", disabled=running):
        jobs.start(pdir, "配音全部",
                   lambda: [sy.synthesize_part(pdir, p, _tts_for(pdir, stub), force=force)
                            for p in confirmed_parts])
        st.rerun()

    tl = sy.load_timeline(pdir, part)
    if tl is None:
        st.info("本章还没有时间轴。")
    else:
        st.caption(f"总长 {tl['total_sec']} 秒　·　引擎：{sorted({x['engine'] for x in tl['sentences']})}")
        for msg in tl.get("warnings", []):
            st.warning(msg)
        st.dataframe([{
            "句": x["id"], "文字": x["text"][:28], "音色": x["voice"], "语速": x["rate"],
            "引擎": x["engine"], "开始": x["start"], "时长": x["duration"], "结束": x["end"],
        } for x in tl["sentences"]], use_container_width=True, hide_index=True)
        ids = [x["id"] for x in tl["sentences"]]
        pick = st.selectbox("试听某一句", ids, format_func=lambda i: next(
            f"{x['id']} {x['text'][:20]}" for x in tl["sentences"] if x["id"] == i))
        path = pdir / "parts" / part / "audio" / f"{pick}.wav"
        if path.is_file():
            st.audio(str(path))

    st.subheader("试听任意文字")
    text = st.text_input("文字", placeholder="输入一句话试听，不会改变项目")
    if st.button("生成试听") and text.strip():
        try:
            out = sy.preview(pdir, text, cfg["narration_voice"], "+0%", _tts_for(pdir, stub))
            st.audio(str(out))
        except (PlanError, RuntimeError) as exc:
            st.error(str(exc))
