"""ui/outline_page.py — 「🗺️ 大纲」（C0）：生成、编辑章标题与钩子、拆分/合并/恢复取舍、确认。"""

from __future__ import annotations

import streamlit as st
from ui import jobs
from ui.joblog_panel import is_busy, render_job_panel, render_page_status

from article_to_video.paths import projects_root
from article_to_video.planning import outline_edit as oe
from article_to_video.planning.errors import PlanError
from article_to_video.planning.runner import confirm_spine, load_digests, load_spine, plan_outline, save_spine
from article_to_video.planning.chunks import section_catalog
from article_to_video.project_store import list_projects, read_json
from article_to_video.providers.text import make_text_llm, text_model_status
from article_to_video.validators.coverage import validate_spine

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
        if st.button("生成 / 重新生成", type="primary",
                     disabled=is_busy(pdir) or (engine == "miniagent" and not model_ok)):
            def run(engine=engine, overwrite=overwrite):
                llm = make_text_llm("miniagent") if engine == "miniagent" else None
                plan_outline(pdir, engine="llm" if engine == "miniagent" else "rules",
                             llm=llm, force=overwrite)
            if jobs.start(pdir, f"生成大纲（{ENGINE_LABELS[engine]}）", run, scope="outline"):
                st.rerun()
            st.error("已有后台任务在运行，请稍候")

    if spine is None:
        st.info("还没有大纲。请先生成。")
        return

    catalog = _catalog(pdir)
    digests = load_digests(pdir)
    check = validate_spine(spine, [c["id"] for c in catalog], digests)

    st.subheader("主线")
    st.write(f"**主旨**：{spine['thesis']}")
    st.write(f"**论证弧线**：{spine['arc']}")
    st.caption(f"生成方式：{ENGINE_LABELS.get('rules' if spine['engine'] == 'rules' else 'miniagent')}"
               f"　·　状态：{'已确认 ✅' if confirmed else '未确认'}")
    for w in check["warnings"]:
        st.warning(w)
    for e in check["errors"]:
        st.error(e)

    st.subheader("章节与时长")
    st.dataframe([{
        "章": p["id"], "标题": p["title"], "角色": p["role"], "小节数": len(p["section_ids"]),
        "预计分钟": p["budget_minutes"], "一句话": p["one_line"],
    } for p in spine["parts"]], use_container_width=True, hide_index=True)

    with st.form("outline_edit_form"):
        target = st.slider("目标时长（分钟）", 3, 60, int(round(spine["target_minutes"])))
        hooks = spine["hook_candidates"]
        hook = st.radio("开场钩子", list(range(len(hooks))), index=int(spine["selected_hook"]),
                        format_func=lambda i: hooks[i]) if hooks else None
        titles = [st.text_input(f"{p['id']} 章标题", value=p["title"], key=f"title_{p['id']}")
                  for p in spine["parts"]]
        saved = st.form_submit_button("保存修改（会取消确认）")
    if saved:
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

    st.subheader("确认大纲（C0）")
    if st.button("确认大纲", type="primary", disabled=bool(check["errors"]) or confirmed):
        try:
            confirm_spine(pdir)
            st.success("已确认。后续口播稿将基于此大纲。")
            st.rerun()
        except PlanError as exc:
            st.error(str(exc))


