"""驾驶舱·新方案（幻灯片讲解）：只包含新方案自己的标签页与操作，与现有方案的界面完全分开。

标签：🧭 总控（挡位、一键运行、运行日志）· 🗺️ 大纲（共用）· 📑 幻灯片规划 · ✍️ 讲稿 · 🎙️ 配音 · 📤 合成导出。
每个操作都在后台运行，并把每章的失败原因记录下来，表格中直接显示。
"""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from article_to_video import app_settings as S
from article_to_video.compose.export import list_all_exports
from article_to_video.deck import status as DS
from article_to_video.deck.compose import compose_deck_part, export_deck_video
from article_to_video.deck.pipeline import load_run_status, regenerate_deck, run_deck_pipeline
from article_to_video.deck.script import confirm_script, plan_script
from article_to_video.deck.store import confirm_deck, deck_status, plan_deck
from article_to_video.deck.voice import synthesize_deck
from article_to_video.pipeline.gates import check_budget_requirement
from article_to_video.planning.errors import PlanError
from article_to_video.planning.runner import load_spine
from ui import jobs
from article_to_video.deck.script import AUTO_ATTEMPTS, script_status
from ui.joblog_panel import is_busy, render_job_panel

CONTROL = "🧭 总控"
PAGES = {
    "📑 幻灯片规划": "幻灯片规划",
    "✍️ 讲稿": "讲稿",
    "🎙️ 配音": "配音",
    "📤 合成导出": "合成导出",
}
GEAR_HELP = {
    "G0": "G0：规划、讲稿、终审都需要人工确认。",
    "G1": "G1：规划与讲稿自动确认，只有终审需要人工。",
    "G2": "G2：全自动，需要设置预算上限。",
    "G3": "G3：完全无人值守，终审自动通过并导出，不设预算上限。",
}


def _parts(pdir: Path):
    spine = load_spine(pdir)
    if spine is None or not spine.get("confirmed"):
        return None
    return spine["parts"]


def overview_rows(pdir: Path) -> list:
    """新方案的流程总览（只读，不调用模型）。"""
    parts = _parts(pdir)
    if not parts:
        return [{"步骤": "🗺️ 大纲", "状态": "未确认", "说明": "先在「大纲」确认（所有方案共用）"}]
    ids = [p["id"] for p in parts]
    rows = [{"步骤": "🗺️ 大纲", "状态": "已确认", "说明": f"{len(ids)} 章（所有方案共用）"}]
    for stage, name in (("plan", "📑 幻灯片规划"), ("script", "✍️ 讲稿"), ("voice", "🎙️ 配音"),
                        ("compose", "📤 合成导出")):
        rows.append({"步骤": name, "状态": DS.stage_summary(pdir, stage, ids), "说明": ""})
    return rows


def _status_table(pdir: Path, parts, stage: str, extra=None):
    rows = []
    for p in parts:
        st_ = DS.part_state(pdir, stage, p["id"])
        row = {"章": f"{p['id']} {p['title'][:18]}", "状态": st_["label"]}
        if st_["detail"]:
            row["说明"] = st_["detail"]
        if extra:
            row.update(extra(p["id"]))
        rows.append(row)
    st.table(rows)


def _run_per_part(pdir: Path, stage: str, parts, action, label: str):
    """逐章执行 action；成功清除该章的失败记录，失败记录原因。返回 (state, message)。"""
    failed = []
    for p in parts:
        try:
            action(p["id"])
            DS.record_error(pdir, stage, p["id"], None)
        except PlanError as exc:
            DS.record_error(pdir, stage, p["id"], str(exc))
            failed.append(f"{p['id']}：{exc}")
    if failed:
        return {"state": "error", "message": f"{label}失败：" + "；".join(failed)}
    return {"state": "done", "message": f"{label}完成（{len(parts)} 章）"}


def _providers(pdir: Path, offline: bool):
    from article_to_video.pipeline.run import default_providers
    return default_providers(pdir, offline=offline)


# ---------- 总控 ----------

def render_control(pdir: Path) -> None:
    parts = _parts(pdir)
    st.subheader("新方案总控")
    if parts is None:
        st.info("请先在「🗺️ 大纲」确认大纲（所有方案共用）。")
        return
    pl = S.effective_group(pdir, "pipeline")
    c1, c2, c3 = st.columns(3)
    gear = c1.radio("挡位", ["G0", "G1", "G2", "G3"], index=["G0", "G1", "G2", "G3"].index(pl["gear"])
                    if pl["gear"] in ("G0", "G1", "G2", "G3") else 0, horizontal=True, key="deck_gear")
    until = c2.selectbox("运行到", ["plan", "script", "voice", "compose", "export"],
                         index=4, format_func=lambda k: {"plan": "规划", "script": "讲稿", "voice": "配音",
                                                          "compose": "合成", "export": "导出"}[k], key="deck_until")
    approve = c3.checkbox("确认终审（允许导出）", value=False, key="deck_approve")
    c4, c5, c6 = st.columns(3)
    max_images = c4.number_input("生图次数上限（0 = 不限）", min_value=0, value=int(pl["max_images"]), step=1,
                                 key="deck_max_images")
    max_minutes = c5.number_input("耗时上限（分钟，0 = 不限）", min_value=0.0, value=float(pl["max_minutes"]),
                                  step=5.0, key="deck_max_minutes")
    offline = c6.checkbox("离线冒烟（不调用模型与语音引擎）", value=False, key="deck_offline")
    st.caption(GEAR_HELP[gear])
    S.save_budget(pdir, gear=gear, max_images=int(max_images), max_minutes=float(max_minutes))

    busy = is_busy(pdir)
    if st.button("▶ 一键运行新方案（后台）", disabled=busy, type="primary", key="deck_run_all"):
        mi = int(max_images) or None
        mm = float(max_minutes) or None
        try:
            check_budget_requirement(gear, mi, mm)
        except Exception as exc:  # noqa: BLE001
            st.error(str(exc))
        else:
            def run_all():
                prov = _providers(pdir, offline)
                out = run_deck_pipeline(pdir, gear=gear, until=until, approve=approve, max_images=mi,
                                        max_minutes=mm, llm=prov["llm"], tts=prov["tts"], image=prov["image"])
                if out["state"] == "done":
                    return {"state": "done", "message": "新方案已完成"}
                if out["state"] == "waiting":
                    w = out.get("waiting") or {}
                    return {"state": "waiting", "message": f"停在「{w.get('stage', '')}」：{w.get('detail', '')}"}
                return {"state": "error", "message": out.get("error") or out["state"]}

            if jobs.start(pdir, f"新方案一键运行（{gear}）", run_all, scope="deck"):
                st.rerun()
    with st.expander("🗑️ 清空当前新方案数据并全部重新生成", expanded=False):
        st.caption("清空新方案的全部产出（规划、讲稿、配音、视频、成片、封面、运行记录），然后按上方的挡位与设置重新运行整条流程。"
                   "大纲与现有方案的数据不受影响。")
        keep_audio = st.checkbox("保留配音缓存（不重新配音，节省时间和费用）", value=False, key="deck_keep_audio")
        confirm_all = st.checkbox("我确认清空新方案的全部数据并重新生成", value=False, key="deck_reset_ok")
        if st.button("清空并全部重新生成（后台）", disabled=busy or not confirm_all, key="deck_reset_go",
                     type="primary"):
            mi = int(max_images) or None
            mm = float(max_minutes) or None

            def reset_and_run():
                prov = _providers(pdir, offline)
                out = regenerate_deck(pdir, keep_audio=keep_audio, confirm=True, gear=gear, until=until,
                                      approve=approve, max_images=mi, max_minutes=mm,
                                      llm=prov["llm"], tts=prov["tts"], image=prov["image"])
                st_ = out["status"]
                if st_["state"] == "done":
                    return {"state": "done", "message": f"已清空 {len(out['removed'])} 项并重新生成完成"}
                if st_["state"] == "waiting":
                    w = st_.get("waiting") or {}
                    return {"state": "waiting", "message": f"停在「{w.get('stage', '')}」：{w.get('detail', '')}"}
                return {"state": "error", "message": st_.get("error") or st_["state"]}

            if jobs.start(pdir, f"新方案清空并重新生成（{gear}）", reset_and_run, scope="deck"):
                st.rerun()
    last = load_run_status(pdir)
    if last:
        st.caption(f"上次一键运行：{last.get('state')}（{last.get('updated_at', '')[:19]}）"
                   + (f" · 等待：{last['waiting']['detail']}" if last.get("waiting") else "")
                   + (f" · 错误：{last['error']}" if last.get("error") else ""))
    render_job_panel(pdir, ["deck"], title="新方案运行日志（实时）", height=380)


# ---------- 各阶段页面 ----------

def render_plan(pdir: Path) -> None:
    parts = _parts(pdir)
    st.subheader("幻灯片规划")
    st.caption("按文章结构用规则规划每页幻灯片；规则规划不合格时，自动把具体问题交给文本模型修复并持续重试。规划结果需要确认后，才能生成讲稿。")
    if parts is None:
        st.info("请先确认大纲。")
        return
    _status_table(pdir, parts, "plan", lambda pid: {"页数": (deck_status(pdir, pid)["slides"])})
    c1, c2 = st.columns(2)
    if c1.button("生成全部章的规划（后台）", key="deck_plan_go"):
        def job():
            prov = _providers(pdir, offline=False)           # 规则不合格时由文本模型修复（没有模型则不修复）
            return _run_per_part(pdir, "plan", parts, lambda pid: plan_deck(pdir, pid, llm=prov["llm"]),
                                 "幻灯片规划")
        if jobs.start(pdir, "新方案：幻灯片规划", job, scope="deck"):
            st.rerun()
    if c2.button("确认全部章的规划", key="deck_plan_confirm"):
        for p in parts:
            try:
                confirm_deck(pdir, p["id"])
                DS.record_error(pdir, "plan", p["id"], None)
            except PlanError as exc:
                DS.record_error(pdir, "plan", p["id"], str(exc))
        st.rerun()
    render_job_panel(pdir, ["deck"], title="幻灯片规划日志", height=200)


def render_script(pdir: Path) -> None:
    parts = _parts(pdir)
    st.subheader("讲稿")
    st.caption("为每页生成讲稿（需要文本模型）。讲稿需要确认后才能配音。")
    if parts is None:
        st.info("请先确认大纲。")
        return
    _status_table(pdir, parts, "script", lambda pid: {"讲稿字数": script_status(pdir, pid)["total_chars"]})
    c1, c2 = st.columns(2)
    if c1.button("生成全部章的讲稿（后台）", key="deck_script_go"):
        def job():
            prov = _providers(pdir, offline=False)
            if prov["llm"] is None:
                msg = "未配置文本模型，无法生成讲稿（请到「⚙️ 设置」配置）"
                for p in parts:
                    DS.record_error(pdir, "script", p["id"], msg)
                return {"state": "error", "message": msg}
            return _run_per_part(pdir, "script", parts,
                                 lambda pid: plan_script(pdir, pid, llm=prov["llm"], max_attempts=AUTO_ATTEMPTS,
                                                         part_title=next(x["title"] for x in parts
                                                                         if x["id"] == pid)),
                                 "讲稿生成")
        if jobs.start(pdir, "新方案：讲稿", job, scope="deck"):
            st.rerun()
    if c2.button("确认全部章的讲稿", key="deck_script_confirm"):
        for p in parts:
            try:
                confirm_script(pdir, p["id"])
                DS.record_error(pdir, "script", p["id"], None)
            except PlanError as exc:
                DS.record_error(pdir, "script", p["id"], str(exc))
        st.rerun()
    render_job_panel(pdir, ["deck"], title="讲稿日志", height=200)


def render_voice(pdir: Path) -> None:
    parts = _parts(pdir)
    st.subheader("配音")
    st.caption("每页讲稿逐句配音，页面停留时长由真实配音时长决定。")
    if parts is None:
        st.info("请先确认大纲。")
        return
    _status_table(pdir, parts, "voice")
    if st.button("生成全部章的配音（后台）", key="deck_voice_go"):
        def job():
            prov = _providers(pdir, offline=False)
            if prov["tts"] is None:
                msg = "未配置语音引擎，无法配音（请到「⚙️ 设置」配置）"
                for p in parts:
                    DS.record_error(pdir, "voice", p["id"], msg)
                return {"state": "error", "message": msg}
            return _run_per_part(pdir, "voice", parts, lambda pid: synthesize_deck(pdir, pid, prov["tts"]),
                                 "配音")
        if jobs.start(pdir, "新方案：配音", job, scope="deck"):
            st.rerun()
    render_job_panel(pdir, ["deck"], title="配音日志", height=200)


def render_export(pdir: Path) -> None:
    parts = _parts(pdir)
    st.subheader("合成导出")
    st.caption("每章合成为视频，再拼接为新方案成片。所有方案的成片也可在「📤 合成导出」（现有方案）页看到。")
    if parts is None:
        st.info("请先确认大纲。")
        return
    _status_table(pdir, parts, "compose")
    c1, c2 = st.columns(2)
    if c1.button("合成全部章（后台）", key="deck_compose_go"):
        def job():
            prov = _providers(pdir, offline=False)           # 背景插图需要生图服务与文本模型（主体描述）
            return _run_per_part(pdir, "compose", parts,
                                 lambda pid: compose_deck_part(pdir, pid, image=prov["image"], llm=prov["llm"]),
                                 "章合成")
        if jobs.start(pdir, "新方案：章合成", job, scope="deck"):
            st.rerun()
    if c2.button("导出新方案成片（后台）", key="deck_export_go"):
        def job_export():
            try:
                out = export_deck_video(pdir)
            except PlanError as exc:
                return {"state": "error", "message": str(exc)}
            return {"state": "done", "message": f"已导出 {out.name}"}
        if jobs.start(pdir, "新方案：导出", job_export, scope="deck"):
            st.rerun()
    videos = [r for r in list_all_exports(pdir) if r["scheme"] == "新方案"]
    if videos:
        st.subheader("新方案成片")
        for r in videos:
            st.markdown(f"**{r['name']}**")
            cover = r["path"].with_name(r["path"].stem + "_cover.png")
            if cover.is_file():
                st.image(str(cover), caption="封面")
            st.video(str(r["path"]))
            st.download_button("下载视频", r["path"].read_bytes(), file_name=r["name"], mime="video/mp4",
                               key=f"deck_dl_{r['name']}")
    render_job_panel(pdir, ["deck"], title="合成导出日志", height=200)
