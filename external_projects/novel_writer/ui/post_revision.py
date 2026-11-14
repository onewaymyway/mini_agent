"""📝 完稿修改：完稿后的版本化修改（独立于写作流水线）。

五个分页：版本 / 变更与预览 / 运行 / 章节反馈 / 设置。
  * 执行（会调用 LLM）放在后台线程中进行，页面通过运行记录显示进度；可停止、可续跑。
  * 所有业务错误通过 FlowError 提示给作者，不抛到页面上。
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Callable, Optional

import streamlit as st

from novel_writer.revision_flow import changes as CH
from novel_writer.revision_flow import config as CFG
from novel_writer.revision_flow import derived, executor, feedback
from novel_writer.revision_flow import versions as V
from novel_writer.revision_flow.common import FlowError, sub_dir
from novel_writer.workspace import Workspace
from ui.components import require_ws

_STATUS_LABEL = {
    "running": "运行中", "paused_limit": "已达上限（可续跑）", "stopped": "已停止（可续跑）",
    "done": "已完成", "failed": "失败", "draft": "草稿", "confirmed": "已确认", "applied": "已应用",
}


def _invoker():
    from novel_writer.runner import FrameworkInvoker
    return FrameworkInvoker()


def _try(fn: Callable[..., Any], *args, **kwargs) -> Optional[Any]:
    try:
        return fn(*args, **kwargs)
    except FlowError as exc:
        st.error(str(exc))
    except Exception as exc:  # 意外错误也要给出可读提示，不让页面崩溃
        st.error(f"操作失败：{exc}")
    return None


def _background(label: str, target: Callable[[], None]) -> None:
    def runner():
        try:
            target()
        except Exception as exc:  # 后台异常记入会话，页面下次刷新时显示
            st.session_state.setdefault("rv_bg_errors", []).append(f"{label}：{exc}")
    threading.Thread(target=runner, daemon=True).start()
    st.info(f"已在后台开始：{label}。可以切换到其它页面，稍后回来刷新查看进度。")


def _show_bg_errors() -> None:
    for msg in st.session_state.pop("rv_bg_errors", []):
        st.error(msg)


def _status(s: str) -> str:
    return _STATUS_LABEL.get(s, s)


# ─────────────────────────────── 版本 ───────────────────────────────

def _tab_versions(ws: str) -> None:
    root = Workspace(ws)
    if not V.is_baseline_ready(root):
        st.info("尚未创建完稿基线。全书终审通过后，在这里生成只读的 v001。")
        if st.button("创建完稿基线 v001", key="rv_baseline"):
            if _try(V.init_baseline, root):
                st.success("已创建 v001（只读）")
                st.rerun()
        return
    index = V.load_index(root)
    st.write(f"当前 head：**{index['head']}**")
    st.dataframe([{"版本": v["id"], "父版本": v["parent"] or "—", "类型": v["kind"],
                   "说明": v["note"], "创建时间": v["created_at"]} for v in index["versions"]],
                 hide_index=True, use_container_width=True)
    ids = [v["id"] for v in index["versions"]]
    c1, c2 = st.columns(2)
    with c1:
        pick = st.selectbox("校验版本", ids, key="rv_verify_pick")
        if st.button("校验该版本", key="rv_verify"):
            problems = _try(V.verify, root, pick)
            if problems is not None:
                if problems:
                    st.error("；".join(problems[:5]))
                else:
                    st.success("校验通过")
    with c2:
        if st.button("撤销最近一次修改（丢弃 head）", key="rv_undo", disabled=index["head"] in (None, "v001")):
            if _try(V.undo_head, root) is not None:
                st.success("已撤销，head 回到父版本")
                st.rerun()
    st.markdown("**版本差异**")
    d1, d2 = st.columns(2)
    a = d1.selectbox("旧版本", ids, index=0, key="rv_diff_a")
    b = d2.selectbox("新版本", ids, index=len(ids) - 1, key="rv_diff_b")
    if st.button("查看差异", key="rv_diff"):
        rows = _try(derived.diff_versions, root, a, b)
        if rows is not None:
            changed = [r for r in rows if r["status"] != "same"]
            st.dataframe(changed or [{"结果": "两个版本完全相同"}], hide_index=True, use_container_width=True)
    st.markdown("**导出**")
    ex = st.selectbox("导出版本", ids, key="rv_export_pick")
    if st.button("导出该版本", key="rv_export"):
        result = _try(derived.export_version, root, ex)
        if result is not None:
            st.success("已导出")
            st.json(result, expanded=False)


# ─────────────────────────────── 变更与预览 ───────────────────────────────

def _tab_changes(ws: str) -> None:
    root = Workspace(ws)
    st.markdown("**新建设定变更**")
    with st.form("rv_change_form", clear_on_submit=False):
        title = st.text_input("标题", key="rv_c_title")
        before = st.text_input("旧设定", key="rv_c_before")
        after = st.text_input("新设定", key="rv_c_after")
        reason = st.text_input("理由", key="rv_c_reason")
        terms = st.text_input("定位词（逗号分隔）", key="rv_c_terms")
        after_terms = st.text_input("新设定关键词（逗号分隔，用于 from_chapter 警告）", key="rv_c_after_terms")
        mode = st.radio("时间语义", list(CH.MODES), horizontal=True, key="rv_c_mode",
                        format_func=lambda m: "追溯式（全书一直如此）" if m == "retcon" else "从第 N 章起生效")
        eff = st.number_input("生效章号（from_chapter 时）", min_value=1, value=1, step=1, key="rv_c_eff")
        submitted = st.form_submit_button("保存变更")
    if submitted:
        raw = {"kind": "setting", "title": title, "before": before, "after": after, "reason": reason,
               "terms": _split(terms), "after_terms": _split(after_terms), "mode": mode,
               "effective_from": int(eff) if mode == "from_chapter" else None}
        created = _try(CH.save_change, root, raw)
        if created:
            st.success(f"已保存变更 {created['id']}")

    changes = CH.list_changes(root)
    if changes:
        st.dataframe([{"ID": c["id"], "类型": c["kind"], "标题": c["title"], "状态": _status(c["status"]),
                       "语义": c.get("mode") or "—"} for c in changes], hide_index=True, use_container_width=True)
    st.divider()
    st.markdown("**生成预览**")
    versions = [v["id"] for v in V.list_versions(root)] if V.is_baseline_ready(root) else []
    if not versions:
        st.info("请先创建完稿基线。")
        return
    base = st.selectbox("基础版本", versions, index=len(versions) - 1, key="rv_p_base")
    pick = st.multiselect("变更", [c["id"] for c in changes if c["status"] in ("draft", "confirmed")],
                          key="rv_p_changes")
    use_llm = st.checkbox("做 LLM 语义判定（会调用模型，判定不涉及的章节默认排除）", value=True, key="rv_p_review")
    if st.button("生成预览", key="rv_p_make", disabled=not pick):
        pv = _try(CH.build_preview, root, base, pick, invoker=_invoker() if use_llm else None)
        if pv:
            st.session_state["rv_preview"] = pv["id"]
    pid = st.session_state.get("rv_preview")
    if not pid:
        return
    pv = CH.load_preview(root, pid)
    st.write(f"预览 **{pid}**　基础版本 {pv['base_version']}　预计调用 {pv['estimated_llm_calls']} 次　"
             f"需要 {pv['runs_needed']} 次运行")
    for w in pv["warnings"]:
        st.warning(w)
    if pv["confirmed"]:
        st.success("预览已确认，可以到「运行」页执行。")
    st.caption("取消勾选即排除该章（确认前有效）")
    for row in pv["chapters"]:
        reason_txt = row["review"]["reason"] if row.get("review") else "未判定"
        keep = st.checkbox(f"第{row['number']}章 {row['title']}　命中：{'、'.join(row['hits']) or '—'}　判定：{reason_txt}",
                           value=not row["excluded"], key=f"rv_keep_{pid}_{row['number']}",
                           disabled=pv["confirmed"])
        if keep == row["excluded"] and not pv["confirmed"]:
            _try(CH.set_excluded, root, pid, row["number"], not keep)
            st.rerun()
    if not pv["confirmed"] and st.button("确认预览", key="rv_p_confirm"):
        if _try(CH.confirm_preview, root, pid):
            st.success("已确认")
            st.rerun()


def _split(text: str):
    return [t.strip() for t in str(text or "").replace("，", ",").split(",") if t.strip()]


# ─────────────────────────────── 运行 ───────────────────────────────

def _tab_runs(ws: str) -> None:
    root = Workspace(ws)
    _show_bg_errors()
    previews = []
    for p in sorted(sub_dir(root, "preview").glob("prv_*.json")):
        d = json.loads(p.read_text(encoding="utf-8"))
        if d.get("confirmed"):
            previews.append(d["id"])
    if previews:
        st.markdown("**开始运行**")
        pid = st.selectbox("已确认的预览", previews, key="rv_r_pick")
        if st.button("在后台开始改写", key="rv_r_start"):
            if _try(executor.precheck_start, root, pid):
                _background(f"改写（{pid}）", lambda: executor.start(root, pid, _invoker()))
    else:
        st.info("还没有已确认的预览。")
    runs = executor.list_runs(root)
    if not runs:
        return
    st.divider()
    st.dataframe([{"运行": r["id"], "状态": _status(r["status"]), "目标版本": r["target_version"],
                   "已完成章": ", ".join(str(n) for n in r["done"]) or "—",
                   "LLM 调用": r["llm_calls"], "更新时间": r.get("updated_at", "")} for r in runs],
                 hide_index=True, use_container_width=True)
    rid = st.selectbox("选择运行", [r["id"] for r in runs], key="rv_r_sel")
    run = executor.load_run(root, rid)
    c1, c2 = st.columns(2)
    if run["status"] == "running" and c1.button("请求停止", key="rv_r_stop"):
        if _try(executor.request_stop, root, rid):
            st.info("已请求停止，当前章节结束后生效。")
    if run["status"] in ("paused_limit", "stopped") and c2.button("续跑", key="rv_r_resume"):
        if _try(executor.precheck_resume, root, rid):
            _background(f"续跑（{rid}）", lambda: executor.resume(root, rid, _invoker()))
    if run["status"] == "failed" and run.get("failed"):
        st.error(f"第{run['failed']['chapter']}章失败：{'；'.join(run['failed']['errors'])}")
    if run.get("derived_marks"):
        st.markdown("**建议重新检查的派生物**（不会自动运行）")
        st.dataframe(run["derived_marks"], hide_index=True, use_container_width=True)
    with st.expander("运行日志"):
        st.code("\n".join(run.get("log", [])[-80:]) or "（暂无）")


# ─────────────────────────────── 章节反馈 ───────────────────────────────

def _tab_feedback(ws: str) -> None:
    root = Workspace(ws)
    if not V.is_baseline_ready(root):
        st.info("请先创建完稿基线。")
        return
    versions = [v["id"] for v in V.list_versions(root)]
    base = st.selectbox("基于版本", versions, index=len(versions) - 1, key="rv_f_base")
    chapter = st.number_input("章号", min_value=1, value=1, step=1, key="rv_f_ch")
    fb = st.text_area("对本章的意见", key="rv_f_text")
    sel = st.text_area("选中的段落（可选）", key="rv_f_sel")
    if st.button("分诊（会调用模型）", key="rv_f_triage", disabled=not fb.strip()):
        with st.spinner("分诊中…"):
            rec = _try(feedback.triage, root, base, int(chapter), fb, _invoker(), selection=sel)
        if rec:
            st.session_state["rv_triage"] = rec["id"]
    tid = st.session_state.get("rv_triage")
    if not tid:
        return
    rec = feedback.load_triage(root, tid)
    if rec["status"] == "failed":
        st.error("分诊失败：" + "；".join(rec["errors"]))
        return
    if rec["status"] != "pending":
        st.info(f"分诊记录状态：{rec['status']}")
        return
    res = rec["result"]
    st.write(f"分类：**{'章内修改' if res['kind'] == 'chapter' else '设定变更'}**　理由：{res['reason']}")
    if res["kind"] == "chapter":
        st.write(f"修改意见：{res['instruction']}")
        mode, eff = None, None
    else:
        ch = res["change"]
        st.write(f"旧设定：{ch.get('before')}　→　新设定：{ch.get('after')}　定位词：{'、'.join(ch.get('terms', []))}")
        mode = st.radio("时间语义（由作者决定）", list(CH.MODES), horizontal=True, key="rv_f_mode")
        eff = st.number_input("生效章号", min_value=1, value=int(rec["chapter"]), step=1, key="rv_f_eff") \
            if mode == "from_chapter" else None
    c1, c2 = st.columns(2)
    if c1.button("采纳为变更", key="rv_f_accept"):
        if res["kind"] == "setting" and not mode:
            st.error("请选择时间语义")
        elif _try(feedback.accept, root, tid, mode=mode, effective_from=int(eff) if eff else None):
            st.success("已生成变更，请到「变更与预览」页生成预览")
            st.session_state.pop("rv_triage", None)
    if c2.button("放弃", key="rv_f_reject"):
        feedback.reject(root, tid)
        st.session_state.pop("rv_triage", None)
        st.rerun()


# ─────────────────────────────── 设置 ───────────────────────────────

def _tab_settings(ws: str) -> None:
    root = Workspace(ws)
    cur = CFG.load(root)
    with st.form("rv_settings"):
        c1, c2 = st.columns(2)
        max_ch = c1.number_input("单次运行最多章数", 1, 200, int(cur["max_chapters_per_run"]), key="rv_s_ch")
        max_calls = c2.number_input("单次运行最多 LLM 调用", 1, 1000, int(cur["max_llm_calls_per_run"]), key="rv_s_calls")
        attempts = c1.number_input("单章最多尝试次数", 1, 5, int(cur["max_attempts_per_chapter"]), key="rv_s_att")
        scope = c2.selectbox("语义判定候选范围", ["hits", "all"], index=0 if cur["review_scope"] == "hits" else 1,
                             format_func=lambda s: "只判定命中定位词的章节" if s == "hits" else "全书逐章判定（成本高，能找到指代）",
                             key="rv_s_scope")
        lo = c1.number_input("字数比下限", 0.1, 1.0, float(cur["length_ratio_min"]), step=0.05, key="rv_s_lo")
        hi = c2.number_input("字数比上限", 1.0, 3.0, float(cur["length_ratio_max"]), step=0.05, key="rv_s_hi")
        stop = st.checkbox("某章失败即停止", value=bool(cur["stop_on_failure"]), key="rv_s_stop")
        saved = st.form_submit_button("保存设置")
    if saved:
        patch = {"max_chapters_per_run": int(max_ch), "max_llm_calls_per_run": int(max_calls),
                 "max_attempts_per_chapter": int(attempts), "review_scope": scope,
                 "length_ratio_min": float(lo), "length_ratio_max": float(hi), "stop_on_failure": bool(stop)}
        if _try(CFG.update, root, patch):
            st.success("已保存")


# ─────────────────────────────── 入口 ───────────────────────────────

def page_post_revision() -> None:
    ws = require_ws()
    if not ws:
        return
    st.header("📝 完稿修改")
    st.caption("完稿后的版本化修改：原稿只读，每次修改生成新版本。独立于写作流水线。")
    tabs = st.tabs(["📌 版本", "🧾 变更与预览", "▶ 运行", "💬 章节反馈", "⚙ 设置"])
    renders = [_tab_versions, _tab_changes, _tab_runs, _tab_feedback, _tab_settings]
    for tab, render in zip(tabs, renders):
        with tab:
            render(ws)
