"""ui/graph_page.py — 🕸 概念图（P2）：概念列表、定义与依据、关系、冲突，以及以当前概念为中心的关系小图。

页面只读取 wiki/ 下的文件并展示；重新构建在后台运行（entrypoints/build_wiki.py）。
"""

from __future__ import annotations

import streamlit as st

from article_garden import joblog
from article_garden.digest import runner
from article_garden.library import catalog
from article_garden.wiki import model
from ui import state
from ui.jobs import spawn
from ui.topbar import render_topbar


def render_graph_page() -> None:
    ctx = state.current_ctx()
    if ctx is None:
        return
    render_topbar(ctx, "🕸 概念图")
    busy = joblog.recently_active(ctx.root, scopes=("wiki",))
    digests_ok = sum(1 for r in catalog.list_articles(ctx) if (runner.load_digest(ctx, r["id"]) or {}).get("status") == "ok")
    concepts = model.list_concepts(ctx)

    if st.button("构建概念与关系（后台）", disabled=busy or digests_ok == 0, type="primary", key="build_wiki"):
        spawn("build_wiki.py", ctx.id)
        st.success("已在后台开始构建，进度见「▶ 运行日志」。完成后刷新本页。")
    if busy:
        st.caption("构建正在进行中……")
    if not concepts:
        st.info("还没有概念。先完成文章摘要，再构建概念与关系。" if digests_ok == 0
                else f"已有 {digests_ok} 篇摘要，点击上方按钮构建概念与关系。")
        return

    conflicts = model.read_jsonl(ctx, "conflicts.jsonl")
    edges = model.read_jsonl(ctx, "edges.jsonl")
    claims = model.read_jsonl(ctx, "claims.jsonl")
    names = {c["id"]: c["name"] for c in concepts}
    st.dataframe([{"编号": c["id"], "概念": c["name"], "别名": "、".join(c.get("aliases") or []),
                   "出处文章": len(c.get("articles") or []), "断言": len(c.get("claims") or []),
                   "状态": {"ok": "正常", "no_source": "无依据", "failed": "失败"}.get(c.get("status"), "")}
                  for c in concepts], hide_index=True, use_container_width=True)

    sel = st.selectbox("查看概念", [c["id"] for c in concepts], format_func=lambda i: f"{i}  {names[i]}",
                       key="graph_sel")
    meta, body = model.read_page(ctx, sel) or ({}, "")
    if meta.get("status") == "failed":
        st.error("定义生成失败：" + "；".join(meta.get("issues") or []))
    st.markdown(body.split("## 依据")[0] if body else "（无页面）")
    if meta.get("human_edited"):
        st.caption("✎ 此概念已人工编辑，自动构建不会覆盖。")

    tab1, tab2, tab3 = st.tabs(["关系", "断言与出处", "冲突"])
    with tab1:
        rel = [e for e in edges if sel in (e["src"], e["dst"])]
        if rel:
            rows = []
            for e in rel:
                other = e["dst"] if e["src"] == sel else e["src"]
                ev = e["evidence"][0] if e["evidence"] else {}
                rows.append({"关系": e["type"], "另一端": names.get(other, other),
                             "方向": "→" if e["src"] == sel else "←",
                             "依据摘录": ev.get("quote", ""), "出处": ev.get("article_id", "")})
            st.dataframe(rows, hide_index=True, use_container_width=True)
            st.graphviz_chart(_ego_dot(sel, names, rel))
        else:
            st.caption("还没有可追溯依据的关系。")
    with tab2:
        mine = [c for c in claims if sel in c.get("concepts", [])]
        st.dataframe([{"断言": c["text"], "原文摘录": c["quote"], "出处": c["article_id"]} for c in mine],
                     hide_index=True, use_container_width=True) if mine else st.caption("没有关联到该概念的断言。")
    with tab3:
        mine = [c for c in conflicts if c["concept"] == sel]
        if not mine:
            st.caption("没有发现冲突。")
        for c in mine:
            st.warning(c["reason"])
            for side in c["sides"]:
                st.markdown(f"- **{side['article_id']}**：{side['quote']}")


def _ego_dot(center: str, names: dict, rel: list) -> str:
    def q(s: str) -> str:
        return '"' + str(s).replace('"', "'") + '"'
    lines = ["digraph G {", "rankdir=LR;", "node [shape=box, style=rounded];",
             f"{q(center)} [style=filled, fillcolor=lightyellow];"]
    for e in rel:
        other = e["dst"] if e["src"] == center else e["src"]
        lines.append(f"{q(other)} [label={q(names.get(other, other))}];")
        lines.append(f"{q(e['src'])} -> {q(e['dst'])} [label={q(e['type'])}];")
    lines.append("}")
    return "\n".join(lines)
