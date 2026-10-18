"""🧭 驾驶舱：单本小说的入口，包含 总控台 / 规划 / 设定库 / 线索 四个分页（M2）。"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from novel_writer import tool_api
from novel_writer.settings import GEARS, QUALITIES
from ui import codex as codex_page
from ui import live as live_page
from ui import ideas as ideas_page
from ui import metrics as metrics_page
from ui import settings_page
from ui import reading as reading_page
from ui import review as review_page
from ui import writing as writing_page
from ui import planning as planning_page
from ui import threads as threads_page
from ui.components import (
    GATE_LABEL, INBOX_ICON, INBOX_LABEL, REASON_LABEL, STATUS_LABEL, novel_title, require_ws, show_df, show_error,
)

GATE_CHOICES = ["（挡位预设）", "manual", "review", "auto", "skip"]


def page_cockpit() -> None:
    ws = require_ws()
    if not ws:
        return
    st.header(f"🧭 {novel_title(ws)}")
    st.caption(ws)
    names = ["🧭 总控台", "🗺 规划", "✍ 写作台", "🔍 审校", "📖 阅读",
             "💡 灵感箱", "📊 度量", "⚙ 设置", "👥 设定库", "🧵 线索"]
    tabs = st.tabs(names)
    pages = [
        lambda: render_control(ws),
        lambda: planning_page.render_planning(ws),
        lambda: writing_page.render_writing(ws),
        lambda: review_page.render_review(ws),
        lambda: reading_page.render_reading(ws),
        lambda: ideas_page.render_ideas(ws),
        lambda: metrics_page.render_metrics(ws),
        lambda: settings_page.render_settings(ws),
        lambda: codex_page.render_codex(ws),
        lambda: threads_page.render_threads(ws),
    ]
    for tab, page in zip(tabs, pages):
        with tab:
            page()


def render_control(ws: str) -> None:
    from ui.autorefresh import enable_auto_refresh

    enable_auto_refresh(ws)
    res = tool_api.pipeline_status(ws)
    if not res["ok"]:
        show_error(res)
        return
    data = res["data"]
    nxt_res = tool_api.next_step(ws)
    nxt = nxt_res["data"] if nxt_res["ok"] else {"step": None, "action": "none", "message": ""}
    counts = data["counts"]
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("前沿步骤", data["frontier"] or "—")
    c2.metric("已通过", counts.get("passed", 0) + counts.get("skipped", 0))
    c3.metric("待你处理", counts.get("waiting_review", 0) + counts.get("waiting_user", 0) + counts.get("stale", 0))
    c4.metric("失败 / 阻塞", counts.get("failed", 0) + counts.get("blocked", 0))
    _render_settings(ws, data)
    live_page.render_live_panel(ws)
    _render_inbox(ws)
    _render_run_buttons(ws, nxt)
    _render_feedback_cycle(ws, data["steps"])
    _render_write_history(ws)

    stale_up = [x for x in data["steps"] if x["status"] == "stale" and str(x.get("note") or "").startswith("上游产物已变化")]
    if stale_up:
        with st.expander(f"🩹 {len(stale_up)} 个步骤因「上游产物已变化」需重跑（若核对后确认无误，可清除标记）", expanded=True):
            st.caption("这些步骤的标记可能是误判（比如归档 agent 顺手改了蓝图）。"
                       "确认产物无误后，清除标记；若确有问题，请直接运行对应步骤。")
            st.write("、".join(x["title"] for x in stale_up[:12]) + (" 等" if len(stale_up) > 12 else ""))
            confirmed = st.checkbox("我已核对：这些步骤的产物无误", key="cp_rebase_confirm")
            if st.button("清除这些需重跑标记", key="cp_rebase_go", disabled=not confirmed):
                _report_ok(tool_api.rebaseline_stale(ws))

    st.subheader("流水线")
    rows = []
    for s in data["steps"]:
        rows.append({
            "下一步": "▶" if s["id"] == nxt.get("step") else "",
            "步骤": s["id"],
            "标题": s["title"],
            "阶段": s["stage"],
            "状态": STATUS_LABEL.get(s["status"], s["status"]),
            "闸门": f"{GATE_LABEL.get(s['gate'], s['gate'])}（{s['gate_source']}）",
            "尝试": s.get("attempts") or 0,
            "说明": s.get("error") or s.get("summary") or s.get("note") or "",
        })
    show_df(pd.DataFrame(rows), width="stretch", hide_index=True, height=min(520, 40 + 35 * len(rows)))
    _render_step_actions(ws, data["steps"], nxt)


def _render_feedback_cycle(ws: str, steps: list) -> None:
    """按作者反馈修改：大模型按意见修改 → 重新核查，循环至清零或达到轮数上限。"""
    chapters = sorted({s["chapter"] for s in steps if s.get("chapter")})
    if not chapters:
        return
    with st.expander("✍ 按反馈修改并核查（大模型修改 → 重新核查）", expanded=False):
        st.caption("把修改意见交给大模型，它按意见最小修改本章，然后重新核查；有 🔴 或机器硬问题就继续修，最多 3 轮。"
                   "审稿意见本身会自动作为修改依据，意见可留空。")
        n = st.selectbox("章节", chapters, format_func=lambda c: f"第 {c} 章", key="cp_fb_chapter")
        text = st.text_area("修改意见", key="cp_fb_text", placeholder="例如：第二场对话太直白，改成含蓄的表达；去掉主角第一段的重复心理描写")
        if st.button("提交并运行", key="cp_fb_go", disabled=not text.strip()):
            r = tool_api.request_revision_cycle(ws, int(n), text)
            if not r["ok"]:
                show_error(r)
                return
            _report(tool_api.run_novel(ws, mode="single", step_id=r["data"]["step"]))


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
    "G0": "手动挡：每一步都要你点 ▶，只有强制检查点会停下来等你。运行范围：单步。",
    "G1": "引导挡：设计类与每章写作运行后等你批准，其余写前/审校自动。运行范围：到下个检查点。",
    "G2": "半自动挡：建档与第 4 章起的写作自动；设计与前三章逐章仍需批准。运行范围：每批 N 章（见预算）。",
    "G3": "全自动挡：第 4 章起全自动；设计、前三章、幕末深度审稿仍需批准。运行范围：到预算上限或全书完成。",
    "G4": "托管挡：自动批准、自动回答所有问题，一直运行到你点暂停/停止、某步失败或全书完成。每步最多自动回答 3 次，之后停下等你。所有自动决定记在运行输出里。",
}


def _render_inbox(ws: str) -> None:
    res = tool_api.inbox(ws)
    if not res["ok"]:
        show_error(res)
        return
    data = res["data"]
    if not data["items"]:
        st.caption("📥 收件箱为空：目前没有需要你处理的事项。")
        return
    title = f"📥 收件箱（{data['total']} 项，其中阻塞 {data['blocking']} 项）"
    with st.expander(title, expanded=data["blocking"] > 0):
        for it in data["items"]:
            chapter = f"第{it['chapter']}章 " if it.get("chapter") else ""
            st.markdown(f"{INBOX_ICON.get(it['severity'], '•')} **{INBOX_LABEL.get(it['kind'], it['kind'])}**　"
                        f"{chapter}{it.get('title') or ''}　—　{it['message']}")
        st.caption("收件箱由状态文件推导，worker 每次退出时刷新；点「刷新」查看最新内容。")




def _render_run_buttons(ws: str, nxt: dict) -> None:
    # 「运行下一步」明确运行 next_step 返回的那一步（与界面显示一致），不再由 worker 临时决定
    if nxt.get("step"):
        st.caption(f"▶ {nxt.get('message') or ''}")
    elif nxt.get("message"):
        st.caption(nxt["message"])
    c1, c2, c3, c4, c5 = st.columns(5)
    runnable = nxt.get("action") == "run"
    if c1.button("▶ 运行下一步", key="cp_run_single", width="stretch", disabled=not runnable,
                 help=nxt.get("message") or None):
        _report(tool_api.run_novel(ws, mode="single", step_id=nxt["step"]))
    if c2.button("▶▶ 运行到检查点", key="cp_run_until", width="stretch"):
        _report(tool_api.run_novel(ws, mode="until_gate"))
    if c3.button("⏸ 暂停", key="cp_pause", width="stretch"):
        _report(tool_api.pause_novel(ws))
    if c4.button("⏹ 停止", key="cp_stop", width="stretch"):
        _report(tool_api.stop_novel(ws))
    c5.button("🔄 刷新", key="cp_refresh", width="stretch", on_click=_reset_step_pick)


def _render_write_history(ws: str) -> None:
    """📜 写入记录：谁在什么时候改写了哪个文件。用于追溯「为什么某步骤被标为需重跑」。"""
    import pandas as pd

    with st.expander("📜 写入记录（追溯文件是谁、何时改写的）", expanded=False):
        st.caption("每次写入都会记录：写入者（步骤或作者）、所属章节、文件、前后指纹是否变化。"
                   "这是只读的追溯信息，不影响流水线的判断。")
        c1, c2, c3, c4 = st.columns([3, 2, 2, 2])
        path = c1.text_input("文件（完整相对路径，留空=全部）", key="wh_path")
        writer = c2.text_input("写入者（如 ch02.archive，留空=全部）", key="wh_writer")
        kind_label = c3.selectbox("类型", ["全部", "agent（执行器写入）", "declared（声明产出）", "author（作者修改）"],
                                  key="wh_kind")
        chapter = c4.number_input("章节（0=全部）", min_value=0, value=0, step=1, key="wh_chapter")
        c5, c6 = st.columns([2, 3])
        limit = c5.selectbox("条数", [50, 200, 500], index=0, key="wh_limit")
        only_changed = c6.checkbox("只看内容确实变化的写入", value=True, key="wh_changed")
        kind = None if kind_label.startswith("全部") else kind_label.split("（")[0]

        res = tool_api.write_history(ws, path=path.strip() or None, writer=writer.strip() or None,
                                     kind=kind, chapter=int(chapter) if chapter else None,
                                     limit=int(limit))
        if not res["ok"]:
            show_error(res)
            return
        events = res["data"]["events"]
        if only_changed:
            events = [e for e in events if e.get("before") != e.get("after")]
        if not events:
            st.info("没有符合条件的写入记录。")
            return
        rows = [{
            "时间": str(e.get("ts", ""))[:19].replace("T", " "),
            "写入者": e.get("writer") or "",
            "章节": e.get("chapter") or "",
            "类型": e.get("kind") or "",
            "文件": e.get("path") or "",
            "内容变化": "是" if e.get("before") != e.get("after") else "否",
        } for e in events]
        show_df(pd.DataFrame(rows), width="stretch", hide_index=True)


def _reset_step_pick() -> None:
    """刷新 = 重新读取当前状态：清掉单步选择框的记忆，让它回到「下一步」。
    （否则用户手动选过的步骤会一直留着，「待批准」的步骤就从面板上消失了。）"""
    st.session_state.pop("cp_step_pick", None)
    st.session_state.pop("cp_step_auto", None)


def _report(res: dict) -> None:
    if not res["ok"]:
        show_error(res)
        return
    data = res.get("data") or {}
    message = data.get("message") or "已提交。后台运行中，稍后点「刷新」查看进度。"
    if data.get("outcome") in ("queued_behind_running", "not_spawned"):
        st.warning(message)
    elif data.get("outcome") == "spawned":
        st.success(message)
    else:
        st.info(message)


def _render_step_actions(ws: str, steps: list, nxt: dict) -> None:
    st.subheader("单步操作")
    ids = [s["id"] for s in steps]
    if not ids:
        st.info("流水线为空。请先完成工作目录初始化。")
        return
    # 需要作者处理的步骤单独列出，不依赖下面的选择框——选择框可能停在别的步骤上。
    waiting = [s for s in steps if s["status"] in ("waiting_review", "waiting_user")]
    if waiting:
        with st.container(border=True):
            st.markdown("**待你处理**")
            for s in waiting:
                c_text, c_btn = st.columns([4, 2])
                label = "待批准" if s["status"] == "waiting_review" else "待回答"
                c_text.markdown(f"🟠 {s['title']}（{label}）")
                if s["status"] == "waiting_review":
                    if c_btn.button("✅ 批准", key=f"cp_top_approve_{s['id']}", width="stretch"):
                        _report_ok(tool_api.approve_step(ws, s["id"]))
                else:
                    if c_btn.button("去回答", key=f"cp_top_answer_{s['id']}", width="stretch"):
                        st.session_state["cp_step_pick"] = s["id"]
                        st.rerun()
    # 默认选中「下一步」（第一个未完成的步骤；待审核/待答复的步骤排在最前因为它阻塞流水线）。
    # 下一步变化时才自动跟随，之后用户手动选的值保持不变。
    default = nxt.get("step") if nxt.get("step") in ids else ids[0]
    if st.session_state.get("cp_step_auto") != default:
        st.session_state["cp_step_auto"] = default
        st.session_state["cp_step_pick"] = default
    pick = st.selectbox("选择步骤", ids, key="cp_step_pick",
                        help="默认是下一步（第一个未完成的步骤）；下一步变化后会自动跟随。")
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

    if step.get("outputs") and step["status"] not in ("running", "passed", "skipped"):
        with st.expander("✍ 我自己写 / 上传了产出（手写旁路）", expanded=False):
            st.caption("系统只检查产出文件是否存在，然后把本步标为已通过；内容质量由你负责。")
            st.code("\n".join(step["outputs"]), language=None)
            note = st.text_input("备注（可选）", key=f"cp_manual_note_{pick}")
            if st.button("标记为已通过", key=f"cp_manual_{pick}"):
                _report_ok(tool_api.mark_manual(ws, pick, note))

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
    is_review = step["id"].endswith(".review")
    if is_review:
        st.warning("完章核查提出了问题，请逐条决定。提交后，你的选择会作为修改意见交给大模型修改，修改后自动重新核查。")
    else:
        st.warning("这一步需要你决定：")
    with st.form(f"answer_{step['id']}"):
        answers = {}
        for q in questions:
            qid = str(q.get("id") or "q")
            opts = q.get("options") or []
            text = str(q.get("question") or "")
            if opts:
                # index=None：默认不选中，避免未确认就提交了第一个选项
                answers[qid] = st.radio(text, opts, index=None, key=f"ans_{step['id']}_{qid}")
            else:
                answers[qid] = st.text_input(text, key=f"ans_{step['id']}_{qid}")
        label = "按答复修改并核查" if is_review else "提交答复并重跑此步"
        if st.form_submit_button(label):
            missing = [str(q.get("question") or "")[:20] for q in questions
                       if q.get("options") and not answers.get(str(q.get("id") or "q"))]
            if missing:
                st.error("请为每个问题选择一个答复后再提交。")
            elif is_review:
                res = tool_api.answer_step(ws, step["id"], answers)
                if res["ok"]:
                    run = (res.get("data") or {}).get("run") or {}
                    st.success("已转为修改意见。" + (run.get("message") or "正在修改并核查，可点「刷新」查看进度。"))
                else:
                    show_error(res)
            else:
                _report_ok(tool_api.answer_step(ws, step["id"], answers))


def _report_ok(res: dict) -> None:
    if res["ok"]:
        st.success("已完成。点「刷新」查看最新状态。")
    else:
        show_error(res)
