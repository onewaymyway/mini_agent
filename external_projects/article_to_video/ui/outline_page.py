"""ui/outline_page.py — 「🗺️ 大纲」（C0）：生成、编辑章标题与钩子、拆分/合并/恢复取舍、确认。"""

from __future__ import annotations

import streamlit as st
from ui import jobs
from ui.joblog_panel import is_busy, render_job_panel, render_page_status

from article_to_video.paths import projects_root
from article_to_video.planning import outline_edit as oe
from article_to_video.planning.errors import PlanError
from article_to_video.planning.runner import (add_outline_quote, confirm_spine, dismiss_dropped, load_digests,
                                             load_spine, plan_outline, promote_dropped_quote, remove_outline_quote,
                                             save_spine, set_project_target)
from article_to_video.planning.chunks import section_catalog
from article_to_video.project_store import list_projects, read_json
from article_to_video.providers.text import make_text_llm, text_model_status
from article_to_video.validators.outline_report import outline_report
from ui.reset_panel import render_reset_panel

ENGINE_LABELS = {"miniagent": "模型（默认，mini_agent 的 LLM）", "rules": "离线规则（不调用模型，较粗略）"}


def _catalog(pdir):
    lines = (pdir / "source" / "article.md").read_text(encoding="utf-8").splitlines()
    return section_catalog(lines, read_json(pdir / "source" / "blocks.json"))


def _apply(pdir, fn):
    """读取最新大纲 → 应用一次编辑 → 保存。出错时在界面显示。"""
    spine = load_spine(pdir)
    try:
        save_spine(pdir, fn(spine))
        return True
    except (PlanError, ValueError) as exc:
        st.error(str(exc))
        return False


_STATUS_ICON = {"ok": "✅ 通过", "warn": "⚠️ 需注意", "error": "❌ 未通过"}


def _render_report(report) -> None:
    """校验分组：每类一列，状态、问题与处理建议。门禁以 validate_spine 为准（未通过则不能确认）。"""
    st.subheader("大纲校验")
    cols = st.columns(len(report["categories"]))
    for col, cat in zip(cols, report["categories"]):
        with col:
            st.markdown(f"**{cat['name']}**　{_STATUS_ICON[cat['status']]}")
            if not cat["items"]:
                st.caption("没有问题")
            for it in cat["items"]:
                (st.error if it["level"] == "error" else st.warning)(it["text"])
            st.caption(cat["hint"])
    if report["ok"]:
        st.caption("校验通过，可以确认大纲。")
    else:
        st.caption("存在未通过的问题，修正后才能确认大纲。")


def _render_chapter_cards(spine, titles_by_id) -> None:
    """章节卡片：标题、角色、一句话、覆盖的小节、预算占比与金句。"""
    st.subheader("章节")
    total = sum(float(p.get("budget_minutes") or 0) for p in spine["parts"]) or 1.0
    for p in spine["parts"]:
        with st.container(border=True):
            head, budget = st.columns([3, 2])
            head.markdown(f"**{p['id']} {p['title']}**　·　{p['role']}")
            minutes = float(p.get("budget_minutes") or 0)
            budget.markdown(f"预计 **{minutes:.1f} 分钟**（占 {minutes / total:.0%}）")
            st.progress(min(1.0, minutes / total))
            if p.get("one_line"):
                st.write(p["one_line"])
            sections = [titles_by_id.get(sid, sid) for sid in p["section_ids"]]
            st.caption(f"覆盖 {len(sections)} 节：" + "；".join(sections[:6])
                       + ("……" if len(sections) > 6 else ""))


def _part_labels(spine) -> dict:
    return {p["id"]: f"{p['id']} {p['title']}" for p in spine["parts"]}


def _render_quotes(pdir, spine) -> None:
    """金句：直接输入即可加入，指定所属章节；所属章节的口播必须原样使用这些金句。"""
    st.subheader("金句（写稿时原样使用）")
    labels = _part_labels(spine)
    quotes = spine.get("quotes") or []
    if not quotes:
        st.caption("还没有金句。可以直接输入，也可以从下方“模型给出但未在原文中找到的内容”中加入。")
    for i, q in enumerate(quotes):
        with st.container(border=True):
            st.write(f"“{q['text']}”")
            c1, c2 = st.columns([4, 1])
            c1.caption(f"所属：{labels.get(q['part_id'], q['part_id'])}　·　来源：{q.get('origin', '人工')}")
            if c2.button("删除", key=f"qdel_{i}"):
                try:
                    remove_outline_quote(pdir, i)
                    st.rerun()
                except PlanError as exc:
                    st.error(str(exc))
    with st.form("add_quote_form", clear_on_submit=True):
        text = st.text_input("金句文字", placeholder="直接输入，不需要先在原文中找到")
        part_id = st.selectbox("所属章节", list(labels), format_func=labels.get)
        submitted = st.form_submit_button("加入金句")
    if submitted:
        try:
            add_outline_quote(pdir, text, part_id)
            st.rerun()
        except PlanError as exc:
            st.error(str(exc))


def _render_dropped(pdir, digests, spine) -> None:
    """模型给出、但在原文中找不到的内容。金句可修改文字后加入大纲，或忽略；数字暂未接入口播，只能忽略。"""
    entries = [(d, i, item) for d in digests for i, item in enumerate(d.get("dropped") or [])]
    if not entries:
        return
    st.subheader("模型给出但未在原文中找到的内容")
    st.caption("金句可以修改文字后直接加入大纲（会标注“人工”来源，不要求出现在原文中）；"
               "数字目前没有接入口播，只能忽略。")
    labels = _part_labels(spine)
    ids = list(labels)
    for digest, idx, item in entries:
        gid = digest["group_id"]
        with st.container(border=True):
            if item.get("kind") == "number":
                st.write(f"**{gid} · 数字**：{item.get('text', '')}（数字暂未接入口播）")
                if st.button("忽略", key=f"dign_{gid}_{idx}"):
                    dismiss_dropped(pdir, gid, idx)
                    st.rerun()
                continue
            default = next((k for k, p in enumerate(spine["parts"])
                            if set(p["section_ids"]) & set(digest.get("section_ids") or [])), 0)
            text = st.text_input("金句文字（可修改）", value=item.get("text", ""), key=f"dtxt_{gid}_{idx}")
            part_id = st.selectbox("加入到章节", ids, index=default, format_func=labels.get, key=f"dpart_{gid}_{idx}")
            c1, c2 = st.columns(2)
            if c1.button("加入金句", key=f"dadd_{gid}_{idx}"):
                try:
                    promote_dropped_quote(pdir, gid, idx, text, part_id)
                    st.rerun()
                except PlanError as exc:
                    st.error(str(exc))
            if c2.button("忽略", key=f"dign_{gid}_{idx}"):
                dismiss_dropped(pdir, gid, idx)
                st.rerun()


def render_outline_page() -> None:
    st.header("🗺️ 大纲（C0）")
    root = projects_root()
    projects = [p for p in list_projects(root) if "error" not in p]
    if not projects:
        st.info("还没有项目。先到「📥 新建项目」导入一篇文章。")
        return
    pid = st.selectbox("项目", [p["id"] for p in projects],
                       format_func=lambda i: next(p["title"] for p in projects if p["id"] == i))
    pdir = root / pid
    try:
        _outline_body(pdir)
    finally:
        render_job_panel(pdir, ["outline"], title="大纲运行日志")


def _outline_body(pdir) -> None:
    render_page_status(pdir, ["outline"])

    spine = load_spine(pdir)
    confirmed = bool(spine and spine.get("confirmed"))

    with st.expander("生成大纲", expanded=spine is None):
        model_ok, model_why = text_model_status()
        from article_to_video import app_settings as S  # 默认值来自「⚙️ 设置」（R4）
        default_engine = "miniagent" if S.effective_group(pdir, "pipeline")["outline_engine"] == "llm" else "rules"
        engine = st.radio("生成方式", list(ENGINE_LABELS), index=list(ENGINE_LABELS).index(default_engine),
                          format_func=ENGINE_LABELS.get, horizontal=True)
        if engine == "miniagent" and not model_ok:
            st.warning(f"文本模型未就绪：{model_why}。请检查 mini_agent 的模型配置后重试；"
                       "也可以先改选「离线规则」生成一版。")
        overwrite = st.checkbox("覆盖已确认的大纲", value=False, disabled=not confirmed,
                                help="大纲已确认时，默认禁止覆盖，避免丢失人工编辑")
        reuse = st.checkbox("复用已有的分节摘要（断点续跑）", value=False,
                            help="默认关闭：每次“重新生成”都从原文重新调用模型。"
                                 "只有在上次生成中途失败、想接着做时才打开")
        if st.button("生成 / 重新生成", type="primary",
                     disabled=is_busy(pdir) or (engine == "miniagent" and not model_ok)):
            def run(engine=engine, overwrite=overwrite, reuse=reuse):
                llm = make_text_llm("miniagent", pdir=pdir) if engine == "miniagent" else None
                plan_outline(pdir, engine="llm" if engine == "miniagent" else "rules",
                             llm=llm, force=overwrite, reuse_digests=reuse)
            if jobs.start(pdir, f"生成大纲（{ENGINE_LABELS[engine]}）", run, scope="outline"):
                st.rerun()
            st.error("已有后台任务在运行，请稍候")

    def _regen_outline(p, ids, engine=engine):
        llm = make_text_llm("miniagent", pdir=p) if engine == "miniagent" else None
        plan_outline(p, engine="llm" if engine == "miniagent" else "rules", llm=llm, force=True, reuse_digests=False)

    render_reset_panel(pdir, stage="outline", key="rst_outline", busy=is_busy(pdir), regenerate=_regen_outline)

    if spine is None:
        st.info("还没有大纲。请先生成。")
        return

    catalog = _catalog(pdir)
    digests = load_digests(pdir)
    report = outline_report(spine, [c["id"] for c in catalog], digests)
    titles_by_id = {c["id"]: c["title"] for c in catalog}

    st.subheader("主线")
    st.write(f"**主旨**：{spine['thesis']}")
    st.write(f"**论证弧线**：{spine['arc']}")
    st.caption(f"生成方式：{ENGINE_LABELS.get('rules' if spine['engine'] == 'rules' else 'miniagent')}"
               f"　·　状态：{'已确认 ✅' if confirmed else '未确认'}")
    _render_report(report)
    _render_chapter_cards(spine, titles_by_id)

    with st.form("outline_edit_form"):
        target = st.slider("目标时长（分钟）", 3.0, 60.0, float(spine["target_minutes"]), 0.5,
                           help="与「⚙️ 设置」中的目标时长是同一个值，修改后两处都会更新")
        hooks = spine["hook_candidates"]
        hook = st.radio("开场钩子", list(range(len(hooks))), index=int(spine["selected_hook"]),
                        format_func=lambda i: hooks[i]) if hooks else None
        titles = [st.text_input(f"{p['id']} 章标题", value=p["title"], key=f"title_{p['id']}")
                  for p in spine["parts"]]
        saved = st.form_submit_button("保存修改（会取消确认）")
    if saved:
        if abs(target - float(spine["target_minutes"])) > 1e-9:
            set_project_target(pdir, target)   # 同步项目设置，重新生成时也使用这个值
        def edit(s):
            s = oe.set_target_minutes(s, target)
            for i, t in enumerate(titles):
                if t.strip() and t.strip() != s["parts"][i]["title"]:
                    s = oe.rename_part(s, i, t)
            if hook is not None:
                s = oe.select_hook(s, hook)
            return s
        if _apply(pdir, edit):
            st.rerun()

    st.subheader("章的拆分与合并")
    for i, p in enumerate(spine["parts"]):
        c1, c2, c3 = st.columns([4, 2, 2])
        c1.write(f"**{p['id']} {p['title']}**（{len(p['section_ids'])} 节）")
        if i < len(spine["parts"]) - 1 and c2.button("与下一章合并", key=f"merge_{p['id']}"):
            if _apply(pdir, lambda s, i=i: oe.merge_with_next(s, i)):
                st.rerun()
        if len(p["section_ids"]) > 1 and c3.button("从中间拆开", key=f"split_{p['id']}"):
            if _apply(pdir, lambda s, i=i, p=p: oe.split_part(s, i, len(p["section_ids"]) // 2)):
                st.rerun()

    if spine["cuts"]:
        st.subheader("取舍报告（被舍弃的小节）")
        for ci, cut in enumerate(spine["cuts"]):
            c1, c2, c3 = st.columns([4, 2, 2])
            c1.write(f"{', '.join(cut['section_ids'])}：{cut['reason'] or '（无理由）'}")
            target_part = c2.selectbox("恢复到", range(len(spine["parts"])), key=f"restore_to_{ci}",
                                       format_func=lambda k: spine["parts"][k]["id"])
            if c3.button("恢复", key=f"restore_{ci}"):
                if _apply(pdir, lambda s, ci=ci, k=target_part: oe.restore_cut(s, ci, k, catalog)):
                    st.rerun()

    _render_quotes(pdir, spine)
    _render_dropped(pdir, digests, spine)

    st.subheader("确认大纲（C0）")
    if st.button("确认大纲", type="primary", disabled=not report["ok"] or confirmed):
        try:
            confirm_spine(pdir)
            st.success("已确认。后续口播稿将基于此大纲。")
            st.rerun()
        except PlanError as exc:
            st.error(str(exc))


