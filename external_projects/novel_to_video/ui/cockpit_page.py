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
             "done": "已完成", "failed": "失败"}
GEAR_LABEL = {"G0": "G0 手动", "G1": "G1 引导", "G2": "G2 半自动", "G3": "G3 全自动",
              "G4": "G4 托管", "G5": "G5 不停机"}


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
    _chapter_table(d)
    _gates(d)


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
    res = tool_api.cockpit_status(pdir)
    if not res["ok"]:
        st.error(res["error"]["message"])
        return
    run = res["data"]["run"]
    state = RUN_LABEL.get(run["state"], run["state"])
    if run["busy"]:
        cur = f"第 {run['chapter']} 章" if run.get("chapter") else "章节间"
        st.write(f"状态：**{state}**　·　当前：{cur}　·　{run.get('message') or ''}")
        if run["state"] == "running":
            if st.button("请求停止（当前章节完成后停止）", key="ck_stop"):
                r = tool_api.stop_queue(pdir)
                (st.success if r["ok"] else st.error)("已请求停止" if r["ok"] else r["error"]["message"])
    else:
        st.write(f"状态：**{state}**　·　{run.get('message') or '没有正在运行的任务'}")
    if run.get("history"):
        with st.expander("本次运行记录", expanded=run["busy"]):
            for h in reversed(run["history"]):
                st.write(f"{h['at']}　第 {h['chapter']} 章 → {STATUS_LABEL.get(h['status'], h['status'])}　{h.get('message', '')}")


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
