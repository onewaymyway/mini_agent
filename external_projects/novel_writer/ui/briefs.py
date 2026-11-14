"""📚 章节简述（大纲先行 O2）：查看与编辑每章简述、按反馈改写、整幕重排、确认冻结。

长任务（LLM 改写、整幕重排）在后台线程执行，页面用定时刷新的日志框显示进度（最新在下）。
所有按钮的开始 / 结果 / 失败都写入操作日志（与完稿修改页共用 jobs/ops.log）。
"""

from __future__ import annotations

import uuid
from typing import Any, Callable, Dict, Optional

import streamlit as st

from novel_writer import brief as BR
from novel_writer import brief_review as BRV
from novel_writer import brief_sync as BSY
from novel_writer import state as st_state
from novel_writer.revision_flow import jobs
from novel_writer.revision_flow.common import FlowError
from novel_writer.workspace import Workspace
from ui.components import require_ws

OPS = "ops"
_STATE_LABEL = {"pending": "未运行", "running": "运行中", "waiting_review": "等待确认", "passed": "已确认",
                "stale": "需重新确认", "failed": "失败", "blocked": "阻塞"}


def _invoker():
    from novel_writer.runner import FrameworkInvoker
    return FrameworkInvoker()


def _act(root: Workspace, label: str, fn: Callable[..., Any], *args: Any, summarize=None, **kw: Any):
    jobs.log(root, OPS, f"▶ {label}：开始")
    try:
        out = fn(*args, **kw)
    except FlowError as exc:
        jobs.log(root, OPS, f"✘ {label}：失败：{exc}")
        st.error(str(exc))
        return None
    except Exception as exc:
        jobs.log(root, OPS, f"✘ {label}：异常：{exc}")
        st.error(f"操作失败：{exc}")
        return None
    text = summarize(out) if summarize else ""
    jobs.log(root, OPS, f"✔ {label}：完成{('，' + text) if text else ''}")
    return out


def _start_bg(root: Workspace, label: str, fn: Callable[..., Any], *args: Any) -> None:
    current = st.session_state.get("br_job")
    if current and jobs.status(root, current).get("state") == "running":
        st.warning("已有后台任务在运行，请等它结束。")
        return
    name = f"brief_{uuid.uuid4().hex[:8]}"
    jobs.log(root, OPS, f"▶ {label}：已提交后台任务 {name}")
    jobs.start(root, name, fn, root, name, *args)
    st.session_state["br_job"] = name


def _job_box(root: Workspace) -> None:
    name = st.session_state.get("br_job")
    if not name:
        return
    done = jobs.status(root, name).get("state") in ("done", "failed")

    def body():
        info = jobs.status(root, name)
        state = info.get("state", "unknown")
        lines = jobs.tail(root, name, 2000).splitlines()
        st.markdown(f"**后台任务**　状态：{ {'running': '运行中', 'done': '已完成', 'failed': '失败'}.get(state, state) }"
                    f"　已输出 {len(lines)} 行（最新在下）")
        if info.get("error"):
            st.error(info["error"])
        st.code("\n".join(lines[-40:]) or "（已提交，等待第一行输出…）", language=None)
        if state in ("done", "failed") and not st.session_state.get(f"br_seen_{name}"):
            st.session_state[f"br_seen_{name}"] = True
            if state == "done":
                st.session_state["br_result"] = info.get("result") or {}
            st.rerun()

    st.fragment(run_every=None if done else 2)(body)()


def _chapter_job(ws: Workspace, name: str, n: int, feedback: str, progress) -> Dict[str, Any]:
    res = BRV.rewrite_chapter(ws, n, feedback, jobs.make_invoker(ws, name, _invoker()))
    return res


def _act_job(ws: Workspace, name: str, act: int, start: int, end: int, feedback: str, progress) -> Dict[str, Any]:
    progress(f"第{act}幕重排：第{start}–{end}章，意见 {len(feedback)} 字")
    return BRV.regenerate_act(ws, act, start, end, feedback, jobs.make_invoker(ws, name, _invoker()))


def _lines(text: str):
    return [t.strip() for t in str(text or "").splitlines() if t.strip()]


def _editor(root: Workspace, n: int, b: Dict[str, Any]) -> None:
    k = b["key"]
    with st.form(f"br_form_{n}"):
        title = st.text_input("章名", value=b.get("title", ""), key=f"br_t_{n}")
        narrative = st.text_area("叙述简述（300–600 字）", value=b.get("narrative", ""), height=260, key=f"br_n_{n}")
        events = st.text_area("必须发生的事件（每行一条）", value="\n".join(k.get("must_events", [])), key=f"br_e_{n}")
        states = st.text_area("人物状态变化（每行：人物名：变化）",
                              value="\n".join(f"{s['who']}：{s['change']}" for s in k.get("must_states", [])),
                              key=f"br_s_{n}")
        fs = st.text_input("伏笔动作（逗号分隔的伏笔编号，如 F1, F3）", value=", ".join(k.get("must_foreshadow", [])),
                           key=f"br_f_{n}")
        hook = st.text_input("章末钩子", value=k.get("must_hook", ""), key=f"br_h_{n}")
        reason = st.text_input("本次修改原因（会记入版本历史）", value="作者编辑", key=f"br_r_{n}")
        saved = st.form_submit_button("保存修改")
    if saved:
        states_list = []
        for line in _lines(states):
            who, sep, change = line.partition("：")
            if not sep:
                who, sep, change = line.partition(":")
            states_list.append({"who": who.strip(), "change": change.strip()})
        brief = {"title": title, "narrative": narrative,
                 "key": {"must_events": _lines(events), "must_states": states_list,
                         "must_foreshadow": [x.strip() for x in fs.replace("，", ",").split(",") if x.strip()],
                         "must_hook": hook}}
        res = _act(root, f"保存第{n}章简述", BRV.save_edit, root, n, brief, reason or "作者编辑",
                   summarize=lambda r: f"已存为新版本 v{r['version']:03d}" if r.get("ok") else "")
        if res and not res["ok"]:
            st.error("未保存，请修正：\n" + "\n".join(f"- {e}" for e in res["errors"]))
        elif res:
            st.success("已保存。确认已失效，修改完成后需重新确认。")
            st.rerun()


def _chapter_section(root: Workspace, n: int) -> None:
    b = BR.load_brief(root, n)
    st.subheader(f"第{n}章")
    if not b:
        st.info("这一章还没有简述。")
        return
    st.caption(f"第{b.get('act')}幕 · 叙述 {BR.count_words(b.get('narrative', ''))} 字")
    _editor(root, n, b)

    vers = BRV.versions(root, n)
    if vers:
        st.markdown("**版本历史**")
        labels = {v["ver"]: f"v{v['ver']:03d} · {v['at']} · {v['reason']}" for v in vers}
        pick = st.selectbox("选择历史版本", [v["ver"] for v in vers], format_func=lambda x: labels[x],
                            key=f"br_vsel_{n}")
        if st.button("回退到这个版本", key=f"br_restore_{n}"):
            res = _act(root, f"第{n}章回退到 v{pick:03d}", BRV.restore, root, n, pick,
                       summarize=lambda r: f"新版本 v{r['version']:03d}" if r.get("ok") else "")
            if res and res.get("ok"):
                st.rerun()
            elif res:
                st.error("回退失败：" + "；".join(res["errors"]))

    st.markdown("**按反馈改写本章**")
    fb = st.text_area("对本章的修改意见", key=f"br_fb_{n}")
    busy = bool(st.session_state.get("br_job")) and jobs.status(root, st.session_state["br_job"]).get("state") == "running"
    if st.button("提交改写（会调用模型）", key=f"br_rw_{n}", disabled=not fb.strip() or busy):
        _start_bg(root, f"改写第{n}章", _chapter_job, n, fb)


def _acts_section(root: Workspace) -> None:
    from novel_writer import act_planning as AP
    from novel_writer import blueprint_store as BS
    from novel_writer.pipeline import total_chapters
    bp = BS.load_for_ws(root, strict=False)
    acts = AP.acts_of(bp, total_chapters(root, bp))
    if not acts:
        return
    st.markdown("**整幕重排**")
    labels = {a: f"第{a}幕（第{s}–{e}章）" for a, s, e in acts}
    pick = st.selectbox("选择幕", [a for a, _, _ in acts], format_func=lambda a: labels[a], key="br_act_pick")
    fb = st.text_area("本幕修改意见（整幕会重新生成，旧版本全部进入历史）", key="br_act_fb")
    busy = bool(st.session_state.get("br_job")) and jobs.status(root, st.session_state["br_job"]).get("state") == "running"
    if st.button("整幕重排（会调用模型）", key="br_act_go", disabled=not fb.strip() or busy):
        a, s, e = next(x for x in acts if x[0] == pick)
        _start_bg(root, f"重排第{a}幕", _act_job, a, s, e, fb)


def _changed_section(root: Workspace) -> None:
    """O4：简述修改后，依赖旧简述写成的章节。ask 策略下逐章由作者决定；auto_rewrite 策略在确认时自动提交重写。"""
    rows = BSY.affected(root)
    policy = (root.settings() or {}).get("brief_change_policy", "ask")
    if not rows:
        return
    st.warning(f"有 {len(rows)} 个已写章节依据的是旧简述（当前策略：{'自动重写' if policy == 'auto_rewrite' else '由作者决定'}）。")
    if policy == "auto_rewrite":
        st.caption("自动重写策略：确认简述时会为这些章节提交重写请求；也可以在下面逐章手动处理。")
        if st.button("现在按策略提交重写", key="bsy_apply"):
            res = _act(root, "按策略提交重写", BSY.apply_policy, root,
                       summarize=lambda r: f"已提交 {len(r['submitted'])} 章，失败 {len(r['failed'])} 章")
            if res:
                st.rerun()
    for row in rows:
        n = row["chapter"]
        with st.expander(f"第{n}章 {row['title']} · 简述已变更", expanded=False):
            st.markdown("\n".join(f"- {d}" for d in row["diff"]) or "（差异无法逐项列出，请对照版本历史）")
            c1, c2, c3 = st.columns(3)
            for col, label, fn, key in ((c1, "重写（按新简述）", BSY.rewrite, "rw"), (c2, "保留正文", BSY.keep, "kp"),
                                        (c3, "忽略此次变更", BSY.ignore, "ig")):
                if col.button(label, key=f"bsy_{key}_{n}"):
                    if _act(root, f"第{n}章：{label}", fn, root, n):
                        st.rerun()


def page_briefs() -> None:
    ws = require_ws()
    if not ws:
        return
    root = Workspace(ws)
    st.header("📚 章节简述")
    st.caption("大纲先行模式：每章 300–600 字场景简述 + 关键内容（写作不得偏离）。确认后才进入正文写作。")
    _job_box(root)
    if st.session_state.get("br_result"):
        res = st.session_state.pop("br_result")
        if res.get("affected"):
            st.warning("以下章节可能受本次修改影响，请自行检查：" + "、".join(f"第{k}章" for k in res["affected"]))

    state = st_state.get_step_state(root, BR.BRIEF_REVIEW_ID)
    status = state.get("status", "pending")
    conf = BRV.confirmation_status(root)
    c1, c2 = st.columns([2, 1])
    with c1:
        st.markdown(f"**确认状态**：{_STATE_LABEL.get(status, status)}"
                    + (f"　（确认于 {conf['confirmed_at']}）" if conf.get("confirmed") else ""))
        if state.get("note"):
            st.caption(state["note"])
        if conf.get("changed"):
            st.warning("已确认的简述后来被修改：" + "、".join(f"第{k}章" for k in conf["changed"]))
    with c2:
        if st.button("确认全部简述", key="br_confirm", type="primary", disabled=status != st_state.ST_WAITING_REVIEW):
            res = _act(root, "确认全部简述", BRV.confirm, root,
                       summarize=lambda r: f"共 {r['chapters']} 章，已冻结")
            if res:
                st.success("已确认，可以进入章节写作。")
                st.rerun()
    if status != st_state.ST_WAITING_REVIEW and status != st_state.ST_PASSED:
        st.info("简述需要先由流水线生成并运行到「章节简述确认」步骤，才能在这里确认。")

    problems = BRV.check_all(root)
    rows = []
    total = max([int(p.stem[1:4]) for p in BR.brief_dir(root).glob("第*章.json")] or [0])
    for n in range(1, total + 1):
        b = BR.load_brief(root, n)
        rows.append({"章": n, "章名": (b or {}).get("title", "—"),
                     "叙述字数": BR.count_words((b or {}).get("narrative", "")),
                     "校验": "通过" if n not in problems and b else ("缺失" if not b else "未通过"),
                     "历史版本": len(BRV.versions(root, n)) if b else 0})
    if rows:
        import pandas as pd
        st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
    else:
        st.info("还没有生成任何简述。")
        return

    _changed_section(root)
    _acts_section(root)
    st.divider()
    pick = st.selectbox("编辑章节", list(range(1, total + 1)), format_func=lambda n: f"第{n}章", key="br_pick")
    _chapter_section(root, pick)
    with st.expander("操作日志"):
        st.code(jobs.tail(root, OPS, 300) or "（暂无）", language=None)
