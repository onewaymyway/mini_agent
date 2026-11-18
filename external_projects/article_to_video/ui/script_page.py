"""ui/script_page.py — 「📝 口播稿」（C1）：词典、后台写稿（可停止）、逐段编辑、确认。"""

from __future__ import annotations

import streamlit as st

from article_to_video.paths import projects_root
from article_to_video.planning.errors import PlanError
from article_to_video.planning.runner import load_spine
from article_to_video.project_store import list_projects
from article_to_video.providers.text import make_text_llm
from article_to_video.script import writer as w
from article_to_video.script.budget import chars_for_minutes
from article_to_video.script.lexicon import load_lexicon, parse_lexicon_text, save_lexicon
from ui import jobs


def _editor_text(beat) -> str:
    return "\n".join(("> " if s.get("quote") else "") + s["text"] for s in beat["sentences"])


def _parse_editor(text: str):
    sentences = []
    for line in text.splitlines():
        t = line.strip()
        if not t:
            continue
        quote = t.startswith(">")
        t = t.lstrip(">").strip()
        if t:
            sentences.append({"text": t, "quote": quote, "emphasis": []})
    return sentences


def render_script_page() -> None:
    st.header("📝 口播稿（C1）")
    root = projects_root()
    projects = [p for p in list_projects(root) if "error" not in p]
    if not projects:
        st.info("还没有项目。先到「📥 新建项目」导入文章。")
        return
    pid = st.selectbox("项目", [p["id"] for p in projects],
                       format_func=lambda i: next(p["title"] for p in projects if p["id"] == i))
    pdir = root / pid
    spine = load_spine(pdir)
    if spine is None or not spine.get("confirmed"):
        st.info("请先在「🗺️ 大纲」生成并确认大纲（C0）。")
        return

    st.subheader("后台写稿")
    job = jobs.status(pdir)
    if job:
        st.write(f"任务：{job['label']} — **{job['state']}**：{job['message']}")
        if job["state"] == "running":
            c1, c2 = st.columns(2)
            if c1.button("⏹ 请求停止"):
                jobs.stop(pdir)
                st.rerun()
            if c2.button("🔄 刷新状态"):
                st.rerun()

    part_ids = [p["id"] for p in spine["parts"]]
    c1, c2, c3 = st.columns([3, 2, 2])
    force = c1.checkbox("重新写（覆盖已有稿）", value=False,
                        help="默认只写缺少稿件或输入已变化的章（断点续跑）")
    running = bool(job and job["state"] == "running")
    if c2.button("写全部章", disabled=running, type="primary"):
        jobs.start(pdir, "写全部章", lambda: w.write_all(pdir, make_text_llm("miniagent"), force=force))
        st.rerun()
    part = c3.selectbox("章", part_ids,
                        format_func=lambda i: next(p["title"] for p in spine["parts"] if p["id"] == i))
    if st.button("写本章", disabled=running):
        jobs.start(pdir, f"写 {part}", lambda: w.write_part(pdir, part, make_text_llm("miniagent"), force=force))
        st.rerun()

    with st.expander("发音词典（只影响配音，字幕显示原写法）"):
        cur = load_lexicon(pdir)
        text = st.text_area("每行一条：原词=读法", value="\n".join(f"{k}={v}" for k, v in cur.items()), height=120)
        if st.button("保存词典"):
            save_lexicon(pdir, parse_lexicon_text(text))
            st.success("已保存。已写稿的章需重写才会应用新读法。")

    script = w.load_script(pdir, part)
    if script is None:
        st.info("本章还没有口播稿。")
        return

    total = script.get("total_chars", 0)
    budget = script["budget_chars"]
    st.caption(f"{script['title']}　·　字数 {total} / 预算 {budget}（{total / max(budget, 1) - 1:+.0%}）"
               f"　·　{'已确认 ✅' if script.get('confirmed') else '未确认'}")
    for msg in script.get("warnings", []):
        st.warning(msg)

    with st.form(f"script_form_{part}"):
        texts = {}
        for b in script["beats"]:
            texts[b["id"]] = st.text_area(
                f"{b['id']}（{b['role']}，来源 {', '.join(b['source_refs'])}）",
                value=_editor_text(b), height=max(90, 28 * (len(b["sentences"]) + 1)),
                key=f"beat_{part}_{b['id']}")
        saved = st.form_submit_button("保存本章")
    st.caption("每行一句；行首加 `>` 表示原文金句（必须是原文逐字片段）。")
    if saved:
        edited = {**script, "beats": [
            {**b, "sentences": _parse_editor(texts[b["id"]])} for b in script["beats"]]}
        try:
            w.save_script(pdir, part, edited)
            st.success("已保存（本章变为未确认）")
            st.rerun()
        except PlanError as exc:
            st.error(str(exc))

    if st.button("确认本章（C1）", disabled=bool(script.get("confirmed"))):
        try:
            w.confirm_script(pdir, part)
            st.rerun()
        except PlanError as exc:
            st.error(str(exc))
