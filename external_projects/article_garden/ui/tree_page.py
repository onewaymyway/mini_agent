"""ui/tree_page.py — 📚→🌳 知识树（P3）：挂载提案（确认后应用）、覆盖度、树视图、节点的人工操作。

自动挂载在后台生成提案；应用、放弃、改名、锁定、移动、合并、新建主题都在本页完成，并立即生效（每次保存前备份旧树）。
"""

from __future__ import annotations

import streamlit as st

from article_garden import joblog
from article_garden.library import catalog
from article_garden.wiki import coverage, model, placement, tree
from ui import state
from ui.jobs import spawn
from ui.topbar import render_topbar

DOT = {"red": "🔴", "yellow": "🟡", "green": "🟢"}


def render_tree_page() -> None:
    ctx = state.current_ctx()
    if ctx is None:
        return
    render_topbar(ctx, "🌳 知识树")
    busy = joblog.is_running(ctx.root, scopes=("tree",))
    _proposal_section(ctx, busy)
    st.divider()
    data = tree.load(ctx)
    if not data["nodes"]:
        st.info("知识树还是空的。先完成「概念与关系」，再生成挂载提案（骨架来自章程的范围）。")
        return
    cov = coverage.read(ctx)
    if not cov.get("nodes"):
        cov = coverage.compute(ctx)
    _tree_view(ctx, data, cov)
    st.divider()
    _node_editor(ctx, data, cov)


def _proposal_section(ctx, busy: bool) -> None:
    st.subheader("挂载提案")
    if st.button("生成挂载提案（后台）", disabled=busy, type="primary", key="tree_propose"):
        spawn("build_tree.py", ctx.id)
        st.success("已在后台生成提案，完成后刷新本页。")
    p = placement.load_proposal(ctx)
    if not p:
        st.caption("没有待确认的提案。新增文章或概念后，再生成提案即可看到增量挂载的变化。")
        return
    s = p.get("summary", {})
    st.info(f"待确认：新建主题 {s.get('new_topics', 0)}，挂载概念 {s.get('attached', 0)}，"
            f"解除挂载 {s.get('detached', 0)}，新增方向 {s.get('skeleton', 0)}。")
    names = _names(ctx, tree.load(ctx))
    rows = []
    for op in p["ops"]:
        if op["op"] == "add_node":
            rows.append({"操作": "新建" + ("方向" if op["level"] == tree.DIRECTION else "主题"), "内容": op["name"],
                         "位置": names.get(op.get("parent"), "顶层")})
        elif op["op"] == "attach":
            rows.append({"操作": "挂载概念", "内容": names.get(op["concept"], op["concept"]),
                         "位置": names.get(op["node"], op["node"])})
        else:
            rows.append({"操作": "解除挂载", "内容": names.get(op["concept"], op["concept"]), "位置": ""})
    st.dataframe(rows, hide_index=True, use_container_width=True)
    c1, c2 = st.columns(2)
    if c1.button("应用提案", key="tree_apply"):
        try:
            placement.apply_proposal(ctx)
            coverage.compute(ctx)
            st.success("已应用，知识树已更新（旧树已备份）。")
            st.rerun()
        except ValueError as exc:
            st.error(str(exc))
    if c2.button("放弃提案", key="tree_discard"):
        placement.discard_proposal(ctx)
        st.rerun()


def _names(ctx, data) -> dict:
    out = dict(_concept_names(ctx))
    out.update({nid: n["name"] for nid, n in data["nodes"].items()})
    return out


def _tree_view(ctx, data, cov) -> None:
    st.subheader("知识树")
    counts = {c: 0 for c in DOT}
    for m in cov.get("nodes", {}).values():
        counts[m.get("color", "red")] = counts.get(m.get("color", "red"), 0) + 1
    st.caption(f"覆盖度：🔴 空洞 {counts['red']} ｜ 🟡 偏浅 {counts['yellow']} ｜ 🟢 充足 {counts['green']}")
    concept_names = _concept_names(ctx)
    for did, d in tree.nodes_at(data, tree.DIRECTION):
        m = cov.get("nodes", {}).get(did, {})
        with st.expander(f"{DOT.get(m.get('color'), '⚪')} {d['name']}　（{m.get('article_count', 0)} 篇）"
                         + ("　📌" if d.get("pinned") else "")):
            topics = tree.children(data, did)
            if not topics:
                st.caption("这个方向下还没有主题。")
            for tid, t in topics:
                tm = cov.get("nodes", {}).get(tid, {})
                label = f"{DOT.get(tm.get('color'), '⚪')} {t['name']}　{tm.get('article_count', 0)} 篇"
                st.markdown(label + ("　📌锁定" if t.get("pinned") else "") + ("　✎" if t.get("human_edited") else ""))
                names = [concept_names.get(c, c) for c in t.get("concepts") or []]
                st.caption("概念：" + ("、".join(names) if names else "（无）"))


def _node_editor(ctx, data, cov) -> None:
    st.subheader("节点操作")
    ids = list(data["nodes"])
    labels = {nid: f"{nid}  {n['name']}（{'方向' if n['level'] == tree.DIRECTION else '主题'}）"
              for nid, n in data["nodes"].items()}
    sel = st.selectbox("选择节点", ids, format_func=lambda i: labels[i], key="tree_sel")
    node = data["nodes"][sel]
    m = cov.get("nodes", {}).get(sel, {})
    st.markdown(f"覆盖度 {DOT.get(m.get('color'), '⚪')}：{m.get('article_count', 0)} 篇 ｜ 平均 {m.get('avg_chars', 0)} 字 ｜ "
                f"断言 {m.get('claims', 0)} ｜ 有依据概念占比 {m.get('evidence_share', 0)} ｜ 悬空概念 {m.get('dangling', 0)}")
    titles = {r["id"]: r["title"] for r in catalog.list_articles(ctx)}
    arts = _node_articles(ctx, data, sel)
    if arts:
        st.caption("文章：" + "；".join(f"{a} {titles.get(a, '')}" for a in arts))

    c1, c2 = st.columns([3, 1])
    new_name = c1.text_input("改名", value=node["name"], key=f"tree_name_{sel}")
    if c2.button("保存名称", key=f"tree_rename_{sel}"):
        _try(lambda: tree.rename(ctx, sel, new_name))
    pinned = st.checkbox("锁定（不接收自动挂载）", value=bool(node.get("pinned")), key=f"tree_pin_{sel}")
    if pinned != bool(node.get("pinned")):
        _try(lambda: tree.set_pinned(ctx, sel, pinned))
    if node["level"] == tree.TOPIC:
        dirs = {nid: n["name"] for nid, n in tree.nodes_at(data, tree.DIRECTION)}
        target = st.selectbox("移动到方向", list(dirs), format_func=lambda i: dirs[i],
                              index=list(dirs).index(node["parent"]) if node.get("parent") in dirs else 0,
                              key=f"tree_move_{sel}")
        if st.button("移动", key=f"tree_mv_btn_{sel}") and target != node.get("parent"):
            _try(lambda: tree.move(ctx, sel, target))
        same = {nid: n["name"] for nid, n in tree.nodes_at(data, tree.TOPIC) if nid != sel}
        if same:
            other = st.selectbox("合并进（并删除本节点）", list(same), format_func=lambda i: same[i], key=f"tree_mg_{sel}")
            if st.button("合并", key=f"tree_mg_btn_{sel}"):
                _try(lambda: tree.merge(ctx, sel, other))
    if node["level"] == tree.DIRECTION:
        new_topic = st.text_input("在此方向下新建主题", key=f"tree_new_{sel}")
        if st.button("新建主题", key=f"tree_new_btn_{sel}"):
            _try(lambda: tree.create_topic(ctx, new_topic, sel))


def _try(fn) -> None:
    try:
        fn()
        st.rerun()
    except ValueError as exc:
        st.error(str(exc))


def _concept_names(ctx) -> dict:
    return {m["id"]: m["name"] for m in model.list_concepts(ctx)}


def _node_articles(ctx, data, nid) -> list:
    concepts = set(coverage._concepts_under(data, nid))
    out = []
    for m in model.list_concepts(ctx):
        if m["id"] in concepts:
            out += m.get("articles") or []
    return sorted(set(out))
