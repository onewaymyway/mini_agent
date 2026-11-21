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
from article_to_video.visual.structures import coverage_rows
from ui import jobs
from ui.reset_panel import render_reset_panel

_TYPE_LABELS = {"keypoint": "卡片", "list": "列表", "timeline": "时间线", "diagram": "流程/回路",
                "table": "表格", "quote": "金句", "formula": "公式", "illustration": "插图",
                "stack": "堆叠层图", "stages": "递进卡", "definition": "定义卡", "contrast": "对比卡",
                "recap": "回顾卡"}


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
            elif kind == "contrast":
                st.caption("对比卡的两侧由原文决定，本页只读：左 " + "；".join(sc.get("left") or [])
                           + " ｜ 右 " + "；".join(sc.get("right") or []))
                items = []
            else:
                items_text = st.text_area("条目（每行一条，每条不超过 60 字）",
                                          value="\n".join(sc.get("items") or []), height=110,
                                          key=f"items_{key}")
                items = [x for x in items_text.splitlines() if x.strip()]
            emph = st.text_input("强调词（逗号分隔）", value="、".join(sc.get("emphasis") or []),
                                 key=f"emph_{key}")
            new = {"id": sc["id"], "beat": sc["beat"], "type": kind, "kind": sc.get("kind"),
                   "sentences": sc.get("sentences") or [], "refs": sc.get("refs", []),
                   "items": items,
                   "emphasis": [x.strip() for x in emph.replace(",", "、").split("、") if x.strip()],
                   "header": sc.get("header", []), "rows": sc.get("rows", []),
                   "loop": sc.get("loop", False), "formula": sc.get("formula", ""), "image": None,
                   "reveal": []}
            if kind == "stages":
                new["stages"] = sc.get("stages") or []
                st.caption("递进卡的阶段与标签取自原文，本页只读；条目随阶段标题自动生成。")
                new["items"] = [s.get("title", "") for s in new["stages"]]
            if kind == "contrast":
                new.update(left=sc.get("left") or [], right=sc.get("right") or [],
                           left_title=sc.get("left_title", ""), right_title=sc.get("right_title", ""))
            if kind == "definition":
                new["term"] = st.text_input("术语（取自原文概念标题，可修改）", value=sc.get("term", ""), key=f"term_{key}")
            if kind == "formula":
                new["formula"] = st.text_input("公式（纯文本）", value=sc.get("formula", ""), key=f"f_{key}")
            if kind == "illustration":
                img = sc.get("image") or {}
                new["image"] = {
                    "subject": st.text_input("视觉主体（具体的物体或场景，不要写抽象词或文字）",
                                             value=img.get("subject", ""), key=f"subj_{key}"),
                    "scene": st.text_input("场景（所处环境，可空）", value=img.get("scene", ""), key=f"scene_{key}"),
                    "composition": st.text_input("构图（画面安排，可空）", value=img.get("composition", ""),
                                                 key=f"comp_{key}"),
                    "focus_x": st.slider("水平取景位置", 0.0, 1.0, float(img.get("focus_x", 0.5)),
                                         key=f"focus_{key}"),
                    "version": img.get("version", 0), "file": img.get("file"),
                }
            st.caption(f"本段 {n_sent} 句；保存时自动按句子重算出现时机。")
        raw_scenes.append(new)
    return {"scenes": raw_scenes}


def _coverage_panel(sb) -> None:
    """C2：分镜生成时的结构覆盖报告（哪些原文结构由哪些镜头承载；未承载或只承载部分的说明）。"""
    cov = (sb or {}).get("coverage")
    if not cov:
        return
    with st.expander("📊 结构覆盖报告（C2）", expanded=False):
        rows = coverage_rows(cov)
        if rows:
            st.table(rows)
        else:
            st.caption("本章没有需要承载的原文结构。")
        for u in cov.get("uncovered") or []:
            st.caption(f"未完全承载：{u}")
        for n in cov.get("notes") or []:
            st.caption(f"说明：{n}")


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
    if status["exists"]:
        cols[1].metric("需生成图片", f"{status['images']} / {status['allowance']}",
                       help="分镜计划生成的图片数 / 本章图片额度")
        cols[2].metric("引擎", "模型版" if status.get("engine") == "llm" else "规则版")
    else:  # 尚无分镜时没有“计划图片数”，不能把额度当作已计划的数量
        cols[1].metric("需生成图片", "—", help=f"生成分镜后显示；本章图片额度 {status['allowance']}")
        cols[2].metric("引擎", "—", help="生成分镜后显示所用引擎")
    if status["stale"]:
        st.warning("口播稿或视觉设置已变更，分镜已过期。重新生成会覆盖你在这里做的编辑。")

    engine_label = st.radio("分镜引擎", ["规则版（离线，确定）", "模型版（需要 mini_agent 模型）"],
                            index=1, horizontal=True, key=f"eng_{part_id}")  # 默认：模型版
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

    _batch_controls(pdir, ready, engine, busy)
    _render_all_controls(pdir, ready, busy)
    def _regen_storyboard(p, ids):
        from article_to_video import joblog
        from article_to_video.providers.text import make_text_llm
        llm = make_text_llm("miniagent", pdir=p) if engine == "llm" else None
        for pid in ids:
            try:
                sbm.plan_storyboard(p, pid, force=True, engine=engine, llm=llm)
            except PlanError as exc:   # 单章失败不影响其它章
                joblog.bound_log(f"{pid}：分镜重新生成失败：{exc}", level="error")

    render_reset_panel(pdir, stage="storyboard", key="rst_storyboard", busy=busy,
                       regenerate=_regen_storyboard, confirmed_only=[p["id"] for p in ready])

    if not status["exists"]:
        return
    sb = sbm.load_storyboard(pdir, part_id)
    script = load_script(pdir, part_id)
    for w in status["warnings"][:10]:
        st.warning(w)
    for e in status["errors"][:10]:
        st.error(e)
    _coverage_panel(sb)

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
            from article_to_video.providers.image import project_image_provider
            from article_to_video.render.compose_part import render_part
            provider = project_image_provider(pdir, scope="渲染 ")

            def run():
                render_part(pdir, part_id, provider=provider)

            if jobs.start(pdir, f"渲染 {part_id}", run, scope="render"):
                st.rerun()

    _preview(pdir, part_id)


def _render_all_controls(pdir, ready, busy: bool) -> None:
    """R20：一次性渲染全部可渲染章节的画面（后台任务，逐章独立，图片复用，不重复生图）。"""
    from article_to_video.render.compose_part import render_ready_reason
    ids = [p["id"] for p in ready]
    ready_now = [i for i in ids if render_ready_reason(pdir, i) is None]
    st.markdown("**渲染全部章节画面**")
    st.caption(f"可渲染 {len(ready_now)} 章（分镜已确认、未过期、有时间轴）；其余章节会跳过并写明原因。"
               "已生成的图片直接复用，不会重新生图。")
    if st.button(f"🖼️ 渲染所有章的画面（{len(ready_now)} 章）", disabled=busy or not ready_now,
                 key="batch_render_all"):
        from article_to_video.providers.image import project_image_provider
        from article_to_video.render.compose_part import render_all_parts
        provider = project_image_provider(pdir, scope="渲染全部 ")
        if jobs.start(pdir, f"渲染所有章的画面（{len(ids)} 章）",
                      lambda: render_all_parts(pdir, ids, provider=provider), scope="render"):
            st.rerun()
        st.error("已有后台任务在运行，请稍候")


def _batch_controls(pdir, ready, engine: str, busy: bool) -> None:
    """R15：一次性逐章生成全部分镜（后台任务，章与章之间独立，失败不影响其它章）。"""
    st.divider()
    st.markdown("**全部章节**")
    force = st.checkbox("覆盖已有分镜（会丢失这些章节里的手工编辑）", value=False, key="batch_force",
                        help="不勾选时：只生成缺失或已过期的章节，已是最新的章节保留你的编辑。")
    label = (f"⚠️ 覆盖并重新生成全部章节（{len(ready)} 章）" if force
             else f"⚡ 生成全部章节分镜（{len(ready)} 章，逐章进行）")
    st.caption("只处理已确认口播稿的章节；按章节顺序逐章生成，单章失败不影响其它章，"
               "过程见页面底部的运行日志，可随时请求停止。")
    if st.button(label, disabled=busy, key="batch_go"):
        from article_to_video.providers.text import make_text_llm

        def run():
            llm = make_text_llm("miniagent", pdir=pdir) if engine == "llm" else None
            sbm.plan_all_storyboards(pdir, force=force, engine=engine, llm=llm)

        title = f"批量{'模型' if engine == 'llm' else '规则'}生成分镜（{len(ready)} 章{'，覆盖' if force else ''}）"
        if jobs.start(pdir, title, run, scope="storyboard"):
            st.rerun()
        st.error("已有后台任务在运行，请稍候")


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
        n = len(scene["frames"])
        if n == 1:  # 只有一个状态时无需滑块（Streamlit 不接受 min == max）
            idx = 0
        else:
            idx = st.slider("状态", 0, n - 1, n - 1, key=f"st_{part_id}_{sid}")
        frame = scene["frames"][idx]
        st.image(str(base / frame["file"]), caption=f"出现时刻 {frame['start']} 秒")
    else:
        st.image(str(base / scene["background"]), caption="插图底图")
