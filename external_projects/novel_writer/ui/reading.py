"""📖 阅读：章节目录、正文预览，以及导出全书合稿（M3）。"""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from novel_writer import reading_state, tool_api
from ui.components import show_error, show_df
from ui.listen import render_listen

EXPORT_LABELS = {"md": "Markdown", "txt": "纯文本 (txt)", "docx": "Word (docx)", "epub": "电子书 (epub)"}


def render_reading(ws: str) -> None:
    """阅读页入口：有完稿修改版本时，顶部可选择工作稿或某个版本；选版本时只读该版本并可导出。"""
    options = _version_options(ws)
    if options:
        labels = dict(options)
        pick = st.selectbox("查看版本", [vid for vid, _ in options],
                            format_func=lambda v: labels[v], key="rd_version")
        if pick is not None:
            _render_version(ws, pick)
            return
    _render_working(ws)


def _version_options(ws: str):
    from novel_writer.revision_flow import versions as V
    from novel_writer.workspace import Workspace as _W
    root = _W(ws)
    if not V.is_baseline_ready(root):
        return []
    opts = [(None, "工作稿（当前编辑中，原流程）")]
    for v in V.list_versions(root):
        note = v.get("note") or ""
        kind = "完稿只读" if v["kind"] == "baseline" else "修改版本"
        opts.append((v["id"], f"{v['id']} · {kind} · {note}".rstrip(" ·")))
    return opts


def _version_chapters(vroot) -> list:
    """版本目录没有工作目录的初始化标记，所以直接用 Workspace 读章节（与 tool_api 的字段保持一致）。"""
    from novel_writer.textio import read_text_lenient
    from novel_writer.workspace import Workspace as _W, count_words
    rows = []
    for ch in _W(vroot).list_chapters():
        text, _ = read_text_lenient(ch.path)
        rows.append({"chapter": ch.number, "title": ch.title, "words": count_words(text),
                     "file": ch.path.name, "text": text})
    return rows


def _render_version(ws: str, vid: str) -> None:
    from novel_writer.revision_flow import derived
    from novel_writer.revision_flow.common import version_dir
    from novel_writer.workspace import Workspace as _W
    root = _W(ws)
    vroot = version_dir(root, vid)
    st.caption(f"当前查看：{vid}（版本内容只读，导出结果统一放在 _versions/exports/{vid}/）")
    rows = _version_chapters(vroot)
    if not rows:
        st.info("该版本没有章节。")
        return
    total = sum(r["words"] for r in rows)
    st.caption(f"共 {len(rows)} 章 · {total} 字")
    show_df([{"章": r["chapter"], "标题": r["title"], "字数": r["words"], "文件": r["file"]} for r in rows],
            width="stretch", hide_index=True)

    c_fmt, c_btn = st.columns([1, 2])
    fmt = c_fmt.selectbox("导出格式", list(EXPORT_LABELS), format_func=lambda f: EXPORT_LABELS[f],
                          key=f"rd_v_fmt_{vid}")
    if c_btn.button(f"导出 {vid} 全书合稿", key=f"rd_v_export_{vid}", type="primary"):
        out = _version_action(ws, f"导出 {vid} 全书合稿（{fmt}）", derived.export_book, root, vid, fmt)
        if out:
            st.success(f"已导出 {out['chapters']} 章 · {out['words']} 字 → {out['file']}")
    if st.button(f"导出 {vid} 最终稿（设定 + 章节总结 + 清洗后正文 + txt）", key=f"rd_v_final_{vid}"):
        out = _version_action(ws, f"导出 {vid} 最终稿", derived.export_final, root, vid)
        if out:
            st.success(f"最终稿已导出：{out['chapters']} 章 · {out['words']} 字 · 设定 {out['settings']} 个 → {out['dir']}")
            for w in out["warnings"]:
                st.warning(w)
            for e in out["errors"]:
                st.error(e)

    files = derived.list_exports(root, vid)
    st.markdown(f"**{vid} 已导出的文件**")
    if not files:
        st.caption("还没有导出过。")
    for f in files:
        p = Path(f["abs"])
        st.download_button(f"下载 {f['path']}（{max(1, f['size'] // 1024)} KB）", data=p.read_bytes(),
                           file_name=p.name, key=f"rd_v_dl_{vid}_{f['path']}")

    numbers = [r["chapter"] for r in rows]
    chosen = st.selectbox("预览章节", numbers,
                          format_func=lambda n: f"第{n}章 {next(r['title'] for r in rows if r['chapter'] == n)}",
                          key=f"rd_v_pick_{vid}")
    text = next(r["text"] for r in rows if r["chapter"] == chosen)
    st.markdown(text.strip() or "（尚无正文）")


def _version_action(ws: str, label: str, fn, *args):
    """版本视图里的操作：开始/完成/失败都写入 _versions 的操作日志（与完稿修改页共用 ops 日志）。"""
    from novel_writer.revision_flow import jobs
    from novel_writer.revision_flow.common import FlowError
    from novel_writer.workspace import Workspace as _W
    root = _W(ws)
    jobs.log(root, "ops", f"▶ {label}：开始")
    try:
        out = fn(*args)
    except FlowError as exc:
        jobs.log(root, "ops", f"✘ {label}：失败：{exc}")
        st.error(str(exc))
        return None
    except Exception as exc:
        jobs.log(root, "ops", f"✘ {label}：异常：{exc}")
        st.error(f"操作失败：{exc}")
        return None
    jobs.log(root, "ops", f"✔ {label}：完成 → {out.get('file') or out.get('dir')}")
    return out


def _render_working(ws: str) -> None:
    res = tool_api.chapters(ws)
    if not res["ok"]:
        show_error(res)
        return
    rows = res["data"]["chapters"]
    if not rows:
        st.info("还没有章节。")
        return
    render_listen(ws, rows)
    total = sum(r["words"] for r in rows)
    st.caption(f"共 {len(rows)} 章 · {total} 字")
    show_df([{"章": r["chapter"], "标题": r["title"], "字数": r["words"], "文件": r["file"] or "—"} for r in rows],
                 width="stretch", hide_index=True)
    c_fmt, c_btn = st.columns([1, 2])
    fmt = c_fmt.selectbox("导出格式", list(EXPORT_LABELS), format_func=lambda f: EXPORT_LABELS[f], key="rd_fmt")
    if c_btn.button("导出全书合稿", key="rd_export", type="primary"):
        r = tool_api.export(ws, fmt)
        if r["ok"]:
            st.session_state["rd_last_export"] = r["data"]
            st.success(f"已导出 {r['data']['chapters']} 章 · {r['data']['words']} 字 → {r['data']['file']}")
        else:
            show_error(r)
    if st.button("导出最终稿（设定 + 章节总结 + 清洗后正文 + txt）", key="rd_final_export"):
        r = tool_api.export_final(ws)
        if r["ok"]:
            d = r["data"]
            st.session_state["rd_final_result"] = d
            st.success(f"最终稿已导出：{d['chapters']} 章 · {d['words']} 字 · 设定 {d['settings']} 个 → {d['dir']}")
            for w in d["warnings"]:
                st.warning(w)
            for e in d["errors"]:
                st.error(e)
        else:
            show_error(r)
    last = st.session_state.get("rd_last_export")
    if last and (ws_path := Path(ws) / last["file"]).is_file():
        st.download_button(f"下载 {last['file'].rsplit('.', 1)[-1]} 文件", data=ws_path.read_bytes(),
                           file_name=ws_path.name, key="rd_download")
    numbers = [r["chapter"] for r in rows]
    if "rd_pick" not in st.session_state:
        # 新会话（刷新/重启后）恢复上次阅读的章节；同一会话内以用户选择为准。
        saved = reading_state.chapter_or_none(reading_state.load(ws).get("last_read_chapter"), numbers)
        st.session_state["rd_pick"] = saved if saved is not None else numbers[0]
    chosen = st.selectbox("预览章节", numbers,
                          format_func=lambda n: f"第{n}章 {next(r['title'] for r in rows if r['chapter'] == n)}",
                          key="rd_pick")
    if reading_state.load(ws).get("last_read_chapter") != chosen:
        reading_state.update(ws, last_read_chapter=chosen)
    detail = tool_api.chapter(ws, chosen)
    if detail["ok"]:
        st.markdown(detail["data"]["text"] or "（尚无正文）")
