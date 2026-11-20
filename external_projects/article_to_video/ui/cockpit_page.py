"""ui/cockpit_page.py — 「🧭 驾驶舱」（R7）：项目的主入口。

驾驶舱分两部分：
  * 流程总览：每个步骤（大纲、口播稿、配音、分镜、合成导出）的状态与下一步；
  * 步骤区：「🧭 总控」（挡位与预算、一键流水线、上次运行、各章状态、整章重置）与各个步骤的编辑界面。
    步骤之间用一排单选按钮切换，每次只渲染当前步骤，因此同一项目内不会出现重复控件。
各步骤的业务代码仍在 ui/outline_page.py 等模块中（_xxx_body），这里只负责编排与导航。
"""

from __future__ import annotations

import streamlit as st

from article_to_video.paths import projects_root
from article_to_video.pipeline.gates import GEARS, PipelineError, STAGES
from article_to_video import joblog
from article_to_video.pipeline.gates import export_needs_approval
from article_to_video.pipeline.regenerate import outcome_of, regenerate_all
from article_to_video.pipeline.run import default_providers, read_status, run_pipeline
from article_to_video.planning.runner import load_spine
from article_to_video.project_store import list_projects
from article_to_video.script.writer import load_script
from article_to_video.visual.storyboard import storyboard_status
from ui import jobs
from ui.reset_panel import render_reset_panel
from ui.joblog_panel import render_job_panel, render_run_monitor, run_state
from ui.outline_page import _outline_body
from ui.script_page import _script_body
from ui.voice_page import _voice_body
from ui.storyboard_page import _storyboard_body
from ui.export_page import _export_body

_GEAR_HELP = {
    "G0": "默认：大纲、口播、分镜、终审都需要人工确认。",
    "G1": "只有大纲与终审需要人工；口播与分镜通过校验后自动确认。",
    "G2": "全自动，必须设置预算上限（生图次数或总耗时）；超限即停止。",
    "G3": "完全无人值守：无人工检查点，终审自动通过并导出；不设预算上限（可能产生较多费用）。",
}

CONTROL = "🧭 总控"
# 导航名 → (步骤标题, 业务函数, 运行日志 scope, 日志面板标题)
STEPS = {
    "🗺️ 大纲": ("🗺️ 大纲（C0）", _outline_body, ["outline"], "大纲运行日志"),
    "📝 口播稿": ("📝 口播稿（C1）", _script_body, ["script"], "写稿运行日志"),
    "🔊 配音": ("🔊 配音（S5）", _voice_body, ["voice"], "配音运行日志"),
    "🎞️ 分镜": ("🎞️ 分镜（C2）", _storyboard_body, ["storyboard", "render"], "分镜与渲染运行日志"),
    "🎬 合成导出": ("🎬 合成导出（P5）", _export_body, ["export"], "合成导出运行日志"),
}


def _part_rows(pdir):
    spine = load_spine(pdir)
    rows = []
    for p in spine["parts"] if spine else []:
        pid = p["id"]
        sc = load_script(pdir, pid)
        st_ = storyboard_status(pdir, pid)
        video = (pdir / "parts" / pid / "video" / "part.mp4").is_file()
        rows.append({"章": f"{pid} {p['title'][:20]}",
                     "口播": "已确认" if sc and sc.get("confirmed") else ("草稿" if sc else "未写"),
                     "分镜": "已确认" if st_["confirmed"] else ("过期" if st_["stale"] else
                                                               ("草稿" if st_["exists"] else "未生成")),
                     "视频": "已合成" if video else "未合成"})
    return rows


def overview(pdir) -> list:
    """每个步骤的状态与说明（只读，不调用模型）。返回 [{步骤, 状态, 说明, 完成}]。"""
    spine = load_spine(pdir)
    parts = spine["parts"] if spine else []
    ids = [p["id"] for p in parts]
    n = len(ids)
    rows = []

    if spine is None:
        rows.append({"步骤": "🗺️ 大纲", "状态": "未生成", "说明": "在「大纲」步骤生成", "完成": False})
    elif spine.get("confirmed"):
        rows.append({"步骤": "🗺️ 大纲", "状态": "已确认", "说明": f"{n} 章", "完成": True})
    else:
        rows.append({"步骤": "🗺️ 大纲", "状态": "待确认", "说明": "检查章节与时长后确认", "完成": False})

    scripts = {pid: load_script(pdir, pid) for pid in ids}
    n_conf = sum(1 for sc in scripts.values() if sc and sc.get("confirmed"))
    n_draft = sum(1 for sc in scripts.values() if sc)
    if not n:
        rows.append({"步骤": "📝 口播稿", "状态": "等待大纲", "说明": "", "完成": False})
    elif n_conf == n:
        rows.append({"步骤": "📝 口播稿", "状态": "已确认", "说明": f"{n} 章", "完成": True})
    else:
        rows.append({"步骤": "📝 口播稿", "状态": "未完成" if n_draft == 0 else f"{n_conf}/{n} 章已确认",
                     "说明": "逐章写稿并确认", "完成": False})

    n_voice = sum(1 for pid in ids if (pdir / "parts" / pid / "timeline.json").is_file())
    rows.append({"步骤": "🔊 配音", "状态": "已完成" if n and n_voice == n else
                 (f"{n_voice}/{n} 章" if n_voice else "未配音"),
                 "说明": "逐句配音与时间轴", "完成": bool(n and n_voice == n)})

    sb = {pid: storyboard_status(pdir, pid) for pid in ids}
    n_sb = sum(1 for s_ in sb.values() if s_["confirmed"])
    n_stale = sum(1 for s_ in sb.values() if s_["stale"])
    if n and n_sb == n:
        sb_state = "已确认"
    elif n_stale:
        sb_state = f"{n_stale} 章过期"
    else:
        sb_state = f"{n_sb}/{n} 章已确认" if n_sb else "未生成"
    rows.append({"步骤": "🎞️ 分镜", "状态": sb_state, "说明": "画面设计与插图", "完成": bool(n and n_sb == n)})

    n_video = sum(1 for pid in ids if (pdir / "parts" / pid / "video" / "part.mp4").is_file())
    exported = any((pdir / "exports").glob("*.mp4")) if (pdir / "exports").is_dir() else False
    rows.append({"步骤": "🎬 合成导出",
                 "状态": "已导出" if exported else (f"{n_video}/{n} 章已合成" if n_video else "未合成"),
                 "说明": "章合成与成片导出", "完成": exported})
    return rows


def _next_hint(rows) -> str:
    for r in rows:
        if not r["完成"]:
            return f"下一步：{r['步骤']}（{r['状态']}）"
    return "全部步骤已完成。"


def _deck_panel(pdir, busy: bool) -> None:
    """新方案：逐章生成幻灯片规划 → 确认 → 生成讲稿 → 确认。配音与按页渲染为后续阶段。"""
    from article_to_video.deck.script import plan_script, script_status
    from article_to_video.deck.voice import synthesize_deck, voice_status
    from article_to_video.deck.compose import compose_deck_part, deck_part_ready, export_deck_video
    from article_to_video.deck.store import confirm_deck, deck_status, load_deck, plan_deck
    from article_to_video.planning.errors import PlanError

    st.subheader("新方案：幻灯片讲解")
    st.caption("先把文章规划为幻灯片（确定性规则），确认后为每页生成讲稿（需要文本模型）。"
               "配音与按页渲染将在后续阶段接入；在此之前，新方案下流水线按钮不可用。")
    spine = load_spine(pdir)
    if spine is None or not spine.get("confirmed"):
        st.info("请先在「🗺️ 大纲」生成并确认大纲（C0）。")
        return
    rows = []
    for p in spine["parts"]:
        d = deck_status(pdir, p["id"])
        sc = script_status(pdir, p["id"])
        rows.append({
            "章": f"{p['id']} {p['title'][:20]}",
            "幻灯片规划": ("⬜ 未生成" if not d["exists"] else
                          ("⚠️ 已过期" if d["stale"] else ("✅ 已确认" if d["confirmed"] else "🟡 待确认"))),
            "页数": d["slides"],
            "讲稿": ("⬜ 未生成" if not sc["exists"] else
                     ("⚠️ 已过期" if sc["stale"] else ("✅ 已确认" if sc["confirmed"] else "🟡 待确认"))),
            "讲稿字数": sc.get("total_chars", 0),
            "视频": ("✅ 已合成" if deck_part_ready(pdir, p["id"]) else "⬜ 未合成"),
            "配音": ("⬜ 未生成" if not voice_status(pdir, p["id"])["exists"] else
                    ("⚠️ 已过期" if voice_status(pdir, p["id"])["stale"] else
                     f"✅ {voice_status(pdir, p['id'])['total_sec'] / 60:.1f} 分钟")),
        })
    st.table(rows)

    c1, c2 = st.columns(2)
    if c1.button("生成全部章的幻灯片规划（后台）", disabled=busy):
        def run_plan():
            errs = []
            for p in spine["parts"]:
                try:
                    plan_deck(pdir, p["id"])
                except PlanError as exc:
                    errs.append(f"{p['id']}：{exc}")
            if errs:
                return {"state": "error", "message": "；".join(errs)}
            return {"state": "done", "message": "幻灯片规划已生成"}

        if jobs.start(pdir, "幻灯片规划", run_plan, scope="deck"):
            st.rerun()
    if c2.button("确认全部章的幻灯片规划", disabled=busy):
        for p in spine["parts"]:
            if load_deck(pdir, p["id"]) and not deck_status(pdir, p["id"])["stale"]:
                try:
                    confirm_deck(pdir, p["id"])
                except PlanError as exc:
                    st.error(str(exc))
        st.rerun()

    d1, d2 = st.columns(2)
    if d1.button("生成全部章的讲稿（后台，需要文本模型）", disabled=busy):
        def run_script():
            from article_to_video.pipeline.run import default_providers
            llm = default_providers(pdir, offline=False)["llm"]
            errs = []
            for p in spine["parts"]:
                try:
                    plan_script(pdir, p["id"], llm=llm, part_title=p["title"])
                except PlanError as exc:
                    errs.append(f"{p['id']}：{exc}")
            if errs:
                return {"state": "error", "message": "；".join(errs)}
            return {"state": "done", "message": "讲稿已生成"}

        if jobs.start(pdir, "幻灯片讲稿", run_script, scope="deck"):
            st.rerun()
    v1 = st.columns(1)[0]
    if v1.button("生成全部章的配音（后台，需要语音引擎）", disabled=busy):
        def run_voice():
            from article_to_video.pipeline.run import default_providers
            tts = default_providers(pdir, offline=False)["tts"]
            errs = []
            for p in spine["parts"]:
                try:
                    synthesize_deck(pdir, p["id"], tts)
                except PlanError as exc:
                    errs.append(f"{p['id']}：{exc}")
            if errs:
                return {"state": "error", "message": "；".join(errs)}
            return {"state": "done", "message": "配音与页面时间线已生成"}

        if jobs.start(pdir, "幻灯片配音", run_voice, scope="deck"):
            st.rerun()
    v2 = st.columns(2)
    if v2[0].button("合成全部章的新方案视频（后台）", disabled=busy):
        def run_compose():
            errs = []
            for p in spine["parts"]:
                try:
                    compose_deck_part(pdir, p["id"])
                except (PlanError, Exception) as exc:  # noqa: BLE001 — 界面展示错误文字
                    errs.append(f"{p['id']}：{exc}")
            if errs:
                return {"state": "error", "message": "；".join(errs)}
            return {"state": "done", "message": "新方案视频已合成"}

        if jobs.start(pdir, "新方案合成", run_compose, scope="deck"):
            st.rerun()
    if v2[1].button("导出新方案成片（后台）", disabled=busy):
        def run_export():
            out = export_deck_video(pdir)
            return {"state": "done", "message": f"已导出 {out.name}"}

        if jobs.start(pdir, "新方案导出", run_export, scope="deck"):
            st.rerun()
    if d2.button("确认全部章的讲稿", disabled=busy):
        from article_to_video.deck.script import confirm_script
        for p in spine["parts"]:
            if load_script_doc(pdir, p["id"]):
                try:
                    confirm_script(pdir, p["id"])
                except PlanError as exc:
                    st.error(str(exc))
        st.rerun()


def load_script_doc(pdir, part_id):
    from article_to_video.deck.script import load_script
    return load_script(pdir, part_id)


def _control_body(pdir) -> None:
    busy = run_state(pdir, None)["busy"]

    from article_to_video import app_settings as S  # 默认值来自「⚙️ 设置」（R4）
    pl = S.effective_group(pdir, "pipeline")
    mode_labels = {"classic": "现有方案：口播稿 → 分镜 → 画面", "deck": "新方案：幻灯片 → 讲解（第一阶段：仅规划）"}
    mode = st.radio("生成方案（每个项目单独记忆）", list(mode_labels), index=list(mode_labels).index(pl["mode"]),
                    format_func=lambda k: mode_labels[k], key="gen_mode")
    with st.expander("挡位与预算", expanded=True):
        st.caption("默认值来自「⚙️ 设置」的「流水线」分组；这里的挡位与预算会记为本项目的设置，下次打开自动使用。")
        gear = st.radio("挡位", list(GEARS), horizontal=True, index=list(GEARS).index(pl["gear"]))
        st.caption(_GEAR_HELP[gear])
        c1, c2, c3 = st.columns(3)
        until = c1.selectbox("运行到", list(STAGES), index=len(STAGES) - 1)
        max_images = c2.number_input("预算：最多生图次数（0 = 不限）", min_value=0, value=int(pl["max_images"]), step=1)
        max_minutes = c3.number_input("预算：最长耗时（分钟，0 = 不限）", min_value=0.0, value=float(pl["max_minutes"]),
                                      step=5.0)
        if S.save_budget(pdir, gear=gear, max_images=int(max_images), max_minutes=float(max_minutes), mode=mode):
            joblog.log(pdir, "settings", f"挡位与预算已保存为本项目设置：{gear}，生图 {int(max_images) or '不限'}，"
                                         f"耗时 {float(max_minutes) or '不限'} 分钟")
        c4, c5 = st.columns(2)
        approve = c4.checkbox("确认终审（允许导出）", value=False)
        merge_all = c5.checkbox("多期时导出全集合并版", value=False)
        offline = st.checkbox("离线冒烟（语音静音、生图占位，大纲改用离线规则，不产生费用）", value=False)

    if mode == "deck":
        _deck_panel(pdir, busy)
    elif st.button("▶ 运行流水线（后台）", disabled=busy):
        mi = int(max_images) or None
        mm = float(max_minutes) or None
        try:
            if gear == "G2" and mi is None and mm is None:
                raise PipelineError("G2 必须设置预算上限（生图次数或耗时）")
        except PipelineError as exc:
            st.error(str(exc))
        else:
            def run():
                prov = default_providers(pdir, offline=offline)
                st_ = run_pipeline(pdir, until=until, gear=gear, max_images=mi, max_minutes=mm, approve=approve,
                                   llm=prov["llm"], tts=prov["tts"], image_provider=prov["image"],
                                   merge_all=merge_all, outline_engine="rules" if offline else pl["outline_engine"])
                return outcome_of(st_)
            if jobs.start(pdir, f"流水线 {gear}", run, scope="pipeline"):
                st.rerun()

    with st.expander("🔁 清空所有产出并全部重新生成", expanded=False):
        st.caption("从大纲起清空全部产出（大纲、口播、配音、分镜、画面、合成、导出），然后按上方的挡位与预算"
                   "重新运行整条流水线。生图缓存默认保留。G0 / G1 会在大纲等检查点停下，等待你确认。")
        imgs_all = st.checkbox("同时清空生图缓存（会重新生图，产生费用）", value=False, key="rst_all_imgs")
        ok_all = st.checkbox("我确认清空全部产出并重新生成", value=False, key="rst_all_ok")
        blocked = until == "export" and export_needs_approval(gear, approve)
        if blocked:
            st.warning("当前挡位在导出前需要终审。请先勾选上方「确认终审（允许导出）」，否则流水线会停在导出前，"
                       "不会生成视频。")
        if st.button("清空所有并重新生成（后台）", disabled=busy or not ok_all or blocked or mode == "deck", key="rst_all_go",
                     type="primary"):
            mi_all = int(max_images) or None
            mm_all = float(max_minutes) or None
            try:
                if gear == "G2" and mi_all is None and mm_all is None:
                    raise PipelineError("G2 必须设置预算上限（生图次数或耗时）")
            except PipelineError as exc:
                st.error(str(exc))
            else:
                def run_all():
                    prov = default_providers(pdir, offline=offline)
                    out = regenerate_all(pdir, include_images=imgs_all, confirm=True, until=until, gear=gear,
                                         max_images=mi_all, max_minutes=mm_all, approve=approve, llm=prov["llm"],
                                         tts=prov["tts"], image_provider=prov["image"], merge_all=merge_all,
                                         outline_engine="rules" if offline else pl["outline_engine"])
                    joblog.log(pdir, "pipeline", f"清空所有并重新生成：删除 {len(out['removed'])} 项"
                               + ("（含生图缓存）" if imgs_all else ""), level="warn")
                    return outcome_of(out["status"])
                if jobs.start(pdir, f"清空所有并重新生成（{gear}）", run_all, scope="pipeline"):
                    st.rerun()

    status = read_status(pdir)
    if status:
        st.subheader("上次运行")
        state_label = {"done": "✅ 完成", "waiting": "⏸ 等待人工确认", "budget_exceeded": "🛑 超出预算",
                       "stopped": "⏹ 已停止", "failed": "❌ 失败", "running": "⏳ 运行中"}.get(status["state"],
                                                                                          status["state"])
        if status["state"] == "running" and not busy:
            state_label = "⚠️ 中断（进程已结束，没有完成记录）"
        st.write(f"状态：**{state_label}**　挡位 {status['gear']}　运行到 {status['until']}")
        if status.get("waiting"):
            st.warning(f"等待：{status['waiting']['detail']}")
        if status.get("error"):
            st.error(status["error"])
        for step in status.get("steps", []):
            st.write(f"- {step['stage']}：{step['result']} {step.get('detail', '')}")
        b = status.get("budget") or {}
        if b:
            st.caption(f"预算：生图 {b.get('image_calls')} / {b.get('max_images') or '不限'}；"
                       f"耗时 {b.get('elapsed_sec')} 秒 / {b.get('max_minutes') or '不限'} 分钟")
        if status.get("episodes"):
            st.write("分期：" + "；".join(f"第 {e['index']}/{e['total']} 期：{'、'.join(e['parts'])}"
                                         for e in status["episodes"]))
        for out in status.get("outputs", []):
            st.write(f"📦 {out}")

    st.subheader("各章状态")
    st.table(_part_rows(pdir))

    render_reset_panel(pdir, stage=None, key="rst_cockpit", busy=busy)


def render_cockpit_page() -> None:
    st.header("🧭 驾驶舱")
    root = projects_root()
    projects = [p for p in list_projects(root) if "error" not in p]
    if not projects:
        st.info("还没有项目。先到「📥 新建项目」导入文章。")
        return
    pid = st.selectbox("项目", [p["id"] for p in projects],
                       format_func=lambda i: next(p["title"] for p in projects if p["id"] == i))
    pdir = root / pid

    rows = overview(pdir)
    st.subheader("流程总览")
    st.table([{"步骤": r["步骤"], "状态": r["状态"], "说明": r["说明"]} for r in rows])
    st.caption(_next_hint(rows))

    step = st.radio("步骤", [CONTROL, *STEPS], horizontal=True, key="cockpit_step", label_visibility="collapsed")
    if step == CONTROL:
        try:
            _control_body(pdir)
        finally:
            render_run_monitor(pdir, ["pipeline"], title="流水线运行监控")
        return
    heading, body_fn, scopes, panel_title = STEPS[step]
    st.header(heading)
    try:
        body_fn(pdir)
    finally:
        render_job_panel(pdir, scopes, title=panel_title)
