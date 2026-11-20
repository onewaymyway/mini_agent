"""📝 完稿修改：完稿后的版本化修改（独立于写作流水线）。

七个分页：版本 / 变更与预览 / 运行 / 章节反馈 / 评估 / 大纲 / 设置。
  * 每一个按钮都经过 _act：开始、成功（含结果摘要）、失败都写入持久化的「操作日志」，页面底部可见。
  * 会调用 LLM 的操作（生成预览、开始改写、续跑、分诊）在后台线程执行，按钮旁边显示实时进度日志。
"""

from __future__ import annotations

import inspect
import json
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, Optional

import streamlit as st

from novel_writer.revision_flow import changes as CH
from novel_writer.revision_flow import config as CFG
from novel_writer.revision_flow import derived, executor, feedback, jobs, outline_check, outline_regen, review_report
from novel_writer.revision_flow import review_adopt, review_cards, review_checks, review_llm
from novel_writer.revision_flow import versions as V
from novel_writer.revision_flow.common import FlowError, sub_dir
from novel_writer.workspace import Workspace
from ui.components import require_ws

OPS_JOB = "ops"
_STATUS_LABEL = {
    "running": "运行中", "paused_limit": "已达上限（可续跑）", "stopped": "已停止（可续跑）",
    "interrupted": "已中断（可续跑）", "done": "已完成", "failed": "失败",
    "draft": "草稿", "confirmed": "已确认", "applied": "已应用",
    "open": "待处理", "adopted": "已采纳", "rejected": "已驳回", "resolved": "已解决",
    "still_open": "仍存在", "stale": "基于旧版本",
}
_JOB_STATE = {"running": "运行中", "done": "已完成", "failed": "失败", "unknown": "未开始"}


def _status(s: str) -> str:
    return _STATUS_LABEL.get(s, s)


def _invoker():
    from novel_writer.runner import FrameworkInvoker
    return FrameworkInvoker()


# ─────────────────────────────── 操作日志 ───────────────────────────────

_OP = "rv_op"                 # 当前运行中的页面操作（None 表示空闲）
_OP_LAST = "rv_op_last"       # 最近一次操作的结果（显示在页面底部）


def _store(key: str, transform: Callable[[Any], Any] = lambda r: r) -> Callable[[Any], None]:
    """操作完成时把结果写入 session_state，供页面在下一次渲染时展示。"""
    def done(result: Any) -> None:
        st.session_state[key] = transform(result)
    return done


def _op_running() -> bool:
    return st.session_state.get(_OP) is not None


def _act(root: Workspace, label: str, fn: Callable[..., Any], *args: Any,
         summarize: Optional[Callable[[Any], str]] = None,
         on_done: Optional[Callable[[Any], None]] = None, **kwargs: Any) -> None:
    """在后台线程执行一个页面操作；运行日志显示在页面最下方，完成后页面自动刷新。

    操作的开始、进度、结果、失败都写入该操作自己的日志（jobs/act_*.log）与页面底部的操作日志。
    fn 若接受 progress 参数，会收到一个写日志的回调。成功时调用 on_done(结果)，结果由调用方自己保存。
    同一时间只允许一个页面操作或后台任务运行。
    """
    if _op_running() or _busy(root):
        st.warning("已有操作在进行，请等它结束后再开始。")
        return
    op = f"act_{uuid.uuid4().hex[:8]}"
    box: Dict[str, Any] = {}
    takes_progress = "progress" in inspect.signature(fn).parameters

    def say(text: str) -> None:
        jobs.log(root, op, text)
        jobs.log(root, OPS_JOB, f"  {label} │ {text}")

    def runner() -> None:
        try:
            if takes_progress:
                box["result"] = fn(*args, progress=say, **kwargs)
            else:
                box["result"] = fn(*args, **kwargs)
        except BaseException as exc:  # 线程里的异常在页面底部报告
            box["error"] = exc

    jobs.log(root, OPS_JOB, f"▶ {label}：开始")
    jobs.log(root, op, f"▶ {label}：开始")
    thread = threading.Thread(target=runner, name=f"rv-{op}", daemon=True)
    thread.start()
    st.session_state[_OP] = {"id": op, "label": label, "summarize": summarize, "on_done": on_done,
                             "thread": thread, "box": box}


def _op_finish(root: Workspace) -> None:
    """操作线程结束后：记录结果、调用 on_done、写日志。只执行一次（执行后清空 _OP）。"""
    slot = st.session_state.pop(_OP, None)
    if slot is None:
        return
    label, op, box = slot["label"], slot["id"], slot["box"]
    exc = box.get("error")
    if exc is not None:
        msg = str(exc) if isinstance(exc, FlowError) else f"操作失败：{exc}"
        kind = "失败" if isinstance(exc, FlowError) else "异常"
        jobs.log(root, OPS_JOB, f"✘ {label}：{kind}：{exc}")
        jobs.log(root, op, f"✘ {kind}：{exc}")
        st.session_state[_OP_LAST] = {"label": label, "ok": False, "text": msg}
        return
    result = box.get("result")
    text = slot["summarize"](result) if slot["summarize"] else ""
    jobs.log(root, OPS_JOB, f"✔ {label}：完成{('，' + text) if text else ''}")
    jobs.log(root, op, f"✔ 完成{('：' + text) if text else ''}")
    st.session_state[_OP_LAST] = {"label": label, "ok": True, "text": text}
    if slot["on_done"] is not None:
        try:
            slot["on_done"](result)
        except Exception as cb_exc:  # 回调失败也要可见
            jobs.log(root, OPS_JOB, f"✘ {label}：后续步骤失败：{cb_exc}")
            st.session_state[_OP_LAST] = {"label": label, "ok": False, "text": f"后续步骤失败：{cb_exc}"}


def _op_body(root: Workspace) -> None:
    """运行中：显示实时日志（最新在下）；结束后收尾并刷新整页。"""
    slot = st.session_state.get(_OP)
    if slot is None:
        return
    if slot["thread"].is_alive():
        lines = jobs.tail(root, slot["id"], LOG_LINES).splitlines()
        st.markdown(f"**{slot['label']}**　运行中（实时日志，最新在下）")
        st.code("\n".join(lines[-TAIL_LINES:]) or "（等待输出…）", language=None)
        return
    _op_finish(root)
    st.rerun(scope="app")


def _op_panel(root: Workspace) -> None:
    """页面底部的操作面板：运行中时用 fragment 每 0.5 秒刷新日志；空闲时显示最近一次结果。"""
    slot = st.session_state.get(_OP)
    if slot is not None:
        if slot["thread"].is_alive():
            st.fragment(run_every=0.5)(_op_body)(root)
        else:
            _op_finish(root)
            st.rerun()
        return
    last = st.session_state.get(_OP_LAST)
    if last:
        if last["ok"]:
            st.success(f"{last['label']}：完成" + (f"，{last['text']}" if last["text"] else ""))
        else:
            st.error(f"{last['label']}：{last['text']}")


def _ops_panel(root: Workspace) -> None:
    with st.expander("操作日志（所有按钮的执行过程与结果）", expanded=False):
        st.code(jobs.tail(root, OPS_JOB, 300) or "（暂无）", language=None)


# ─────────────────────────────── 后台任务（单一任务槽 + 顶部面板） ───────────────────────────────

_ACTIVE = "rv_active"   # {"name", "label", "kind"}：同一时间只允许一个后台任务
LOG_LINES = 2000
TAIL_LINES = 40


def _active(root: Workspace) -> Optional[Dict[str, Any]]:
    slot = st.session_state.get(_ACTIVE)
    if not slot:
        return None
    return slot


def _busy(root: Workspace) -> bool:
    slot = _active(root)
    return bool(slot) and jobs.status(root, slot["name"]).get("state") == "running"


def _start_job(root: Workspace, label: str, kind: str, fn: Callable[..., Any], *args: Any) -> None:
    if _busy(root):
        st.warning("已有后台任务在运行，请等它结束后再开始。")
        return
    name = f"{kind}_{uuid.uuid4().hex[:8]}"
    jobs.log(root, OPS_JOB, f"▶ {label}：已提交后台任务 {name}")
    try:
        jobs.start(root, name, fn, root, name, *args)
    except Exception as exc:
        jobs.log(root, OPS_JOB, f"✘ {label}：无法启动：{exc}")
        st.error(f"无法开始：{exc}")
        return
    st.session_state[_ACTIVE] = {"name": name, "label": label, "kind": kind}


_JOB_STATE = {"running": "运行中", "done": "已完成", "failed": "失败", "unknown": "未开始"}


def _apply_result(kind: str, result: Dict[str, Any]) -> None:
    if kind == "preview" and result.get("preview_id"):
        st.session_state["rv_preview"] = result["preview_id"]
    elif kind == "increment":
        st.session_state["rv_increment"] = result
    elif kind == "triage" and result.get("triage_id"):
        st.session_state["rv_triage"] = result["triage_id"]


def _task_panel(root: Workspace) -> None:
    """页面顶部的唯一后台任务面板：实时日志（按时间顺序，最新在最下）、进度、耗时。"""
    slot = _active(root)
    if not slot:
        return
    name = slot["name"]
    done = jobs.status(root, name).get("state") in ("done", "failed")

    def body():
        info = jobs.status(root, name)
        state = info.get("state", "unknown")
        started = info.get("started_at", "")
        lines = jobs.tail(root, name, LOG_LINES).splitlines()
        st.markdown(f"**{slot['label']}**　状态：{_JOB_STATE.get(state, state)}　"
                    f"开始于 {started}　已输出 {len(lines)} 行（最新在下）")
        if info.get("error"):
            st.error(info["error"])
        st.code("\n".join(lines[-TAIL_LINES:]) or "（已提交，等待第一行输出…）", language=None)
        if len(lines) > TAIL_LINES:
            st.caption(f"以上是最近 {TAIL_LINES} 行，完整日志见下方。")
        with st.expander("完整日志"):
            st.code("\n".join(lines) or "（暂无）", language=None)
        if state in ("done", "failed") and not st.session_state.get(f"rv_seen_{name}"):
            st.session_state[f"rv_seen_{name}"] = True
            if state == "done":
                _apply_result(slot["kind"], info.get("result") or {})
            st.rerun()
        if state in ("done", "failed") and st.button("关闭面板", key=f"rv_close_{name}"):
            st.session_state.pop(_ACTIVE, None)
            st.rerun()

    st.fragment(run_every=None if done else 1.5)(body)()


def _preview_job(ws: Workspace, name: str, base: str, pick: list, use_llm: bool, progress) -> Dict[str, Any]:
    invoker = jobs.make_invoker(ws, name, _invoker()) if use_llm else None
    pv = CH.build_preview(ws, base, pick, invoker=invoker, progress=progress)
    return {"preview_id": pv["id"]}


def _run_job(ws: Workspace, name: str, pid: str, progress) -> Dict[str, Any]:
    run = executor.start(ws, pid, jobs.make_invoker(ws, name, _invoker()), progress=progress)
    return {"run_id": run["id"], "status": run["status"]}


def _resume_job(ws: Workspace, name: str, rid: str, progress) -> Dict[str, Any]:
    run = executor.resume(ws, rid, jobs.make_invoker(ws, name, _invoker()), progress=progress)
    return {"run_id": run["id"], "status": run["status"]}


def _triage_job(ws: Workspace, name: str, base: str, chapter: int, text: str, sel: str, progress) -> Dict[str, Any]:
    rec = feedback.triage(ws, base, chapter, text, jobs.make_invoker(ws, name, _invoker()),
                          selection=sel, progress=progress)
    return {"triage_id": rec["id"], "status": rec["status"]}


def _cards_job(ws: Workspace, name: str, progress) -> Dict[str, Any]:
    res = review_cards.generate_cards(ws, None, jobs.make_invoker(ws, name, _invoker()), progress=progress)
    return {"generated": res["generated"], "failed": len(res["failed"]), "paused": res["paused"]}


def _review_job(ws: Workspace, name: str, progress) -> Dict[str, Any]:
    res = review_llm.run_review(ws, None, jobs.make_invoker(ws, name, _invoker()), progress=progress)
    return {"review_id": res["review_id"], "calls": res["calls"], "paused": res["paused"],
            "failed": len(res["failed"])}


def _outline_job(ws: Workspace, name: str, progress) -> Dict[str, Any]:
    res = outline_regen.generate_candidate(ws, None, jobs.make_invoker(ws, name, _invoker()), progress=progress)
    return {"id": res["id"], "changed": res["summary"]["changed"]}


def _outline_check_job(ws: Workspace, name: str, progress) -> Dict[str, Any]:
    res = outline_check.run_check(ws, None, jobs.make_invoker(ws, name, _invoker()), progress=progress)
    return {"counts": res["counts"], "calls": res["calls"], "failed": len(res["failed"])}


def _increment_job(ws: Workspace, name: str, progress) -> Dict[str, Any]:
    return review_adopt.increment(ws, jobs.make_invoker(ws, name, _invoker()), progress=progress)


def _split(text: str):
    return [t.strip() for t in str(text or "").replace("，", ",").split(",") if t.strip()]


_DIFF_MAX_CHARS = 300_000


def _diff_view() -> None:
    """git 风格差异：先列出变化文件，再逐行展示所选文件（或全部）的 +/- 内容。"""
    res = st.session_state.get("rv_diff_result")
    if not res:
        return
    rows = res["rows"]
    changed = [r for r in rows if r["status"] != "same"]
    st.markdown(f"**{res['a']} → {res['b']}**　{len(changed)} 个文件有变化")
    if not changed:
        st.info("两个版本的内容完全相同。")
        return
    st.dataframe([{"文件": r["path"], "状态": {"changed": "修改", "added": "新增", "removed": "删除"}[r["status"]],
                   "新增行": r["added"], "删除行": r["removed"]} for r in changed],
                 hide_index=True, use_container_width=True)
    pick = st.selectbox("查看文件", ["全部文件"] + [r["path"] for r in changed], key="rv_diff_file")
    text = derived.format_diff(rows, None if pick == "全部文件" else pick)
    if len(text) > _DIFF_MAX_CHARS:
        st.warning(f"差异较长（{len(text)} 字符），仅显示前 {_DIFF_MAX_CHARS} 字符；请选择单个文件查看。")
        text = text[:_DIFF_MAX_CHARS]
    st.code(text, language="diff")


# ─────────────────────────────── 版本 ───────────────────────────────

def _tab_versions(root: Workspace) -> None:
    if not V.is_baseline_ready(root):
        st.info("尚未创建完稿基线。全书终审通过后，在这里生成只读的 v001。")
        if st.button("创建完稿基线 v001", key="rv_baseline"):
            _act(root, "创建完稿基线", V.init_baseline, root,
                 summarize=lambda e: f"{e['id']} 已生成（只读）")
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
            _act(root, f"校验 {pick}", V.verify, root, pick,
                 summarize=lambda p: "内容与清单一致" if not p else f"发现 {len(p)} 处不一致",
                 on_done=_store("rv_verify_res", lambda p: {"version": pick, "problems": p}))
        vr = st.session_state.get("rv_verify_res")
        if vr:
            if vr["problems"]:
                st.error(f"{vr['version']}：" + "；".join(vr["problems"][:5]))
            else:
                st.success(f"{vr['version']} 校验通过")
    with c2:
        if st.button("撤销最近一次修改（丢弃 head）", key="rv_undo",
                     disabled=index["head"] in (None, "v001")):
            head = index["head"]
            _act(root, f"撤销 {head}", V.undo_head, root,
                 summarize=lambda new: f"head 回到 {new}")
    st.markdown("**版本差异**")
    d1, d2 = st.columns(2)
    a = d1.selectbox("旧版本", ids, index=0, key="rv_diff_a")
    b = d2.selectbox("新版本", ids, index=len(ids) - 1, key="rv_diff_b")
    if st.button("查看差异", key="rv_diff", disabled=a == b):
        _act(root, f"比较 {a} 与 {b}", derived.diff_versions, root, a, b,
             summarize=lambda r: f"{sum(1 for x in r if x['status'] != 'same')} 个文件有差异",
             on_done=_store("rv_diff_result", lambda rows: {"a": a, "b": b, "rows": rows}))
    _diff_view()
    st.markdown("**导出**")
    ex = st.selectbox("导出版本", ids, key="rv_export_pick")
    if st.button("导出该版本", key="rv_export"):
        _act(root, f"导出 {ex}", derived.export_version, root, ex,
             summarize=lambda r: "已生成导出文件", on_done=_store("rv_export_res"))
    if st.session_state.get("rv_export_res"):
        st.json(st.session_state["rv_export_res"], expanded=False)


# ─────────────────────────────── 变更与预览 ───────────────────────────────

def _tab_changes(root: Workspace) -> None:
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
        _act(root, "保存变更", CH.save_change, root, raw,
             summarize=lambda c: f"{c['id']}「{c['title']}」，定位词 {'、'.join(c['terms'])}")

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
    if st.button("生成预览", key="rv_p_make", disabled=not pick or _busy(root)):
        _start_job(root, "生成预览", "preview", _preview_job, base, pick, use_llm)
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
            label = f"第{row['number']}章{'加回' if keep else '排除'}"
            _act(root, label, CH.set_excluded, root, pid, row["number"], not keep,
                 summarize=lambda p: f"当前参与改写 {sum(1 for r in p['chapters'] if not r['excluded'])} 章")
    if not pv["confirmed"] and st.button("确认预览", key="rv_p_confirm"):
        _act(root, f"确认预览 {pid}", CH.confirm_preview, root, pid,
             summarize=lambda p: f"{len(p['chapters'])} 章候选已锁定")


# ─────────────────────────────── 运行 ───────────────────────────────

def _tab_runs(root: Workspace) -> None:
    recovered = executor.recover_interrupted(root)
    if recovered:
        jobs.log(root, OPS_JOB, f"已识别中断的运行：{', '.join(recovered)}")
        st.warning(f"以下运行所在进程已结束，已标记为「已中断」，可续跑：{', '.join(recovered)}")
    previews = []
    for p in sorted(sub_dir(root, "preview").glob("prv_*.json")):
        d = json.loads(p.read_text(encoding="utf-8"))
        if d.get("confirmed"):
            previews.append(d["id"])
    st.markdown("**开始改写**")
    if previews:
        pid = st.selectbox("已确认的预览", previews, key="rv_r_pick")
        if st.button("开始改写", key="rv_r_start", disabled=_busy(root)):
            _act(root, f"检查预览 {pid}", executor.precheck_start, root, pid,
                 summarize=lambda p: f"待改写 {sum(1 for r in p['chapters'] if not r['excluded'])} 章",
                 on_done=lambda _p: _start_job(root, f"开始改写（{pid}）", "run", _run_job, pid))
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
        _act(root, f"请求停止 {rid}", executor.request_stop, root, rid,
             summarize=lambda _: "将在当前章节结束后生效")
    if run["status"] in executor.RESUMABLE and c2.button("续跑", key="rv_r_resume", disabled=_busy(root)):
        _act(root, f"检查续跑 {rid}", executor.precheck_resume, root, rid,
             summarize=lambda r: f"从未完成章节继续，已完成 {len(r['done'])} 章",
             on_done=lambda _r: _start_job(root, f"续跑（{rid}）", "run", _resume_job, rid))
    if run["status"] == "failed" and run.get("failed"):
        st.error(f"第{run['failed']['chapter']}章失败：{'；'.join(run['failed']['errors'])}")
    if run.get("derived_marks"):
        st.markdown("**建议重新检查的派生物**（不会自动运行）")
        st.dataframe(run["derived_marks"], hide_index=True, use_container_width=True)
    with st.expander("该运行的日志"):
        st.code("\n".join(run.get("log", [])[-80:]) or "（暂无）", language=None)


# ─────────────────────────────── 章节反馈 ───────────────────────────────

def _tab_feedback(root: Workspace) -> None:
    if not V.is_baseline_ready(root):
        st.info("请先创建完稿基线。")
        return
    versions = [v["id"] for v in V.list_versions(root)]
    base = st.selectbox("基于版本", versions, index=len(versions) - 1, key="rv_f_base")
    chapter = st.number_input("章号", min_value=1, value=1, step=1, key="rv_f_ch")
    fb = st.text_area("对本章的意见", key="rv_f_text")
    sel = st.text_area("选中的段落（可选）", key="rv_f_sel")
    if st.button("分诊（会调用模型）", key="rv_f_triage", disabled=not fb.strip() or _busy(root)):
        _start_job(root, f"分诊第{int(chapter)}章", "triage", _triage_job,
                   base, int(chapter), fb, sel)
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
        else:
            _act(root, "采纳分诊结果", feedback.accept, root, tid, mode=mode,
                 effective_from=int(eff) if eff else None,
                 summarize=lambda c: f"已生成变更 {c['id']}，请到「变更与预览」生成预览",
                 on_done=lambda _c: st.session_state.pop("rv_triage", None))
    if c2.button("放弃", key="rv_f_reject"):
        _act(root, "放弃分诊结果", feedback.reject, root, tid, summarize=lambda _: "已放弃",
             on_done=lambda _r: st.session_state.pop("rv_triage", None))


# ─────────────────────────────── 评估 ───────────────────────────────

_VERDICT_TEXT = {"confirmed": "确认为问题", "false_positive": "误报", "uncertain": "无法判定"}


def _tab_review(root: Workspace) -> None:
    if not V.is_baseline_ready(root):
        st.info("全书评估只在完稿后可用：请先在「版本」页创建 v001。")
        return
    st.caption("只读 head 版本：不改版本、不写流水线状态。所有问题都由作者裁决，不会自动修改任何章节。")
    est = review_cards.estimate(root)
    c1, c2, c3 = st.columns(3)
    c1.metric("章节数", est["chapters"])
    c2.metric("事实卡已缓存", est["cards_cached"])
    c3.metric("待生成事实卡", est["cards_to_generate"])
    lest = review_llm.estimate_llm(root)
    hint = "" if lest["checks_run"] else "（尚未运行确定性检查，候选数未知）"
    st.caption(f"预计 LLM 调用：事实卡 {est['cards_calls']} 次；复核约 {lest['calls_typical']} 次"
               f"（候选仲裁 {lest['adjudication_pending']}、衔接窗口 {lest['windows']}、全书 {lest['whole_book']}）"
               f"{hint}。调用上限：{lest['max_llm_calls'] or '不限'}")
    b1, b2, b3 = st.columns(3)
    if b1.button("① 生成事实卡（调用模型）", key="rv_e_cards", disabled=_busy(root)):
        _start_job(root, "生成事实卡", "cards", _cards_job)
    if b2.button("② 确定性检查（不调用模型）", key="rv_e_checks"):
        _act(root, "确定性检查", review_checks.run_checks, root, None,
             summarize=lambda r: f"问题 {sum(r['counts'].values())} 条")
    if b3.button("③ LLM 复核：仲裁 / 衔接 / 全书（调用模型）", key="rv_e_llm", disabled=_busy(root)):
        _start_job(root, "LLM 复核", "review", _review_job)

    vid = review_cards.resolve_version(root, None)
    review_adopt.sync_applied(root)                           # 已执行的采纳问题标为 applied
    data = review_checks.load_findings(root, vid)
    fs = data["findings"]
    parent = V.get_entry(root, vid).get("parent")
    if fs and st.button("导出评估报告（Markdown）", key="rv_e_export"):
        _act(root, "导出评估报告", review_report.export, root, vid,
             summarize=lambda r: f"{r['findings']} 条问题",
             on_done=_store("rv_export_text", lambda r: r["text"]))
    if st.session_state.get("rv_export_text"):
        st.download_button("下载评估报告", data=st.session_state["rv_export_text"],
                           file_name=f"评估报告_{vid}.md", mime="text/markdown", key="rv_e_dl")
    st.divider()
    st.markdown("**增量复评**")
    st.caption("改动后（head 有父版本）：重新生成事实卡（未改章节命中缓存）、确定性检查与 LLM 复核，并与改动前的问题对照："
               "已消失 → 已解决，仍在 → 仍存在，新出现的单独标记。")
    if st.button("运行增量复评（调用模型）", key="rv_e_inc", disabled=_busy(root) or not parent):
        _start_job(root, "增量复评", "increment", _increment_job)
    inc = st.session_state.get("rv_increment")
    if inc and inc.get("version") == vid:
        st.success(f"已对照 {inc['parent']} → {inc['version']}：已解决 {len(inc['resolved'])}，仍存在 "
                   f"{len(inc['still_open'])}，新出现 {len(inc['new'])}，已保留作者驳回 {len(inc['rejected_carried'])}")
    if not fs:
        st.info("还没有问题记录。先运行 ② 确定性检查，再运行 ③ LLM 复核；改动后请运行增量复评。")
        return
    st.markdown(f"**问题池（版本 {vid}）**")
    f1, f2, f3, f4 = st.columns(4)
    sev = f1.multiselect("严重度", list(review_checks.SEVERITIES), default=list(review_checks.SEVERITIES), key="rv_e_sev")
    cats = sorted({f["category"] for f in fs})
    cat = f2.multiselect("类别", cats, default=cats, key="rv_e_cat",
                         format_func=lambda c: review_checks.CATEGORY_LABEL.get(c, c))
    src = f3.multiselect("来源", ["deterministic", "llm", "outline"], default=["deterministic", "llm", "outline"],
                         key="rv_e_src",
                         format_func=lambda s: {"deterministic": "规则候选", "llm": "模型复核",
                                                "outline": "大纲偏离"}[s])
    stat = f4.multiselect("处理状态", list(review_checks.STATUSES), default=list(review_checks.STATUSES), key="rv_e_stat",
                          format_func=_status)
    s1, s2 = st.columns(2)
    show_fp = s1.checkbox("显示已被模型仲裁为误报的条目", value=False, key="rv_e_fp")
    show_unv = s2.checkbox("显示证据未核实的条目（默认折叠）", value=False, key="rv_e_unv")

    visible, folded = [], 0
    for f in fs:
        if f["severity"] not in sev or f["category"] not in cat or f["source"] not in src or f["status"] not in stat:
            continue
        verdict = (f.get("adjudication") or {}).get("verdict")
        unverified = f["source"] in ("llm", "outline") and not f.get("evidence_ok", True)
        if (verdict == "false_positive" and not show_fp) or (unverified and not show_unv):
            folded += 1
            continue
        visible.append(f)
    st.caption(f"显示 {len(visible)} 条；折叠 {folded} 条（误报或证据未核实，可用上方开关展开）")

    adoptable_batch = [f for f in visible if f["status"] in review_adopt.ADOPTABLE and f["category"] != "contradiction"
                       and f.get("evidence_ok", True) and not f.get("change_id")]
    if adoptable_batch:
        pick = st.multiselect("批量采纳（仅非矛盾、证据已核实）", [f["id"] for f in adoptable_batch],
                              key="rv_a_batch", format_func=lambda i: next(f["title"] for f in adoptable_batch if f["id"] == i))
        if st.button("批量采纳所选为变更", key="rv_a_batch_go", disabled=not pick):
            _act(root, "批量采纳", review_adopt.adopt_batch, root, vid, pick,
                 summarize=lambda r: f"生成 {len(r['created'])} 条变更，跳过 {len(r['skipped'])} 条")

    for f in visible:
        chapters = "、".join(str(c) for c in f["chapters"])
        cat_text = review_checks.CATEGORY_LABEL.get(f["category"], f["category"])
        with st.expander(f"{f['severity']}｜{cat_text}｜{f['title']}　（第{chapters}章）"):
            st.write(f["description"])
            origin = {"deterministic": "规则候选", "llm": "模型复核", "outline": "大纲偏离检查"}.get(f["source"], f["source"])
            st.caption(f"编号 {f['id']}　来源 {origin}　状态 {_status(f['status'])}")
            adj = f.get("adjudication")
            if adj:
                st.info(f"模型仲裁：{_VERDICT_TEXT.get(adj.get('verdict'), adj.get('verdict'))}——{adj.get('reason', '')}")
            elif f.get("needs_adjudication"):
                st.caption("待仲裁：规则只是候选，请运行 ③ LLM 复核或自行判断。")
            if f["source"] in ("llm", "outline") and not f.get("evidence_ok", True):
                st.warning("证据未核实：有引文不在章节原文中。该条不参与批量采纳，请核对原文后再决定。")
            for e in f["evidence"]:
                if e.get("quote"):
                    mark = "原文已核实" if e.get("verified") else "原文未核实"
                    st.write(f"- 第{e['chapter']}章（{mark}）：「{e['quote']}」")
                else:
                    st.write(f"- 第{e['chapter']}章：{e.get('note', '')}")
            _adopt_block(root, vid, f)
            new_status = st.selectbox("处理状态", list(review_checks.STATUSES),
                                      index=list(review_checks.STATUSES).index(f["status"]),
                                      key=f"rv_e_st_{f['id']}", format_func=_status)
            notes = st.text_input("备注", value=f.get("notes", ""), key=f"rv_e_nt_{f['id']}")
            if st.button("保存处理", key=f"rv_e_save_{f['id']}"):
                _act(root, f"标记问题 {f['id']}", review_checks.set_status, root, vid, f["id"], new_status, notes,
                     summarize=lambda _: f"状态 {_status(new_status)}")


def _adopt_block(root: Workspace, vid: str, f: Dict[str, Any]) -> None:
    """单条采纳：选择处理方向（矛盾类必须选），可改写修改意见与目标章节，然后生成变更。"""
    if f.get("change_id"):
        st.caption(f"已采纳为变更 {f['change_id']}（去「变更与预览」生成预览）")
        return
    if f["status"] not in review_adopt.ADOPTABLE:
        return
    opts = review_adopt.options(f)
    if not opts:
        return
    labels = [o["label"] for o in opts]
    if f["category"] == "contradiction":
        labels = ["请先选定以哪一方为准"] + labels
    pick = st.selectbox("采纳方式", list(range(len(labels))), format_func=lambda i: labels[i],
                        key=f"rv_a_opt_{f['id']}")
    idx = pick - 1 if f["category"] == "contradiction" else pick
    if idx < 0:
        st.caption("矛盾类问题需要先选定以哪一方为准，才能采纳。")
        return
    base = opts[idx]
    ins = st.text_area("修改意见（可改写后再采纳）", value=base["instruction"], height=140,
                       key=f"rv_a_ins_{f['id']}_{idx}")
    unverified_ok = True
    if f["source"] in ("llm", "outline") and not f.get("evidence_ok", True):
        unverified_ok = st.checkbox("我已核对原文，明确允许采纳这条证据未核实的问题", key=f"rv_a_unv_{f['id']}")
    if st.button("采纳为变更", key=f"rv_a_go_{f['id']}", disabled=not unverified_ok):
        _act(root, f"采纳问题 {f['id']}", review_adopt.adopt, root, vid, f["id"], option_index=idx,
             instruction=ins, allow_unverified=unverified_ok,
             summarize=lambda c: f"已生成变更 {c['id']}，请到「变更与预览」生成预览")


# ─────────────────────────────── 大纲 ───────────────────────────────

_FIELD_TEXT = {"function": "功能", "chapter_type": "类型", "pov": "视角", "tension_level": "张力",
               "word_budget": "字数预算", "characters_present": "出场人物"}


def _tab_outline(root: Workspace) -> None:
    if not V.is_baseline_ready(root):
        st.info("请先创建完稿基线。")
        return
    st.caption("根据 head 版本已写出的正文，反推实际大纲。候选不进入版本；采纳后派生「仅大纲」新版本，正文一字不动。")
    est = outline_regen.estimate(root)
    st.caption(f"共 {est['chapters']} 章，分 {est['batches']} 批判定，预计约 {est['calls_typical']} 次模型调用。"
               "需要每章都有事实卡（请先在「🔎 评估」页生成）。")
    if est["outline_first"]:
        st.info("本书为大纲先行：采纳大纲不会同步章节简述，请在变更日志中留意。")
    if st.button("生成候选大纲（调用模型）", key="rv_o_gen", disabled=_busy(root)):
        _start_job(root, "反推大纲", "outline", _outline_job)
    st.markdown("**偏离检查**：以当前版本的大纲为准，检查正文是否偏离（结果进入「🔎 评估」页的问题池，类别为「大纲偏离」）")
    c1, c2 = st.columns(2)
    if c1.button("仅确定性比对（不调用模型）", key="rv_o_chk0", disabled=_busy(root)):
        _act(root, "大纲偏离（确定性）", outline_check.run_check, root, None, None,
             summarize=lambda r: f"{sum(r['counts'].values())} 条")
    if c2.button("偏离检查（调用模型）", key="rv_o_chk1", disabled=_busy(root)):
        _start_job(root, "大纲偏离检查", "outcheck", _outline_check_job)

    cands = [c for c in outline_regen.list_candidates(root) if c["status"] == "candidate"]
    if not cands:
        st.info("没有待处理的候选大纲。")
        return
    pick = st.selectbox("候选大纲", [c["id"] for c in cands], key="rv_o_pick",
                        format_func=lambda i: next(f"{c['id']}（基于 {c['base_version']}，"
                                                   f"{c['summary']['changed']} 章有变化）" for c in cands if c["id"] == i))
    cand = outline_regen.load_candidate(root, pick)
    head = V.load_index(root)["head"]
    if cand["base_version"] != head:
        st.warning(f"head 已变为 {head}，本候选基于 {cand['base_version']}，不能采纳。请放弃后重新生成。")
    diff = outline_regen.diff_blueprints(outline_regen.current_blueprint(root, cand["base_version"]), cand["candidate"])
    rows = []
    for ch in diff["chapters"]:
        if ch["kind"] == "changed":
            for f, v in ch["fields"].items():
                rows.append({"章": ch["id"], "字段": _FIELD_TEXT.get(f, f), "旧": str(v["old"]), "新": str(v["new"])})
        else:
            rows.append({"章": ch["id"], "字段": "—", "旧": "—", "新": "蓝图中有、正文中没有" if ch["kind"] == "unwritten"
                         else "新增"})
    st.markdown(f"**差异**：{diff['counts']['changed']} 章有字段变化；结构{'有' if diff['structure_changed'] else '无'}变化")
    if rows:
        st.dataframe(rows, hide_index=True, use_container_width=True)
    if cand.get("unwritten"):
        st.warning(f"蓝图中有 {len(cand['unwritten'])} 个章节没有正文，采纳时保持不动：{cand['unwritten']}")
    changed = [ch["id"] for ch in diff["chapters"] if ch["kind"] == "changed"]
    chosen, edits = [], {}
    if changed:
        st.markdown("**采纳范围**（取消勾选则保持原样；可就地改功能描述）")
        for n in changed:
            cand_fn = next(c["function"] for c in cand["candidate"]["chapters"] if c["id"] == n)
            c1, c2 = st.columns([1, 3])
            take = c1.checkbox(f"第{n}章", value=True, key=f"rv_o_take_{pick}_{n}")
            fn = c2.text_input("功能", value=cand_fn, key=f"rv_o_fn_{pick}_{n}", label_visibility="collapsed")
            if take:
                chosen.append(n)
                if fn.strip() and fn.strip() != cand_fn:
                    edits[n] = {"function": fn.strip()}
    take_struct = st.checkbox("采用候选的幕划分与主线、支线概要", value=True, key=f"rv_o_struct_{pick}")
    b1, b2 = st.columns(2)
    if b1.button("采纳为新版本（仅大纲）", key=f"rv_o_adopt_{pick}", disabled=cand["base_version"] != head):
        _act(root, f"采纳大纲 {pick}", outline_regen.adopt, root, pick, chapters=chosen, edits=edits,
             take_structure=take_struct,
             summarize=lambda r: f"已生成 {r['version']}（正文未改动）")
    if b2.button("放弃候选", key=f"rv_o_disc_{pick}"):
        _act(root, f"放弃候选 {pick}", outline_regen.discard_candidate, root, pick,
             summarize=lambda _: "已放弃")


# ─────────────────────────────── 设置 ───────────────────────────────

def _tab_settings(root: Workspace) -> None:
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
        _act(root, "保存设置", CFG.update, root, patch, summarize=lambda s: f"候选范围 {s['review_scope']}")


# ─────────────────────────────── 入口 ───────────────────────────────

def page_post_revision() -> None:
    ws = require_ws()
    if not ws:
        return
    root = Workspace(ws)
    st.header("📝 完稿修改")
    st.caption("完稿后的版本化修改：原稿只读，每次修改生成新版本。独立于写作流水线。")
    tabs = st.tabs(["📌 版本", "🧾 变更与预览", "▶ 运行", "💬 章节反馈", "🔎 评估", "📚 大纲", "⚙ 设置"])
    renders = [_tab_versions, _tab_changes, _tab_runs, _tab_feedback, _tab_review, _tab_outline, _tab_settings]
    for tab, render in zip(tabs, renders):
        with tab:
            render(root)
    # 运行日志统一放在页面最下方：后台任务、页面操作、操作日志
    _task_panel(root)
    _op_panel(root)
    _ops_panel(root)
