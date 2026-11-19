"""ui/cockpit_page.py — 🧭 驾驶舱：项目总览、当前步骤、实时运行面板与章节流水线。

参考 novel_writer 驾驶舱的结构：顶部指标 → 下一步 → 实时面板（定时刷新）→ 流水线表。
实时面板是 fragment，只重绘自身，不会打断其他区域的输入。
"""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from novel_to_video import tool_api
from ui.chapter_page import _pick_project
from ui.common import DEFAULT_PROJECTS_ROOT

REFRESH_SECONDS = 3

STATUS_LABEL = {
    "pending": "待运行", "running": "运行中", "waiting_assets": "等待素材/补建",
    "waiting_review": "等待逐镜审核", "failed": "失败", "done": "已完成", "stale": "需重跑",
}
RUN_LABEL = {"idle": "空闲", "running": "运行中", "stopping": "正在停止", "halted": "已停止",
             "done": "已完成", "failed": "失败", "interrupted": "已中断（已回收，可重新开始）",
             "stopped": "已停止"}
GEAR_LABEL = {"G0": "G0 手动", "G1": "G1 引导", "G2": "G2 半自动", "G3": "G3 全自动",
              "G4": "G4 托管", "G5": "G5 不停机", "G6": "G6 全流程自动"}


def page_cockpit() -> None:
    st.header("🧭 驾驶舱")
    st.caption("一眼看清当前步骤、待处理事项与运行状态。")
    root = st.text_input("项目存放目录", value=DEFAULT_PROJECTS_ROOT, key="ck_root")
    pdir = _pick_project(root)
    if not pdir:
        return
    res = tool_api.cockpit_status(pdir)
    if not res["ok"]:
        st.error(res["error"]["message"])
        return
    d = res["data"]
    _header_metrics(d)
    nxt = d["next"]
    if nxt["key"] in ("done",):
        st.success(f"✅ {nxt['title']}：{nxt['reason']}")
    else:
        st.info(f"👉 当前步骤：**{nxt['title']}**　—　{nxt['reason']}　（前往「{nxt['page']}」）")
    _live_panel(pdir)
    _start_controls(pdir, d)
    _timeline_panel(pdir)
    _chapter_table(d)
    _reset_panel(pdir, d)
    _rename_panel(pdir, d)
    _gates(d)
    from ui.joblog_panel import render_job_panel
    st.subheader("最近日志")
    render_job_panel(pdir, None, title="全部日志", height=260, limit=120)


def _header_metrics(d: dict) -> None:
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("当前步骤", d["next"]["title"])
    c2.metric("章节进度", f"{d['done']} / {len(d['selected'])}")
    waiting = d["shots"]["waiting"] + d["gender"]["pending"]
    c3.metric("待你处理", waiting, help="待审镜头 + 待确认性别")
    c4.metric("失败镜头", d["shots"]["failed"])
    b = d["budget"]
    st.caption(f"挡位 {GEAR_LABEL.get(d['gear'], d['gear'])}　·　"
               f"视频 {b['used']['video']}/{b['limits']['video'] or '—'}　·　"
               f"图像 {b['used']['image']}/{b['limits']['image'] or '—'}　·　"
               f"耗时 {b['used']['minutes']}/{b['limits']['minutes'] or '—'} 分钟")


@st.fragment(run_every=REFRESH_SECONDS)
def _live_panel(pdir: str) -> None:
    st.subheader("实时运行")
    res = tool_api.cockpit_run(pdir)           # 轻量：只取运行状态（完整汇总只在整页渲染时做一次）
    if not res["ok"]:
        st.error(res["error"]["message"])
        return
    run = res["data"]["run"]
    state = RUN_LABEL.get(run["state"], run["state"])
    if run["busy"]:
        cur = f"第 {run['chapter']} 章" if run.get("chapter") else "章节间"
        st.write(f"状态：**{state}**　·　当前：{cur}　·　{run.get('message') or ''}")
        if run["state"] == "running":
            if st.button("立即停止", key="ck_stop"):
                r = tool_api.stop_queue(pdir)
                (st.success if r["ok"] else st.error)("已请求停止" if r["ok"] else r["error"]["message"])
    else:
        st.write(f"状态：**{state}**　·　{run.get('message') or '没有正在运行的任务'}")
    if run.get("history"):
        with st.expander("本次运行记录", expanded=run["busy"]):
            for h in reversed(run["history"]):
                st.write(f"{h['at']}　第 {h['chapter']} 章 → {STATUS_LABEL.get(h['status'], h['status'])}　{h.get('message', '')}")


_TL_STATUS = {"ok": "✔ 完成", "failed": "✘ 失败", "stopped": "⏹ 已停止", "running": "⏳ 进行中",
              "interrupted": "⚠ 中断（无结束记录）", "done": "✔ 完成", "waiting_review": "⏸ 等待审核",
              "waiting_assets": "⏸ 等待素材"}


def _hms(ts) -> str:
    return str(ts)[11:19] if ts else "—"


def _dur(sec) -> str:
    if sec is None:
        return "—"
    sec = float(sec)
    return f"{sec:.1f} 秒" if sec < 60 else f"{int(sec // 60)} 分 {int(sec % 60)} 秒"


@st.fragment(run_every=REFRESH_SECONDS)
def _timeline_panel(pdir: str) -> None:
    """运行时间线：章节流水线 / 全书素材 / 全书合成三个标签页，显示步骤起止时间、用时、状态、完整失败原因。默认收起。
    是 fragment，每 REFRESH_SECONDS 秒自动刷新自身（只重绘本面板，不打断其他输入）；日志按增量读取，刷新开销很小。"""
    with st.expander("⏱ 运行时间线（步骤用时 / 失败重试）", expanded=False):
        t_ch, t_as, t_fi = st.tabs(["章节流水线", "全书素材", "全书合成"])
        with t_ch:
            _chapter_timeline(pdir)
        with t_as:
            _book_timeline(pdir, ["assets", "gender"], "还没有全书素材的运行记录（运行设定补全 / 定妆图 / 性别检索后出现）。")
        with t_fi:
            _book_timeline(pdir, ["final"], "还没有全书合成的运行记录（渲染封面 / 合成全书后出现）。")


def _show_tb(tb) -> None:
    """显示异常堆栈（没有堆栈的失败，如模型/接口返回错误而非抛异常，不显示）。"""
    if tb:
        st.caption("异常堆栈：")
        st.code(str(tb), language="python")


def _book_timeline(pdir: str, scopes: list, empty: str) -> None:
    res = tool_api.book_run_timeline(pdir, scopes)
    if not res["ok"]:
        st.error(res["error"]["message"])
        return
    runs = res["data"]
    if not runs:
        st.caption(empty)
        return
    for r in runs[:15]:
        st.markdown(f"**{r['run']}**　{_TL_STATUS.get(r['status'], r['status'])}　"
                    f"{str(r['started_at'])[5:16].replace('T', ' ')} → {_hms(r['ended_at'])}　用时 {_dur(r.get('seconds'))}")
        rows = [{"步骤": s["stage"], "开始": _hms(s.get("started_at")), "结束": _hms(s.get("ended_at")),
                 "用时": _dur(s.get("seconds")), "状态": _TL_STATUS.get(s["status"], s["status"])}
                for s in r["steps"]]
        if rows:
            st.dataframe(rows, width="stretch", hide_index=True)
        for s in r["steps"]:
            if s.get("error") and s["error"] != r.get("error"):
                st.markdown(f"步骤「{s['stage']}」失败原因（完整）：")
                st.code(str(s["error"]), language=None)
                _show_tb(s.get("traceback"))
        if r.get("error"):
            st.markdown("失败原因（完整）：")
            st.code(str(r["error"]), language=None)
            _show_tb(r.get("traceback"))


def _chapter_timeline(pdir: str) -> None:
    res = tool_api.run_timeline(pdir)
    if not res["ok"]:
        st.error(res["error"]["message"])
        return
    data = res["data"]
    if not data:
        st.caption("还没有带时间的运行记录（运行一次章节后出现）。")
        return
    nos = sorted(data, reverse=True)
    no = st.selectbox("章节", nos, format_func=lambda n: f"第 {n} 章", key="tl_chapter")
    c = data[no]
    st.write(f"累计用时 **{_dur(c['total_seconds'])}**　·　尝试 **{len(c['attempts'])}** 次　·　重试 **{c['retry_count']}** 次")
    retries = {r["attempt"]: r for r in c["retries"]}
    for a in reversed(c["attempts"]):
        st.markdown(f"**第 {a['attempt']} 次尝试**　{_TL_STATUS.get(a['status'], a['status'])}　"
                    f"{_hms(a['started_at'])} → {_hms(a['ended_at'])}　用时 {_dur(a.get('seconds'))}")
        rows = [{"步骤": s["stage"], "开始": _hms(s.get("started_at")), "结束": _hms(s.get("ended_at")),
                 "用时": _dur(s.get("seconds")), "状态": _TL_STATUS.get(s["status"], s["status"])}
                for s in a["stages"]]
        if rows:
            st.dataframe(rows, width="stretch", hide_index=True)
        for s in a["stages"]:
            if s.get("error"):
                st.markdown(f"步骤 {s['stage']} 失败原因（完整）：")
                st.code(str(s["error"]), language=None)
                _show_tb(s.get("traceback"))
        if a.get("error"):
            st.markdown("本次尝试失败原因（完整）：")
            st.code(str(a["error"]), language=None)
        nxt = retries.get(a["attempt"])   # 第 n 次重试是“第 n 次尝试失败之后”的动作
        if nxt:
            st.caption(f"↻ {_hms(nxt['at'])} 决定重试（第 {nxt['attempt']} 次），等待 {nxt.get('wait_sec') or 0:g} 秒，"
                       f"计划 {_hms(nxt.get('next_at'))} 开始下一次尝试")


def _start_controls(pdir: str, d: dict) -> None:
    if d["run"]["busy"]:
        return
    with st.expander("▶ 启动运行", expanded=d["next"]["key"] == "run"):
        st.caption(f"将运行选中的 {len(d['selected'])} 章，挡位 {GEAR_LABEL.get(d['gear'], d['gear'])}。"
                   "运行在后台进行，可以离开本页，回来后在实时面板查看。")
        if st.button("开始运行队列", key="ck_start", type="primary"):
            r = tool_api.start_queue_background(pdir)
            (st.success if r["ok"] else st.error)("已在后台开始运行" if r["ok"] else r["error"]["message"])
            if r["ok"]:
                st.rerun()


def _chapter_table(d: dict) -> None:
    st.subheader("章节流水线")
    if not d["chapters"]:
        st.info("没有选中的章节。")
        return
    current = d["run"].get("chapter")
    rows = []
    for c in d["chapters"]:
        mark = "▶ " if c["no"] == current else ""
        rows.append({
            "章": f"{mark}第 {c['no']} 章",
            "章名": c["title"],
            "状态": STATUS_LABEL.get(c["status"], c["status"]),
            "镜头": f"{c['approved']} / {c['shots_total']} 已通过",
            "待审": c["waiting"],
            "失败": c["failed"],
            "成片": "✅" if c["has_output"] else "—",
            "说明": c["message"],
        })
    st.dataframe(rows, width="stretch", hide_index=True)


def _reset_panel(pdir: str, d: dict) -> None:
    """章节流水线的整章重置：删除该章全部生成产物，下次运行从剧本重新生成。全书设定与原文保留。"""
    busy = d["run"]["busy"]
    if not d["chapters"]:
        return
    with st.expander("♻️ 整章重置（从剧本重新生成）"):
        if busy:
            st.info("队列正在运行，停止后才能整章重置。")
        st.caption("删除所选章节的剧本、镜头、配音、提示词、场景图、视频与封面，章节回到待运行，下次运行从剧本重新生成。"
                   "章节原文、全书角色与地点设定、其他章节不受影响。此操作不可撤销。")
        labels = {c["no"]: f"第 {c['no']} 章 {c['title'] or ''}".rstrip() for c in d["chapters"]}
        no = st.selectbox("选择章节", options=list(labels), format_func=lambda n: labels[n],
                          key="ck_reset_no", disabled=busy)
        sure = st.checkbox(f"我确认清除第 {no} 章的全部生成产物，并从剧本重新生成", key=f"ck_reset_ok_{no}", disabled=busy)
        if st.button("整章重置", key="ck_reset_btn", disabled=busy or not sure, type="secondary"):
            r = tool_api.reset_chapter(pdir, int(no))
            if r["ok"]:
                n = len(r["data"]["removed"])
                msg = f"第 {no} 章已整章重置（清除 {n} 项产物），下次运行从剧本开始"
                if r["data"]["final_marked_stale"]:
                    msg += "；全书成片已标记为过期，需重新合成"
                st.toast(msg)
                st.rerun()
            else:
                st.error(r["error"]["message"])


def _rename_panel(pdir: str, d: dict) -> None:
    """修改小说名与章节名。只改显示名，不影响已生成的剧本、素材和审核；队列运行中不可修改。"""
    busy = d["run"]["busy"]
    with st.expander("✏️ 修改小说名与章节名"):
        if busy:
            st.info("队列正在运行，停止后才能修改名字。")
        st.caption("只改名字：不会重新生成剧本、素材或视频。章节封面与整书封面里的名字在下次合成时按新名字重绘"
                   "（已完成的章节点「重新合成本章」）；整书封面的标题在整书合成页单独设置。")
        c1, c2 = st.columns([4, 1])
        title = c1.text_input("小说名", value=d.get("title") or "", key="ck_book_title", disabled=busy)
        c2.write("")
        if c2.button("保存小说名", key="ck_book_title_save", disabled=busy):
            r = tool_api.rename_project(pdir, title)
            (st.success if r["ok"] else st.error)("已保存小说名" if r["ok"] else r["error"]["message"])
            if r["ok"]:
                st.rerun()
        ov = tool_api.chapters_overview(pdir)
        if not ov["ok"]:
            st.error(ov["error"]["message"])
            return
        rows = [{"章号": r["no"], "章名": r["title"] or ""} for r in ov["data"]]
        edited = st.data_editor(rows, key="ck_chapter_titles", hide_index=True, width="stretch", disabled=busy,
                                column_config={"章号": st.column_config.NumberColumn(disabled=True),
                                               "章名": st.column_config.TextColumn(max_chars=80)})
        if st.button("保存章节名", key="ck_chapter_titles_save", disabled=busy):
            old = {r["章号"]: r["章名"] for r in rows}
            diff = {int(r["章号"]): r["章名"] for r in edited if (r["章名"] or "") != old.get(r["章号"], "")}
            if not diff:
                st.info("没有修改。")
            else:
                r = tool_api.rename_chapters(pdir, diff)
                if r["ok"]:
                    st.success(f"已修改 {len(r['data']['changed'])} 章的章名")
                    st.rerun()
                else:
                    st.error(r["error"]["message"])


def _gates(d: dict) -> None:
    st.subheader("检查点")
    fin = d["final"]
    items = [
        ("角色性别已确认", d["gender"]["pending"] == 0, f"待确认 {d['gender']['pending']} 个"),
        ("全书素材就绪", d["assets"]["ready"], f"未通过 {d['assets']['not_ready']} 个实体"),
        ("所有选中章节完成", fin["ready"], "；".join(fin["problems"][:2]) or "—"),
        ("全书合成人工确认", fin["confirmed"], "合成前必须确认（任何挡位）"),
        ("全书成片", fin["exists"] and not fin["stale"], "已过期，需重新合成" if fin["stale"] else "—"),
    ]
    for label, ok, note in items:
        st.markdown(f"{'✅' if ok else '⬜'} {label}　<span style='color:gray'>{note}</span>", unsafe_allow_html=True)
