"""🩺 巡检整改项：查看、采纳、忽略，以及开启「巡检反馈」的分级开关（默认全关）。"""

from __future__ import annotations

import streamlit as st

from novel_writer import patrol_findings as PF
from novel_writer import tool_api
from ui.components import show_error

_SWITCHES = [
    ("extract", "提取整改项", "巡检后自动把报告整理成整改项（多一次便宜的 LLM 调用 + 一个确定性分流步骤）"),
    ("auto_adopt", "自动采纳", "「约束后续章节」与「避免套路」自动采纳，不需要你点；关闭后需在下方手动采纳"),
    ("inject_forward", "注入后续约束", "已采纳的「约束后续章节」整改项写入之后每一章的上下文包"),
    ("inject_style", "注入避免套路", "已采纳的「避免套路」写入风格提示，并在机器校验里给出提示（不阻断）"),
    ("to_inbox", "收件箱提醒", "待你决定的整改项、蓝图待办、回改建议显示在收件箱"),
]
_SEV_ICON = {"严重": "🔴", "一般": "🟡"}


def render_patrol(ws: str) -> None:
    res = tool_api.patrol_findings(ws)
    if not res["ok"]:
        show_error(res)
        return
    data = res["data"]
    findings, fb = data["findings"], data["feedback"]
    pending = [f for f in findings if f["status"] == PF.S_PROPOSED]
    with st.expander(f"🩺 巡检整改项（{len(findings)} 条，其中需你过目 {sum(1 for f in pending if f['scope'] in (PF.SCOPE_BLUEPRINT, PF.SCOPE_RETRO))}）", expanded=False):
        _render_switches(ws, fb, data["gear"])
        if not findings:
            st.info("暂无整改项。精品档每 10 章巡检之后会自动生成（全自动，无需操作）。")
            return
        groups = [("待决定", PF.S_PROPOSED), ("已采纳", PF.S_ADOPTED), ("已处理", PF.S_DONE), ("已忽略", PF.S_DISMISSED)]
        for label, status in groups:
            rows = [f for f in findings if f["status"] == status]
            if not rows:
                continue
            st.markdown(f"**{label}（{len(rows)}）**")
            for f in rows:
                _render_finding(ws, f)


def _render_switches(ws: str, fb: dict, gear: str) -> None:
    st.caption("整条链路默认全自动：巡检后自动提取、自动采纳并注入后续写作，不需要你操作；以下开关可单项关闭。"
               "只有「蓝图待办」与「已写章节回改建议」不会自动执行（会改蓝图/正文），仅在收件箱提示，不阻塞流水线。")
    cols = st.columns(len(_SWITCHES))
    new = {}
    for col, (key, label, help_) in zip(cols, _SWITCHES):
        new[key] = col.checkbox(label, value=bool(fb.get(key)), help=help_, key=f"pt_sw_{key}")
    if new != {k: bool(fb.get(k)) for k in new} and st.button("保存开关", key="pt_sw_save"):
        r = tool_api.update_settings(ws, {"patrol_feedback": new})
        if r["ok"]:
            st.success("已保存，从下一步起生效。")
            st.rerun()
        else:
            show_error(r)


def _render_finding(ws: str, f: dict) -> None:
    icon = _SEV_ICON.get(f["severity"], "•")
    head = f"{icon} **{f['id']}** ［{PF.SCOPE_LABEL.get(f['scope'], f['scope'])}·{f['dimension']}］ {f['title']}"
    st.markdown(head)
    st.caption(f["directive"])
    meta = []
    if f.get("chapters"):
        meta.append("章节：" + "、".join(str(c) for c in f["chapters"]))
    if f.get("level"):
        meta.append("建议级别：" + f["level"])
    if f.get("avoid_phrases"):
        meta.append("避免：" + "、".join(f["avoid_phrases"]))
    if f.get("foreshadowing"):
        meta.append("伏笔：" + "、".join(f["foreshadowing"]))
    if f.get("characters"):
        meta.append("人物：" + "、".join(f["characters"]))
    if f.get("decided_by") == "auto":
        meta.append("按挡位自动采纳")
    if meta:
        st.caption("　".join(meta))
    c1, c2, c3, _ = st.columns([1, 1, 1, 3])
    status = f["status"]
    if status == PF.S_PROPOSED:
        if c1.button("采纳", key=f"pt_ok_{f['id']}"):
            _act(ws, f["id"], PF.S_ADOPTED)
        if c2.button("忽略", key=f"pt_no_{f['id']}"):
            _act(ws, f["id"], PF.S_DISMISSED)
    elif status == PF.S_ADOPTED:
        if c1.button("标记已处理", key=f"pt_done_{f['id']}"):
            _act(ws, f["id"], PF.S_DONE)
        if c2.button("撤销采纳", key=f"pt_undo_{f['id']}"):
            _act(ws, f["id"], PF.S_PROPOSED)
    else:
        if c1.button("恢复为待决定", key=f"pt_re_{f['id']}"):
            _act(ws, f["id"], PF.S_PROPOSED)
    if f["scope"] == PF.SCOPE_RETRO and status in (PF.S_PROPOSED, PF.S_ADOPTED) and f.get("chapters"):
        ch = c3.selectbox("回改章节", f["chapters"], key=f"pt_ch_{f['id']}", label_visibility="collapsed")
        if st.button(f"为第 {ch} 章创建 {f.get('level')} 修订请求", key=f"pt_rev_{f['id']}"):
            r = tool_api.request_revision(ws, int(ch), str(f["level"]), f"【巡检整改 {f['id']}】{f['directive']}")
            if r["ok"]:
                st.success("已创建修订请求，请到审校页确认后执行（不会自动改正文）。")
            else:
                show_error(r)


def _act(ws: str, fid: str, status: str) -> None:
    r = tool_api.patrol_set_status(ws, fid, status)
    if r["ok"]:
        st.rerun()
    else:
        show_error(r)
