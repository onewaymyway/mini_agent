"""ui/cockpit_page.py — 「🧭 驾驶舱」（P7）：挡位与预算、一键流水线（后台可停止，R1 起带实时日志与步骤时间表）、各章状态、整章重置。"""

from __future__ import annotations

import streamlit as st

from article_to_video import joblog
from article_to_video.paths import projects_root
from article_to_video.pipeline.gates import PipelineError, STAGES
from article_to_video.pipeline.reset import ORDER, reset_part
from article_to_video.pipeline.run import default_providers, read_status, run_pipeline
from article_to_video.planning.runner import load_spine
from article_to_video.project_store import list_projects
from article_to_video.script.writer import load_script
from article_to_video.visual.storyboard import storyboard_status
from ui import jobs
from ui.joblog_panel import render_run_monitor, run_state

_GEAR_HELP = {
    "G0": "默认：大纲、口播、分镜、终审都需要人工确认。",
    "G1": "只有大纲与终审需要人工；口播与分镜通过校验后自动确认。",
    "G2": "全自动，必须设置预算上限（生图次数或总耗时）；超限即停止。",
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

    busy = run_state(pdir, None)["busy"]

    with st.expander("挡位与预算", expanded=True):
        gear = st.radio("挡位", ["G0", "G1", "G2"], horizontal=True, index=0)
        st.caption(_GEAR_HELP[gear])
        c1, c2, c3 = st.columns(3)
        until = c1.selectbox("运行到", list(STAGES), index=len(STAGES) - 1)
        max_images = c2.number_input("预算：最多生图次数（0 = 不限）", min_value=0, value=0, step=1)
        max_minutes = c3.number_input("预算：最长耗时（分钟，0 = 不限）", min_value=0.0, value=0.0, step=5.0)
        c4, c5 = st.columns(2)
        approve = c4.checkbox("确认终审（允许导出）", value=False)
        merge_all = c5.checkbox("多期时导出全集合并版", value=False)
        offline = st.checkbox("离线冒烟（语音静音、生图占位，大纲改用离线规则，不产生费用）", value=False)

    if st.button("▶ 运行流水线（后台）", disabled=busy):
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
                run_pipeline(pdir, until=until, gear=gear, max_images=mi, max_minutes=mm, approve=approve,
                             llm=prov["llm"], tts=prov["tts"], image_provider=prov["image"],
                             merge_all=merge_all, outline_engine="rules" if offline else "llm")
            if jobs.start(pdir, f"流水线 {gear}", run, scope="pipeline"):
                st.rerun()

    render_run_monitor(pdir, ["pipeline"], title="流水线运行监控")

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

    with st.expander("整章重置（会删除产物，需要确认）"):
        spine = load_spine(pdir)
        part_ids = [p["id"] for p in spine["parts"]] if spine else []
        if part_ids:
            rp = st.selectbox("章", part_ids, key="reset_part")
            frm = st.selectbox("从此阶段起删除", list(ORDER), key="reset_from")
            ok = st.checkbox("我确认删除该章从此阶段起的全部产物（图片缓存保留）", key="reset_ok")
            if st.button("重置本章", disabled=not ok or busy):
                try:
                    removed = reset_part(pdir, rp, from_stage=frm, confirm=True)
                    joblog.log(pdir, "pipeline", f"整章重置：{rp} 从「{frm}」起删除 {len(removed)} 项", level="warn")
                    st.success(f"已删除 {len(removed)} 项")
                    st.rerun()
                except PipelineError as exc:
                    st.error(str(exc))
