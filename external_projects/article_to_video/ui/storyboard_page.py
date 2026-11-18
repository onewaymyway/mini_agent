"""ui/storyboard_page.py — 「🎞️ 分镜」（C2）：视觉设置、生成分镜、逐段编辑、确认、后台渲染与预览。"""

from __future__ import annotations

import json

import streamlit as st
from ui.joblog_panel import render_job_panel, render_page_status

from article_to_video.paths import projects_root
from article_to_video.planning.errors import PlanError
from article_to_video.planning.runner import load_spine
from article_to_video.project_store import list_projects
from article_to_video.script.writer import load_script
from article_to_video.visual import storyboard as sbm
from article_to_video.visual.config import load_visual_config
from article_to_video.visual.states import SCENE_TYPES
from ui import jobs

_TYPE_LABELS = {"keypoint": "卡片", "list": "列表", "timeline": "时间线", "diagram": "流程/回路",
                "table": "表格", "quote": "金句", "formula": "公式", "illustration": "插图"}


def _visual_summary(pdir) -> None:
    cfg = load_visual_config(pdir)
    img = cfg["images"]
    st.caption(f"生效的视觉设置：生图 {'开' if img['enabled'] else '关'}（{img['provider']}）　·　"
               f"每分钟 {img['max_per_minute']} 张　·　每图 {img['shots_per_image']} 个镜头　·　"
               f"风格锚「{cfg['bible']['style_anchor'][:24]}…」（在「⚙️ 设置」中修改）")


def _scene_editor(pdir, part_id, sb, script) -> dict:
    """渲染每个场景的编辑控件，返回用于保存的 raw 结构。"""
    by_beat = {b["id"]: b for b in script["beats"]}
    raw_scenes = []
    for sc in sb["scenes"]:
        beat = by_beat.get(sc["beat"])
        n_sent = len(beat["sentences"]) if beat else 0
        key = f"{part_id}_{sc['id']}"
        label = f"{sc['id']} · {_TYPE_LABELS.get(sc['type'], sc['type'])}"
        with st.expander(label, expanded=False):
            st.caption("段落原文（口播）：" + " ".join(s["text"] for s in beat["sentences"])[:160]
                       if beat else "（段落缺失）")
            kind = st.selectbox("画面类型", SCENE_TYPES, index=SCENE_TYPES.index(sc["type"]),
                                format_func=lambda t: _TYPE_LABELS.get(t, t), key=f"type_{key}")
            if kind == "table":
                st.caption("表格的每一行由原文表格决定，本页只读；逐行出现时机随保存自动重算。")
                items = ["、".join(r) for r in sc.get("rows") or []]
            else:
                items_text = st.text_area("条目（每行一条，每条不超过 60 字）",
                                          value="\n".join(sc.get("items") or []), height=110,
                                          key=f"items_{key}")
                items = [x for x in items_text.splitlines() if x.strip()]
            emph = st.text_input("强调词（逗号分隔）", value="、".join(sc.get("emphasis") or []),
                                 key=f"emph_{key}")
            new = {"id": sc["id"], "beat": sc["beat"], "type": kind, "refs": sc.get("refs", []),
                   "items": items,
                   "emphasis": [x.strip() for x in emph.replace(",", "、").split("、") if x.strip()],
                   "header": sc.get("header", []), "rows": sc.get("rows", []),
                   "loop": sc.get("loop", False), "formula": sc.get("formula", ""), "image": None,
                   "reveal": []}
            if kind == "formula":
                new["formula"] = st.text_input("公式（纯文本）", value=sc.get("formula", ""), key=f"f_{key}")
            if kind == "illustration":
                img = sc.get("image") or {}
                new["image"] = {
                    "subject": st.text_input("插图主题（画面意象，不要写文字）",
                                             value=img.get("subject", ""), key=f"subj_{key}"),
                    "focus_x": st.slider("水平取景位置", 0.0, 1.0, float(img.get("focus_x", 0.5)),
                                         key=f"focus_{key}"),
                    "version": img.get("version", 0), "file": img.get("file"),
                }
            st.caption(f"本段 {n_sent} 句；保存时自动按句子重算出现时机。")
        raw_scenes.append(new)
    return {"scenes": raw_scenes}


def render_storyboard_page() -> None:
    st.header("🎞️ 分镜（C2）")
    root = projects_root()
    projects = [p for p in list_projects(root) if "error" not in p]
    if not projects:
        st.info("还没有项目。先到「📥 新建项目」导入文章。")
        return
    pid = st.selectbox("项目", [p["id"] for p in projects],
                       format_func=lambda i: next(p["title"] for p in projects if p["id"] == i))
    pdir = root / pid
    try:
        _storyboard_body(pdir)
    finally:
        render_job_panel(pdir, ["storyboard", "render"], title="分镜与渲染运行日志")


def _storyboard_body(pdir) -> None:
    render_page_status(pdir, ["storyboard", "render"])
    spine = load_spine(pdir)
    if spine is None or not spine.get("confirmed"):
        st.info("请先在「🗺️ 大纲」生成并确认大纲（C0）。")
        return
    _visual_summary(pdir)

    ready = [p for p in spine["parts"] if (load_script(pdir, p["id"]) or {}).get("confirmed")]
    if not ready:
        st.info("还没有已确认的口播稿（C1）。请先到「📝 口播稿」确认。")
        return
    part_id = st.selectbox("章", [p["id"] for p in ready],
                           format_func=lambda i: next(f"{p['id']} {p['title']}" for p in ready if p["id"] == i))

    job = jobs.status(pdir)
    if job:
        st.write(f"任务：{job['label']} — **{job['state']}**：{job['message']}")
        if job["state"] == "running":
            if st.button("⏹ 请求停止"):
                jobs.stop(pdir)
                st.rerun()
            if st.button("🔄 刷新状态"):
                st.rerun()

    status = sbm.storyboard_status(pdir, part_id)
    st.subheader(f"{part_id} 分镜")
    cols = st.columns(4)
    cols[0].metric("状态", "已确认" if status["confirmed"] else ("已过期" if status["stale"] else
                                                               ("未确认" if status["exists"] else "未生成")))
    cols[1].metric("需生成图片", f"{status['images']} / {status['allowance']}")
    cols[2].metric("引擎", "模型版" if status.get("engine") == "llm" else "规则版")
    if status["stale"]:
        st.warning("口播稿或视觉设置已变更，分镜已过期。重新生成会覆盖你在这里做的编辑。")

    engine_label = st.radio("分镜引擎", ["规则版（离线，确定）", "模型版（需要 mini_agent 模型）"],
                            horizontal=True, key=f"eng_{part_id}")
    engine = "llm" if engine_label.startswith("模型") else "rules"
    busy = job is not None and job["state"] == "running"

    def _generate(force: bool) -> None:
        def run():
            if engine == "rules":
                sbm.plan_storyboard(pdir, part_id, force=force)
            else:
                from article_to_video.providers.text import make_text_llm
                sbm.plan_storyboard(pdir, part_id, force=force, engine="llm", llm=make_text_llm("miniagent", pdir=pdir))

        label = f"{'模型' if engine == 'llm' else '规则'}生成分镜 {part_id}"
        if jobs.start(pdir, label, run, scope="storyboard"):
            st.rerun()
        st.error("已有后台任务在运行，请稍候")

    c1, c2, c3 = st.columns(3)
    if c1.button("生成分镜", disabled=(status["exists"] and not status["stale"]) or busy):
        _generate(False)
    if status["exists"] and c2.button("重新生成（覆盖编辑）", disabled=busy):
        _generate(True)
    if status["exists"] and c3.button("🔁 重新分配一图多用", disabled=busy,
                                      help="只重新分配插图的图片组与取景，不改动文字与画面类型"):
        if jobs.start(pdir, f"重新分配一图多用 {part_id}",
                      lambda: sbm.reallocate_images(pdir, part_id), scope="storyboard"):
            st.rerun()
        st.error("已有后台任务在运行，请稍候")

    if not status["exists"]:
        return
    sb = sbm.load_storyboard(pdir, part_id)
    script = load_script(pdir, part_id)
    for w in status["warnings"][:10]:
        st.warning(w)
    for e in status["errors"][:10]:
        st.error(e)

    with st.form(f"sb_form_{part_id}"):
        raw = _scene_editor(pdir, part_id, sb, script)
        save = st.form_submit_button("保存分镜编辑（未确认）")
    if save:
        try:
            sbm.save_storyboard(pdir, part_id, raw)
            st.success("已保存。请检查后确认。")
            st.rerun()
        except PlanError as exc:
            st.error(str(exc))

    c3, c4 = st.columns(2)
    if c3.button("✅ 确认分镜（C2）", disabled=bool(status["errors"]) or status["stale"]):
        try:
            sbm.confirm_storyboard(pdir, part_id)
            st.rerun()
        except PlanError as exc:
            st.error(str(exc))
    if status["confirmed"]:
        if c4.button("🖼️ 渲染本章画面（后台）", disabled=job is not None and job["state"] == "running"):
            from article_to_video.providers.image import make_image_provider as mk
            from article_to_video.render.compose_part import render_part
            cfg = load_visual_config(pdir)
            provider = mk(cfg["images"]["provider"]) if cfg["images"]["enabled"] else None

            def run():
                render_part(pdir, part_id, provider=provider)

            if jobs.start(pdir, f"渲染 {part_id}", run, scope="render"):
                st.rerun()

    _preview(pdir, part_id)




def _preview(pdir, part_id) -> None:
    manifest_path = pdir / "parts" / part_id / "visuals" / "manifest.json"
    if not manifest_path.is_file():
        return
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    st.subheader("画面预览")
    st.caption(f"渲染统计：{manifest.get('counts')}；画幅 {manifest.get('canvas')}")
    for w in manifest.get("warnings", [])[:5]:
        st.warning(w)
    scenes = manifest.get("scenes", [])
    if not scenes:
        return
    sid = st.selectbox("场景", [s["id"] for s in scenes], key=f"prev_{part_id}")
    scene = next(s for s in scenes if s["id"] == sid)
    base = pdir / "parts" / part_id
    if scene.get("fallback_reason"):
        st.info(f"插图未生成，已按卡片绘制：{scene['fallback_reason']}")
    if scene["frames"]:
        idx = st.slider("状态", 0, len(scene["frames"]) - 1, len(scene["frames"]) - 1, key=f"st_{part_id}")
        frame = scene["frames"][idx]
        st.image(str(base / frame["file"]), caption=f"出现时刻 {frame['start']} 秒")
    else:
        st.image(str(base / scene["background"]), caption="插图底图")
