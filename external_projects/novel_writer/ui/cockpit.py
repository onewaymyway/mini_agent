"""🧭 驾驶舱：单本小说的入口，包含 总控台 / 规划 / 设定库 / 线索 四个分页（M2）。"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from novel_writer import tool_api
from novel_writer.settings import GEARS, QUALITIES
from ui import codex as codex_page
from ui import reading as reading_page
from ui import review as review_page
from ui import writing as writing_page
from ui import planning as planning_page
from ui import threads as threads_page
from ui.components import GATE_LABEL, REASON_LABEL, STATUS_LABEL, novel_title, require_ws, show_error

GATE_CHOICES = ["（挡位预设）", "manual", "review", "auto", "skip"]


def page_cockpit() -> None:
    ws = require_ws()
    if not ws:
        return
    st.header(f"🧭 {novel_title(ws)}")
    st.caption(ws)
    names = ["🧭 总控台", "🗺 规划", "✍ 写作台", "🔍 审校", "📖 阅读", "👥 设定库", "🧵 线索"]
    tabs = st.tabs(names)
    with tabs[0]:
        render_control(ws)
    with tabs[1]:
        planning_page.render_planning(ws)
    with tabs[2]:
        writing_page.render_writing(ws)
    with tabs[3]:
        review_page.render_review(ws)
    with tabs[4]:
        reading_page.render_reading(ws)
    with tabs[5]:
        codex_page.render_codex(ws)
    with tabs[6]:
        threads_page.render_threads(ws)


def render_control(ws: str) -> None:
    res = tool_api.pipeline_status(ws)
    if not res["ok"]:
        show_error(res)
        return
    data = res["data"]
    counts = data["counts"]
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("前沿步骤", data["frontier"] or "—")
    c2.metric("已通过", counts.get("passed", 0) + counts.get("skipped", 0))
    c3.metric("待你处理", counts.get("waiting_review", 0) + counts.get("waiting_user", 0) + counts.get("stale", 0))
    c4.metric("失败 / 阻塞", counts.get("failed", 0) + counts.get("blocked", 0))
    _render_settings(ws, data)
    _render_worker(ws, data)
    _render_run_buttons(ws)

    st.subheader("流水线")
    rows = []
    for s in data["steps"]:
        rows.append({
            "步骤": s["id"],
            "标题": s["title"],
            "阶段": s["stage"],
            "状态": STATUS_LABEL.get(s["status"], s["status"]),
            "闸门": f"{GATE_LABEL.get(s['gate'], s['gate'])}（{s['gate_source']}）",
            "尝试": s.get("attempts") or 0,
            "说明": s.get("error") or s.get("summary") or s.get("note") or "",
        })
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True, height=min(520, 40 + 35 * len(rows)))
    _render_step_actions(ws, data["steps"])


def _render_settings(ws: str, data: dict) -> None:
    settings = tool_api.get_novel(ws)
    with st.expander("挡位与预算", expanded=False):
        cur = settings["data"]["settings"] if settings["ok"] else {}
        c1, c2, c3 = st.columns(3)
        gear = c1.selectbox("控制挡位", list(GEARS), index=list(GEARS).index(cur.get("gear", "G0")),
                            format_func=lambda g: f"{g} {GEARS[g]}", key="cp_gear")
        quality = c2.selectbox("质量档位", list(QUALITIES), index=list(QUALITIES).index(cur.get("quality", "standard")),
                               format_func=lambda q: QUALITIES[q], key="cp_quality")
        budget = cur.get("budget") or {}
        max_ch = c3.number_input("每次运行最多章数", min_value=1, max_value=100,
                                 value=int(budget.get("max_chapters_per_run") or 3), key="cp_budget_ch")
        st.caption(GEAR_HINT.get(gear, ""))
        if st.button("保存挡位与预算", key="cp_save_settings"):
            r = tool_api.update_settings(ws, {"gear": gear, "quality": quality,
                                              "budget": {"max_chapters_per_run": int(max_ch)}})
            if r["ok"]:
                st.success("已保存，从下一步起生效")
                st.rerun()
            else:
                show_error(r)
        st.caption(f"当前：{cur.get('gear')} · 质量档 {QUALITIES.get(cur.get('quality'), '')}")


GEAR_HINT = {
    "G0": "手动挡：每一步都要你点 ▶，只有强制检查点会停下来等你。",
    "G1": "引导挡：设计类步骤运行后等你批准，其余写前/审校自动。",
    "G2": "半自动挡：建档与章节写作自动，设计与前三章仍需批准。",
    "G3": "全自动挡：除设计审批与强制检查点外全部自动，遇到问题即停。",
}


def _render_worker(ws: str, data: dict) -> None:
    w = data["worker"]
    if w.get("alive"):
        st.info(f"🔵 后台 worker 正在运行（步骤：{w.get('step') or '—'}）")
    elif w.get("reason"):
        st.caption(f"上次运行结束：{REASON_LABEL.get(w['reason'], w['reason'])}")


def _render_run_buttons(ws: str) -> None:
    c1, c2, c3, c4, c5 = st.columns(5)
    if c1.button("▶ 运行下一步", key="cp_run_single", use_container_width=True):
        _report(tool_api.run_novel(ws, mode="single"))
    if c2.button("▶▶ 运行到检查点", key="cp_run_until", use_container_width=True):
        _report(tool_api.run_novel(ws, mode="until_gate"))
    if c3.button("⏸ 暂停", key="cp_pause", use_container_width=True):
        _report(tool_api.pause_novel(ws))
    if c4.button("⏹ 停止", key="cp_stop", use_container_width=True):
        _report(tool_api.stop_novel(ws))
    if c5.button("🔄 刷新", key="cp_refresh", use_container_width=True):
        st.rerun()


def _report(res: dict) -> None:
    if res["ok"]:
        st.toast("已提交。后台运行中，稍后点「刷新」查看进度。")
    else:
        show_error(res)


def _render_step_actions(ws: str, steps: list) -> None:
    st.subheader("单步操作")
    ids = [s["id"] for s in steps]
    if not ids:
        st.info("流水线为空。请先完成工作目录初始化。")
        return
    pick = st.selectbox("选择步骤", ids, key="cp_step_pick")
    step = next(s for s in steps if s["id"] == pick)
    st.markdown(f"**{step['title']}**　{STATUS_LABEL.get(step['status'], step['status'])}　"
                f"闸门：{GATE_LABEL.get(step['gate'], step['gate'])}（{step['gate_source']}）")
    if step.get("error"):
        st.error(step["error"])
    if step.get("summary"):
        st.caption(f"回执摘要：{step['summary']}")

    b1, b2, b3 = st.columns(3)
    if step["status"] == "waiting_review" and b1.button("✅ 批准", key=f"cp_approve_{pick}"):
        _report_ok(tool_api.approve_step(ws, pick))
    if b2.button("▶ 运行此步", key=f"cp_run_step_{pick}"):
        _report(tool_api.run_novel(ws, mode="single", step_id=pick))
    if b3.button("↺ 重跑（清除状态）", key=f"cp_rerun_{pick}"):
        _report_ok(tool_api.rerun_step(ws, pick))

    if step["status"] == "waiting_user":
        _render_answer_form(ws, step)

    with st.expander("闸门覆盖 / 作者指导", expanded=False):
        current = tool_api.get_novel(ws)["data"]["settings"].get("step_overrides", {}).get(pick)
        idx = GATE_CHOICES.index(current) if current in GATE_CHOICES else 0
        gate = st.selectbox("本步闸门", GATE_CHOICES, index=idx, key=f"cp_gate_{pick}")
        if st.button("保存闸门", key=f"cp_gate_save_{pick}"):
            value = None if gate == GATE_CHOICES[0] else gate
            r = tool_api.set_step_gate(ws, pick, value)
            _report_ok(r)
        note = st.text_area("给这一步的作者指导（下次执行时注入 prompt）", key=f"cp_note_{pick}")
        if st.button("保存指导", key=f"cp_note_save_{pick}"):
            _report_ok(tool_api.write_author_note(ws, pick, note))


def _render_answer_form(ws: str, step: dict) -> None:
    questions = step.get("needs_user") or []
    st.warning("这一步需要你决定：")
    with st.form(f"answer_{step['id']}"):
        answers = {}
        for q in questions:
            qid = str(q.get("id") or "q")
            opts = q.get("options") or []
            text = str(q.get("question") or "")
            if opts:
                answers[qid] = st.radio(text, opts, key=f"ans_{step['id']}_{qid}")
            else:
                answers[qid] = st.text_input(text, key=f"ans_{step['id']}_{qid}")
        if st.form_submit_button("提交答复并重跑此步"):
            _report_ok(tool_api.answer_step(ws, step["id"], answers))


def _report_ok(res: dict) -> None:
    if res["ok"]:
        st.success("已完成。点「刷新」查看最新状态。")
    else:
        show_error(res)
