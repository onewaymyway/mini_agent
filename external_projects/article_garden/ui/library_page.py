"""ui/library_page.py — 📚 文章库（P1）：导入（预览→确认）、文章列表与摘要状态、单篇详情、章程草稿。

耗时操作（摘要、章程草稿）以子进程后台运行，页面立即返回；进度与错误见「▶ 运行日志」。
页面只调用 article_garden 的函数，不在页面里写业务逻辑。
"""

from __future__ import annotations

import streamlit as st
import yaml

from article_garden import joblog
from article_garden.digest import charter_draft, runner
from article_garden.library import catalog, importer
from ui import state
from ui.jobs import spawn as _spawn
from ui.topbar import render_topbar


def render_library_page() -> None:
    ctx = state.current_ctx()
    if ctx is None:
        return
    render_topbar(ctx, "📚 文章库")
    _import_section(ctx)
    st.divider()
    _articles_section(ctx)
    st.divider()
    _charter_section(ctx)


def _import_section(ctx) -> None:
    has_articles = bool(catalog.list_articles(ctx))
    with st.expander("➕ 导入文章", expanded=not has_articles):
        files = st.file_uploader("选择 Markdown / 文本 / zip 文件（可多选）",
                                 type=["md", "markdown", "txt", "zip"], accept_multiple_files=True,
                                 key="imp_files")
        local = st.text_input("或填写本机路径（文件、目录或 zip，多个用英文分号 ; 分隔）", key="imp_paths")
        if st.button("预览导入", key="imp_preview"):
            cands = []
            if files:
                cands += importer.collect([(f.name, f.getvalue()) for f in files])
            if local.strip():
                cands += importer.collect_paths([p.strip() for p in local.split(";") if p.strip()])
            if not cands:
                st.warning("请先选择文件或填写路径")
                st.session_state.pop("imp_rows", None)
            else:
                st.session_state["imp_cands"] = cands
                st.session_state["imp_rows"] = importer.preview(ctx, cands)
        rows = st.session_state.get("imp_rows")
        if rows:
            st.dataframe(rows, hide_index=True, use_container_width=True)
            new = sum(1 for r in rows if r["action"] == "new")
            st.caption(f"新增 {new} 篇；重复与跳过的不会导入。源文件只读，不会被修改。")
            if st.button(f"确认导入 {new} 篇", disabled=new == 0, type="primary", key="imp_apply"):
                res = importer.apply(ctx, st.session_state.pop("imp_cands"))
                st.session_state.pop("imp_rows", None)
                st.success(f"已导入 {len(res['imported'])} 篇：{'、'.join(res['imported'])}")


def _articles_section(ctx) -> None:
    st.subheader("文章")
    rows = catalog.list_articles(ctx)
    if not rows:
        st.info("库里还没有文章。在上方导入 Markdown 或 zip；导入后即可摘要。")
        return
    statuses = {r["id"]: runner.status_of(ctx, r) for r in rows}
    table = [{"编号": r["id"], "标题": r["title"], "来源": "导入" if r["origin"] == "imported" else "生成",
              "状态": r.get("status", ""), "字数": r.get("chars", 0), "摘要": statuses[r["id"]]} for r in rows]
    st.dataframe(table, hide_index=True, use_container_width=True)

    busy = joblog.is_running(ctx.root, scopes=("digest",))
    pending = sum(1 for s in statuses.values() if s in ("未摘要", "待更新", "失败"))
    if st.button(f"开始摘要（{pending} 篇待处理，增量）", disabled=busy or pending == 0, type="primary",
                 key="digest_all"):
        _spawn("digest.py", ctx.id)
        st.success("已在后台开始摘要，进度见「▶ 运行日志」。")
    if busy:
        st.caption("已有摘要任务在运行，请到「▶ 运行日志」查看。")

    labels = {r["id"]: f"{r['id']}  {r['title']}" for r in rows}
    sel = st.selectbox("查看文章", [r["id"] for r in rows], format_func=lambda i: labels[i], key="lib_sel")
    row = catalog.get(ctx, sel)
    _article_detail(ctx, row)


def _article_detail(ctx, row) -> None:
    body = catalog.read_body(ctx, row)
    with st.expander("正文预览", expanded=False):
        st.markdown(body[:4000] + ("\n\n…（预览已截断）" if len(body) > 4000 else ""))
    rec = runner.load_digest(ctx, row["id"])
    if rec and rec.get("status") == "ok":
        st.markdown(f"**论点**：{rec.get('thesis', '')}")
        st.caption(f"类型 {rec.get('kind')} ｜ 层级 {rec.get('level')} ｜ 自评 {rec.get('self_score')}/5 ｜ "
                   f"分块 {rec.get('chunks', 1)}")
        if rec.get("concepts"):
            st.markdown("**概念**：" + "、".join(rec["concepts"]))
        if rec.get("claims"):
            st.dataframe([{"断言": c["text"], "原文摘录": c["quote"]} for c in rec["claims"]],
                         hide_index=True, use_container_width=True)
        if rec.get("dangling"):
            st.markdown("**提到但未讲清的概念**：" + "、".join(rec["dangling"]))
    elif rec:
        st.error("摘要失败：" + "；".join(rec.get("issues") or ["未知原因"]))
        if rec.get("last_output"):
            with st.expander("最后一次模型输出（供排查）"):
                st.code(rec["last_output"], language="yaml")
    else:
        st.info("这篇还没有摘要。")
    busy = joblog.is_running(ctx.root, scopes=("digest",))
    if st.button("重新摘要这篇", disabled=busy, key=f"redo_{row['id']}"):
        _spawn("digest.py", ctx.id, "--article-id", row["id"], "--force")
        st.success("已在后台开始重新摘要。")


def _charter_section(ctx) -> None:
    st.subheader("章程草稿（由摘要反推）")
    st.caption("草稿只是建议。你确认后才会采用为正式章程；采用前旧章程会自动备份。")
    busy = joblog.is_running(ctx.root, scopes=("digest", "charter"))
    if st.button("由已摘要文章生成章程草稿", disabled=busy, key="draft_charter"):
        _spawn("draft_charter.py", ctx.id)
        st.success("已在后台生成草稿，完成后刷新本页查看。")
    d = charter_draft.load_draft(ctx)
    if not d:
        st.info("还没有章程草稿。摘要完成后可生成。")
        return
    st.code(yaml.safe_dump(d, allow_unicode=True, sort_keys=False), language="yaml")
    if st.button("采用为正式章程", key="adopt_charter"):
        warnings = charter_draft.adopt(ctx)
        st.success("已采用为正式章程（旧章程已备份为 charter.yaml.bak）。")
        for w in warnings:
            st.warning(w)
