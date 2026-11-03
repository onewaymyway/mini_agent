"""💡 灵感构思工作室（独立入口）。B1：题材卡、灵感池、发散。对话与提议在 B2 加入。"""

from __future__ import annotations

import streamlit as st

from novel_writer.brainstorm import api as BA
from novel_writer.intake import INTAKE_FIELDS
from ui.components import show_error

_STATUS_LABEL = {"keep": "保留", "park": "搁置", "drop": "丢弃", "used": "已采用"}
_SRC_LABEL = {"author": "作者", "ai": "AI"}


def _current_root() -> str:
    return st.session_state.get("bs_root", "")


def _project_picker() -> None:
    res = BA.list_projects()
    projects = res["data"] if res["ok"] else []
    if not res["ok"]:
        show_error(res)
    labels = {p["root"]: f"{p['title']}（v{p['current_version']}）" for p in projects}
    with st.expander("➕ 新建构思项目", expanded=not projects):
        title = st.text_input("项目名称", key="bs_new_title", placeholder="例如：雾港")
        if st.button("创建", key="bs_create_btn", disabled=not title.strip()):
            r = BA.create_project(title.strip())
            if r["ok"]:
                st.session_state["bs_root"] = r["data"]["root"]
                st.rerun()
            show_error(r)
    if not projects:
        st.info("还没有构思项目。先在上面新建一个。")
        return
    roots = list(labels)
    cur = _current_root()
    idx = roots.index(cur) if cur in roots else 0
    chosen = st.selectbox("当前构思项目", roots, index=idx, format_func=lambda r: labels[r], key="bs_pick")
    if chosen != cur:
        st.session_state["bs_root"] = chosen
        st.rerun()


def _brief_tab(root: str) -> None:
    res = BA.get_project(root)
    if not res["ok"]:
        show_error(res)
        return
    brief = res["data"]["cards"]["brief"]
    st.caption("题材卡与新建向导的字段一一对应；交接成小说时会作为起点。")
    with st.form("bs_brief_form"):
        values = {}
        for key, label, multiline in INTAKE_FIELDS:
            if multiline:
                values[key] = st.text_area(label, value=brief.get(key, ""), key=f"bs_b_{key}")
            else:
                values[key] = st.text_input(label, value=brief.get(key, ""), key=f"bs_b_{key}")
        if st.form_submit_button("保存题材卡"):
            r = BA.save_brief(root, values)
            st.success("已保存。") if r["ok"] else show_error(r)


def _ideas_tab(root: str) -> None:
    with st.form("bs_add_idea", clear_on_submit=True):
        text = st.text_area("新灵感", key="bs_idea_text", height=80)
        tags = st.text_input("标签（用逗号分隔，可空）", key="bs_idea_tags")
        if st.form_submit_button("加入灵感池") and text.strip():
            r = BA.add_idea(root, text, [t for t in tags.replace("，", ",").split(",")])
            if not r["ok"]:
                show_error(r)
    status_filter = st.radio("筛选", ["全部", "保留", "搁置", "已采用", "丢弃"], horizontal=True, key="bs_idea_filter")
    wanted = {"全部": None, "保留": "keep", "搁置": "park", "已采用": "used", "丢弃": "drop"}[status_filter]
    res = BA.list_ideas(root, wanted)
    if not res["ok"]:
        show_error(res)
        return
    if not res["data"]:
        st.caption("（暂无）")
    for idea in res["data"]:
        with st.container(border=True):
            st.write(idea["text"])
            meta = f"{_SRC_LABEL.get(idea['source'], idea['source'])} · {_STATUS_LABEL.get(idea['status'], idea['status'])}"
            if idea.get("tags"):
                meta += " · " + "、".join(idea["tags"])
            st.caption(meta)
            cols = st.columns(4)
            for col, (status, label) in zip(cols, [("keep", "保留"), ("park", "搁置"), ("used", "已采用"), ("drop", "丢弃")]):
                if col.button(label, key=f"bs_st_{idea['id']}_{status}", disabled=idea["status"] == status):
                    r = BA.update_idea(root, idea["id"], status=status)
                    show_error(r) if not r["ok"] else st.rerun()


def _spark_tab(root: str) -> None:
    st.caption("让 AI 围绕一个方向给出候选。候选不会直接进入灵感池，需要你逐条采纳。")
    direction = st.text_input("方向（可空）", key="bs_spark_dir", placeholder="例如：反派的动机、不同寻常的城市")
    count = st.slider("候选数量", 3, 10, 6, key="bs_spark_n")
    if st.button("▶ 发散", key="bs_spark_go"):
        with st.spinner("正在生成候选……"):
            r = BA.spark(root, direction, count)
        if r["ok"]:
            st.session_state["bs_spark_last"] = r["data"]["id"]
        else:
            show_error(r)
    hist = BA.spark_history(root)
    if not hist["ok"]:
        show_error(hist)
        return
    for rec in hist["data"][:5]:
        head = rec.get("direction") or "不限方向"
        with st.expander(f"{rec['created_at']} · {head}", expanded=rec["id"] == st.session_state.get("bs_spark_last")):
            picks = []
            for cand in rec["candidates"]:
                label = cand["text"] + (f"  〔{'、'.join(cand['tags'])}〕" if cand["tags"] else "")
                if cand.get("adopted"):
                    st.write(f"✅ {label}")
                elif st.checkbox(label, key=f"bs_pick_{rec['id']}_{cand['cid']}"):
                    picks.append(cand["cid"])
            if st.button("采纳所选", key=f"bs_adopt_{rec['id']}", disabled=not picks):
                r = BA.adopt_candidates(root, rec["id"], picks)
                show_error(r) if not r["ok"] else st.rerun()


def page_brainstorm() -> None:
    st.header("💡 灵感构思工作室")
    st.caption("独立于小说项目：先把灵感、题材、世界观与剧情打磨成构思包，之后再据此创建小说。")
    _project_picker()
    root = _current_root()
    if not root:
        return
    tabs = st.tabs(["题材卡", "灵感池", "发散"])
    with tabs[0]:
        _brief_tab(root)
    with tabs[1]:
        _ideas_tab(root)
    with tabs[2]:
        _spark_tab(root)
