"""💡 灵感构思工作室（独立入口）。B1：题材卡、灵感池、发散。对话与提议在 B2 加入。"""

from __future__ import annotations

import json
from typing import Any, Dict

import streamlit as st

from novel_writer.brainstorm import api as BA
from novel_writer.brainstorm import chat as CH
from novel_writer.brainstorm import seed_draft as SD
from novel_writer.intake import INTAKE_FIELDS
from ui.components import show_error

_STATUS_LABEL = {"keep": "保留", "park": "搁置", "drop": "丢弃", "used": "已采用"}
_SRC_LABEL = {"author": "作者", "ai": "AI"}


def _current_root() -> str:
    return st.session_state.get("bs_root", "")


def _project_picker() -> None:
    res = BA.list_projects()
    projects = res["data"] if res["ok"] else []
    if not res["ok"]:
        show_error(res)
    labels = {p["root"]: f"{p['title']}（v{p['current_version']}）" for p in projects}
    with st.expander("➕ 新建构思项目", expanded=not projects):
        title = st.text_input("项目名称", key="bs_new_title", placeholder="例如：雾港")
        if st.button("创建", key="bs_create_btn", disabled=not title.strip()):
            r = BA.create_project(title.strip())
            if r["ok"]:
                st.session_state["bs_root"] = r["data"]["root"]
                st.rerun()
            show_error(r)
    if not projects:
        st.info("还没有构思项目。先在上面新建一个。")
        return
    roots = list(labels)
    cur = _current_root()
    idx = roots.index(cur) if cur in roots else 0
    chosen = st.selectbox("当前构思项目", roots, index=idx, format_func=lambda r: labels[r], key="bs_pick")
    if chosen != cur:
        st.session_state["bs_root"] = chosen
        st.rerun()


def _brief_tab(root: str) -> None:
    res = BA.get_project(root)
    if not res["ok"]:
        show_error(res)
        return
    brief = res["data"]["cards"]["brief"]
    st.caption("题材卡与新建向导的字段一一对应；交接成小说时会作为起点。")
    with st.form("bs_brief_form"):
        values = {}
        for key, label, multiline in INTAKE_FIELDS:
            if multiline:
                values[key] = st.text_area(label, value=brief.get(key, ""), key=f"bs_b_{key}")
            else:
                values[key] = st.text_input(label, value=brief.get(key, ""), key=f"bs_b_{key}")
        if st.form_submit_button("保存题材卡"):
            r = BA.save_brief(root, values)
            st.success("已保存。") if r["ok"] else show_error(r)


def _ideas_tab(root: str) -> None:
    with st.form("bs_add_idea", clear_on_submit=True):
        text = st.text_area("新灵感", key="bs_idea_text", height=80)
        tags = st.text_input("标签（用逗号分隔，可空）", key="bs_idea_tags")
        if st.form_submit_button("加入灵感池") and text.strip():
            r = BA.add_idea(root, text, [t for t in tags.replace("，", ",").split(",")])
            if not r["ok"]:
                show_error(r)
    status_filter = st.radio("筛选", ["全部", "保留", "搁置", "已采用", "丢弃"], horizontal=True, key="bs_idea_filter")
    wanted = {"全部": None, "保留": "keep", "搁置": "park", "已采用": "used", "丢弃": "drop"}[status_filter]
    res = BA.list_ideas(root, wanted)
    if not res["ok"]:
        show_error(res)
        return
    if not res["data"]:
        st.caption("（暂无）")
    for idea in res["data"]:
        with st.container(border=True):
            st.write(idea["text"])
            meta = f"{_SRC_LABEL.get(idea['source'], idea['source'])} · {_STATUS_LABEL.get(idea['status'], idea['status'])}"
            if idea.get("tags"):
                meta += " · " + "、".join(idea["tags"])
            st.caption(meta)
            cols = st.columns(4)
            for col, (status, label) in zip(cols, [("keep", "保留"), ("park", "搁置"), ("used", "已采用"), ("drop", "丢弃")]):
                if col.button(label, key=f"bs_st_{idea['id']}_{status}", disabled=idea["status"] == status):
                    r = BA.update_idea(root, idea["id"], status=status)
                    show_error(r) if not r["ok"] else st.rerun()


def _spark_tab(root: str) -> None:
    st.caption("让 AI 围绕一个方向给出候选。候选不会直接进入灵感池，需要你逐条采纳。")
    direction = st.text_input("方向（可空）", key="bs_spark_dir", placeholder="例如：反派的动机、不同寻常的城市")
    count = st.slider("候选数量", 3, 10, 6, key="bs_spark_n")
    if st.button("▶ 发散", key="bs_spark_go"):
        with st.spinner("正在生成候选……"):
            r = BA.spark(root, direction, count)
        if r["ok"]:
            st.session_state["bs_spark_last"] = r["data"]["id"]
        else:
            show_error(r)
    hist = BA.spark_history(root)
    if not hist["ok"]:
        show_error(hist)
        return
    for rec in hist["data"][:5]:
        head = rec.get("direction") or "不限方向"
        with st.expander(f"{rec['created_at']} · {head}", expanded=rec["id"] == st.session_state.get("bs_spark_last")):
            picks = []
            for cand in rec["candidates"]:
                label = cand["text"] + (f"  〔{'、'.join(cand['tags'])}〕" if cand["tags"] else "")
                if cand.get("adopted"):
                    st.write(f"✅ {label}")
                elif st.checkbox(label, key=f"bs_pick_{rec['id']}_{cand['cid']}"):
                    picks.append(cand["cid"])
            if st.button("采纳所选", key=f"bs_adopt_{rec['id']}", disabled=not picks):
                r = BA.adopt_candidates(root, rec["id"], picks)
                show_error(r) if not r["ok"] else st.rerun()


_KIND_LABEL = {"brief": "题材", "premise": "命题", "ideas": "灵感", "open_questions": "开放问题",
               "world": "世界观", "cast": "角色", "plot": "剧情"}
_OP_LABEL = {"add": "新增", "set": "修改", "delete": "删除", "replace": "整体替换"}


_EVENT_ICON = {"tool_call": "🔧", "tool_result": "📤", "tool_error": "❌", "info": "ℹ️", "warning": "⚠️",
               "error": "❌", "success": "✅", "stats": "📊", "markdown": "📝", "debug": "🐞",
               "skill_loaded": "🧩", "retry_banner": "🔁", "assistant_prefix": "🤖"}


def _render_events(events: list, *, key_prefix: str) -> None:
    """按时间顺序显示过程日志。工具结果用代码块完整显示，便于核对。"""
    for i, ev in enumerate(events):
        icon = _EVENT_ICON.get(ev.get("kind", ""), "•")
        text = ev.get("text", "")
        if ev.get("kind") in ("tool_result", "debug", "markdown") and "\n" in text or len(text) > 160:
            head, _, body = text.partition("\n")
            st.markdown(f"{icon} `{ev.get('t', '')[-8:]}` **{ev.get('kind', '')}** {head[:120]}")
            if body.strip():
                st.code(body, language="text")
        else:
            st.markdown(f"{icon} `{ev.get('t', '')[-8:]}` {text}")


@st.fragment(run_every=1)
def _live_turn(root: str, turn_id: str) -> None:
    res = BA.chat_run(root, turn_id)
    if not res["ok"]:
        show_error(res)
        return
    data = res["data"]
    running = data["status"] == "running"
    st.markdown("**本轮过程**" + ("（进行中……）" if running else "（已完成）"))
    _render_events(data["events"], key_prefix=f"live_{turn_id}")
    if not running:
        st.session_state.pop("bs_turn", None)
        st.rerun(scope="app")


def _finished_log(root: str, turn_id: str) -> None:
    res = BA.chat_run(root, turn_id)
    if not res["ok"]:
        return
    events = res["data"]["events"]
    with st.expander(f"本轮全部过程（{len(events)} 条）"):
        if not events:
            st.caption("（没有过程输出）")
        _render_events(events, key_prefix=f"done_{turn_id}")


@st.fragment(run_every=1)
def _live_distill(root: str, run_id: str) -> None:
    res = BA.distill_state(root, run_id)
    if not res["ok"]:
        show_error(res)
        return
    data = res["data"]
    running = data["status"] == "running"
    st.markdown("**整理过程**" + ("（进行中……）" if running else "（已完成）"))
    _render_events(data["events"], key_prefix=f"dist_{run_id}")
    if not running:
        st.session_state["bs_distill_last"] = run_id
        st.session_state.pop("bs_distill_run", None)
        st.rerun(scope="app")


def _finished_distill(root: str, run_id: str) -> None:
    res = BA.distill_state(root, run_id)
    if not res["ok"]:
        return
    data = res["data"]
    with st.expander(f"上一次整理的过程（{len(data['events'])} 条）", expanded=False):
        _render_events(data["events"], key_prefix=f"dist_done_{run_id}")
    if data["status"] == "done":
        r = data["result"]
        st.session_state["bs_minutes_last"] = r["minutes"]
        st.success(f"整理完成：待确认 {r['proposals']} 条，其中冲突 {r['conflicts']} 条，跳过 {r['skipped']} 条。")
    elif data["status"] == "error":
        st.error("整理失败：" + str(data.get("error", "")))


def _distill_box(root: str) -> None:
    from novel_writer.brainstorm import distill as DS

    with st.expander("📝 整理对话（手动）：把对话提取成待确认的修改与 Markdown 纪要"):
        st.caption("只有点击「整理」才会运行。整理过程的每一步都会显示在下方。"
                   "提取结果进入待确认列表，不会直接写入卡片。")
        scope = st.radio("范围", ["上次整理之后的全部对话", "最近几轮", "全部对话（从头整理）"], horizontal=True,
                         key="bs_dist_scope")
        rounds = 0
        if scope == "最近几轮":
            rounds = st.slider("轮数（一轮 = 作者一句 + 助手一句）", 1, 20, 5, key="bs_dist_rounds")
        labels = {m: DS.MODULE_LABEL[m] for m in DS.DISTILLABLE}
        modules = st.multiselect("只整理这些模块", list(DS.DISTILLABLE), default=list(DS.DISTILLABLE),
                                 format_func=lambda m: labels[m], key="bs_dist_modules")
        running = st.session_state.get("bs_distill_run")
        if st.button("▶ 整理", key="bs_dist_go", disabled=not modules or bool(running)):
            scope_key = "all" if scope.startswith("全部") else ("recent" if rounds else "pending")
            r = BA.distill_start(root, modules=modules, scope=scope_key, recent_rounds=rounds)
            if r["ok"]:
                st.session_state["bs_distill_run"] = r["data"]["run"]
                st.rerun()
            show_error(r)
        if running:
            _live_distill(root, running)
        elif st.session_state.get("bs_distill_last"):
            _finished_distill(root, st.session_state["bs_distill_last"])
        _minutes_box(root)


@st.fragment(run_every=1)
def _live_lore(root: str, run_id: str) -> None:
    res = BA.lore_state(root, run_id)
    if not res["ok"]:
        show_error(res)
        return
    data = res["data"]
    running = data["status"] == "running"
    st.markdown("**生成过程**" + ("（进行中……）" if running else "（已完成）"))
    _render_events(data["events"], key_prefix=f"lore_{run_id}")
    if not running:
        st.session_state["bs_lore_last"] = run_id
        st.session_state.pop("bs_lore_run", None)
        st.rerun(scope="app")


def _finished_lore(root: str, run_id: str) -> None:
    res = BA.lore_state(root, run_id)
    if not res["ok"]:
        return
    data = res["data"]
    with st.expander(f"上一次生成的过程（{len(data['events'])} 条）", expanded=False):
        _render_events(data["events"], key_prefix=f"lore_done_{run_id}")
    if data["status"] == "done":
        st.session_state["bs_lore_doc"] = data["result"]["path"]
        st.success(f"设定文档已生成（{data['result']['chars']} 字）。")
    elif data["status"] == "error":
        st.error("生成失败：" + str(data.get("error", "")))


def _lore_box(root: str) -> None:
    with st.expander("📖 生成设定文档：把对话整理成小说的世界观与设定文档"):
        st.caption("只有点击「生成设定文档」才会运行。素材只用对话；每次生成一份新文件，不覆盖旧的；"
                   "不会修改卡片，也不进待确认列表。")
        scope = st.radio("范围", ["全部对话", "最近几轮"], horizontal=True, key="bs_lore_scope")
        rounds = 0
        if scope == "最近几轮":
            rounds = st.slider("轮数（一轮 = 作者一句 + 助手一句）", 1, 40, 10, key="bs_lore_rounds")
        running = st.session_state.get("bs_lore_run")
        if st.button("▶ 生成设定文档", key="bs_lore_go", disabled=bool(running)):
            r = BA.lore_start(root, scope="recent" if rounds else "all", recent_rounds=rounds)
            if r["ok"]:
                st.session_state["bs_lore_run"] = r["data"]["run"]
                st.rerun()
            show_error(r)
        if running:
            _live_lore(root, running)
        elif st.session_state.get("bs_lore_last"):
            _finished_lore(root, st.session_state["bs_lore_last"])
        _lore_docs(root)


def _lore_docs(root: str) -> None:
    res = BA.lore_list(root)
    if not res["ok"] or not res["data"]:
        if res["ok"]:
            st.caption("还没有设定文档。")
        return
    names = res["data"]
    last = st.session_state.get("bs_lore_doc")
    idx = names.index(last) if last in names else 0
    chosen = st.selectbox("设定文档", names, index=idx, format_func=lambda n: n.rsplit("/", 1)[-1], key="bs_lore_pick")
    body = BA.lore_read(root, chosen)
    if not body["ok"]:
        show_error(body)
        return
    st.download_button("下载 Markdown", body["data"], file_name=chosen.rsplit("/", 1)[-1], mime="text/markdown",
                       key="bs_lore_dl")
    with st.container(border=True):
        st.markdown(body["data"])


def _minutes_box(root: str) -> None:
    res = BA.distill_minutes(root)
    if not res["ok"] or not res["data"]:
        if res["ok"]:
            st.caption("还没有纪要。")
        return
    names = res["data"]
    last = st.session_state.get("bs_minutes_last")
    idx = names.index(last) if last in names else 0
    chosen = st.selectbox("纪要", names, index=idx, format_func=lambda n: n.rsplit("/", 1)[-1], key="bs_min_pick")
    body = BA.distill_read_minutes(root, chosen)
    if body["ok"]:
        with st.container(border=True):
            st.markdown(body["data"])
    else:
        show_error(body)


def _conversation_bar(root: str, busy: bool) -> None:
    """历史对话列表（只含当前构思项目的对话）：选择后切换继续对话，或新开一段。"""
    res = BA.chat_list(root)
    if not res["ok"]:
        show_error(res)
        return
    items, current = res["data"]["items"], res["data"]["current"]
    with st.container(border=True):
        cols = st.columns([4, 1])
        if items:
            ids = [i["id"] for i in items]
            labels = {i["id"]: f"{i['title']}（{i['turns']} 条 · {i['updated_at'][5:16].replace('T', ' ')}）"
                      for i in items}
            # key 带上当前对话 id：切换后组件按新的 index 重建，避免显示旧选项
            chosen = cols[0].selectbox("历史对话（仅本项目）", ids, index=ids.index(current) if current in ids else 0,
                                       format_func=lambda c: labels[c], key=f"bs_conv_pick_{current}",
                                       disabled=busy)
            if chosen != current and not busy:
                r = BA.chat_switch(root, chosen)
                show_error(r) if not r["ok"] else st.rerun()
        else:
            cols[0].caption("还没有对话。在下方发一句话，就会开始第一段对话。")
        if cols[1].button("新对话", key="bs_conv_new", disabled=busy,
                          help="切换到一段新的空白对话；之前的对话保留在列表里，可随时切回"):
            r = BA.chat_new(root)
            show_error(r) if not r["ok"] else st.rerun()


def _scope_picker(root: str, busy: bool) -> None:
    """上下文范围：每轮提示词里附带什么。项目级设置，对本项目所有对话生效。"""
    res = BA.scope_get(root)
    if not res["ok"]:
        show_error(res)
        return
    _web_access_toggle(root, busy)
    current, options = res["data"]["scope"], res["data"]["options"]
    keys = list(options)
    labels = {k: (v if k in res["data"]["implemented"] else f"{v}（待实现）") for k, v in options.items()}
    chosen = st.selectbox("上下文范围", keys, index=keys.index(current), format_func=lambda k: labels[k],
                          key=f"bs_scope_{current}", disabled=busy,
                          help="摘要：只附概要。读取文件：模型用只读工具自己读取项目文件（不限次数与大小）。")
    if chosen != current:
        r = BA.scope_set(root, chosen)
        show_error(r) if not r["ok"] else st.rerun()
    elif current in ("full", "files"):
        b = BA.context_budget(root)
        if b["ok"]:
            d = b["data"]
            msg = f"每轮附上全部产出：约 {d['chars']} / {d['limit']} 字。"
            if d["omitted"]:
                msg += "因字数上限已省略：" + "、".join(d["omitted"]) + "。"
            st.caption(msg)


def _web_access_toggle(root: str, busy: bool) -> None:
    """「联网与执行权限」总开关：默认关闭；开启后本项目对话可以联网搜索、用浏览器 skill、执行 bash，且全部自动允许。"""
    res = BA.web_access_get(root)
    if not res["ok"]:
        show_error(res)
        return
    current = bool(res["data"]["enabled"])
    checked = st.checkbox("联网与执行权限（开启后：网页搜索、浏览器 skill、bash 全部自动允许，不再逐条确认）",
                          value=current, key="bs_web_access", disabled=busy,
                          help="默认关闭。开启时本项目的对话忽略上面的上下文范围，使用联网模式。")
    if checked != current:
        r = BA.web_access_set(root, checked)
        show_error(r) if not r["ok"] else st.rerun()
    if current:
        st.warning("已开启：模型可以自动执行命令、访问网络和浏览器。网页内容可能包含误导性指令，请留意对话与产物。")


def _render_proposal_value(item: Dict[str, Any]) -> None:
    """提议内容的显示。文字用可编辑的文本框（不走 st.json：st.json 会把字符串当 JSON 解析，
    中文文本会报 Json Parse Error）；对象/数组用 st.json；显示失败时把原始内容全文给作者。"""
    val = item["value"]
    try:
        if isinstance(val, str):
            if val.lstrip()[:1] in "[{":
                st.caption("⚠ 这段内容看起来是 JSON，可能是模型输出的格式问题，请核对后再采纳。")
            st.text_area("内容（可直接修改，采纳时以修改后的为准）", value=val,
                         height=min(400, 80 + len(val) // 25), key=f"bs_pval_{item['pid']}")
        else:
            st.json(val, expanded=False)
    except Exception as exc:  # noqa: BLE001 - 检测到显示问题：把原始内容全文展示出来
        st.error(f"这条提议的内容无法在卡片上显示：{type(exc).__name__}: {exc}")
        st.code(val if isinstance(val, str) else repr(val), language=None)


def _raw_key(rec: Dict[str, Any], prefix: str) -> str:
    return prefix + "".join(ch if ch.isalnum() else "_" for ch in (rec.get("at", "") + rec.get("text", "")[:40]))


def _fill_repair_suggestion(root: str, key: str) -> None:
    """回调：把自动修复建议填入编辑框（仅供审阅，不登记）。"""
    r = BA.proposal_block_suggest_repair(st.session_state.get(key, ""))
    if r["ok"] and r["data"]["suggestion"]:
        st.session_state[key] = r["data"]["suggestion"]
        st.session_state["bs_raw_msg"] = ("warning", "已填入自动修复的建议，请逐条核对后再登记（修复可能不准确）。")
    else:
        st.session_state["bs_raw_msg"] = ("error", "无法自动修复，请直接在文本框里手动修改。")


def _raw_proposals_box(root: str, rec: Dict[str, Any]) -> None:
    """解析失败的提议：全文展示模型输出的原文，允许手动修改后重新登记。"""
    key = _raw_key(rec, "bs_raw_")
    st.warning("这一轮的提议没能完整解析：" + (rec.get("proposal_error") or "未知错误")
               + "。下面是模型输出的完整原文，可以直接修改，然后重新登记。已经正确的条目请删除，避免重复。")
    st.text_area("提议原文（JSON 数组）", value=rec["raw_proposals"], height=280, key=key)
    c1, c2 = st.columns(2)
    if c1.button("按修改后的内容登记", key=key + "_reg", help="解析通过后登记为待确认提议，仍需采纳才会写入卡片。"):
        r = BA.proposal_block_register(root, st.session_state.get(key, ""))
        if r["ok"]:
            d = r["data"]
            msg = f"已登记 {len(d['proposals'])} 条待确认提议（采纳后才会写入卡片）。"
            if d["failures"]:
                msg += "未登记：" + "；".join(d["failures"])
            st.session_state["bs_raw_msg"] = ("success", msg)
            st.rerun()
        else:
            show_error(r)
    c2.button("尝试自动修复（填入编辑框，需人工确认）", key=key + "_fix",
              on_click=_fill_repair_suggestion, args=(root, key),
              help="自动修复可能把内容改错，只会填入编辑框，不会直接登记。")
    msg = st.session_state.pop("bs_raw_msg", None)
    if msg:
        getattr(st, msg[0])(msg[1])


def _save_setting_doc_copy(root: str) -> None:
    r = BA.setting_doc_save(root)
    if not r["ok"]:
        st.session_state["bs_export_error"] = r["error"]["message"]


def _export_setting_doc_button(root: str) -> None:
    """导出当前全部成果为 Markdown 设定文档：下载，同时在项目的 exports/ 目录留一份副本。"""
    res = BA.setting_doc_markdown(root)
    if not res["ok"]:
        show_error(res)
        return
    d = res["data"]
    st.download_button("📄 导出设定文档（Markdown）", data=d["markdown"].encode("utf-8"),
                       file_name=d["filename"], mime="text/markdown", key="bs_export_setting_doc",
                       on_click=_save_setting_doc_copy, args=(root,),
                       help="把当前构思的全部成果（题材卡、命题、灵感池、世界观、角色、剧情骨架、备选方案、开放问题）导出为一份 Markdown 设定文档。")
    err = st.session_state.pop("bs_export_error", None)
    if err:
        st.warning(f"设定文档已下载，但保存副本失败：{err}")


def _outputs_panel(root: str, busy: bool) -> None:
    """对话区上方：当前构思的全部成果（只读）。每一组可以点「让AI修改」，把它设为下一句话的修改对象。"""
    res = BA.outputs(root)
    if not res["ok"]:
        show_error(res)
        return
    secs = res["data"]
    total = sum(s["count"] for s in secs)
    with st.expander(f"📋 当前构思成果（{total} 项）", expanded=False):
        for sec in secs:
            c1, c2 = st.columns([5, 1])
            c1.markdown(f"**{sec['label']}**（{sec['count']}）")
            for line in sec["lines"]:
                c1.markdown("· " + line)
            if not sec["lines"]:
                c1.caption("（暂无内容）")
            if c2.button("让AI修改", key=f"bs_focus_{sec['key']}", disabled=busy,
                         help="下一句话会针对这一项提出修改"):
                st.session_state["bs_focus"] = sec["key"]
        st.caption("修改请求只会生成待确认的提议，采纳后才写入。")
        _export_setting_doc_button(root)


def _dialogue_tab(root: str) -> None:
    hist = BA.chat_history(root, limit=60)
    if not hist["ok"]:
        show_error(hist)
        return
    busy = bool(st.session_state.get("bs_turn"))
    _conversation_bar(root, busy=busy)
    _scope_picker(root, busy=busy)
    _outputs_panel(root, busy=busy)
    top = st.columns([3, 1])
    mode = top[0].radio("模式", list(CH.MODES), horizontal=True, key="bs_chat_mode")
    top[1].caption("当前对话已保存，可在上方切换")
    for rec in hist["data"]:
        role = "user" if rec["role"] == "user" else "assistant"
        with st.chat_message(role):
            st.write(rec["text"])
            if role == "assistant" and rec.get("raw_proposals"):
                _raw_proposals_box(root, rec)
            if rec.get("turn") and role == "assistant" and rec["turn"] != st.session_state.get("bs_turn"):
                _finished_log(root, rec["turn"])
    active = st.session_state.get("bs_turn")
    if active:
        with st.chat_message("assistant"):
            _live_turn(root, active)
    _distill_box(root)
    _lore_box(root)
    focus = st.session_state.get("bs_focus")
    if focus:
        label = next((s["label"] for s in BA.outputs(root)["data"] if s["key"] == focus), focus)
        fc1, fc2 = st.columns([5, 1])
        fc1.info(f"本次修改对象：{label}。下一句话会针对它提出修改。")
        if fc2.button("取消", key="bs_focus_clear"):
            st.session_state.pop("bs_focus", None)
            st.rerun()
    text = st.chat_input("说点什么……（例如：我想写一个会吞噬记忆的港口）", key="bs_chat_input",
                         disabled=bool(active))
    if text:
        r = BA.chat_start(root, text, mode, focus=focus)
        if r["ok"]:
            st.session_state.pop("bs_focus", None)
            st.session_state["bs_turn"] = r["data"]["turn"]
            st.rerun()
        show_error(r)
    pending = BA.list_proposals(root, "pending")
    if not pending["ok"]:
        show_error(pending)
        return
    if pending["data"]:
        st.subheader(f"待确认的提议（{len(pending['data'])}）")
        st.caption("AI 的建议不会自动生效。采纳后才会写入卡片。")
    for item in pending["data"]:
        with st.container(border=True):
            label = f"{_OP_LABEL.get(item['op'], item['op'])}{_KIND_LABEL.get(item['module'], item['module'])}"
            if item.get("target"):
                label += f"（{item['target']}）"
            st.markdown(f"**{label}**")
            if item["op"] == "replace":
                pv = BA.proposal_preview(root, item["pid"])
                if pv["ok"]:
                    st.code(pv["data"]["diff"], language="diff")
            elif item["value"] is not None:
                _render_proposal_value(item)
            if item.get("reason"):
                st.caption("理由：" + item["reason"])
            if item.get("error"):
                st.warning("上次采纳失败：" + item["error"])
            if item.get("conflict"):
                st.warning("⚠ 与现有内容冲突：" + item["conflict"])
                if st.button("与 AI 在对话里处理这个冲突", key=f"bs_conf_{item['pid']}", disabled=bool(active)):
                    pr = BA.distill_conflict_prompt(root, item["pid"])
                    if pr["ok"]:
                        r = BA.chat_start(root, pr["data"], st.session_state.get("bs_chat_mode", "追问"))
                        if r["ok"]:
                            st.session_state["bs_turn"] = r["data"]["turn"]
                            st.rerun()
                        show_error(r)
                    else:
                        show_error(pr)
            c1, c2 = st.columns(2)
            if c1.button("采纳", key=f"bs_acc_{item['pid']}"):
                edited = None
                if isinstance(item["value"], str):
                    cur = st.session_state.get(f"bs_pval_{item['pid']}")
                    if cur is not None and cur != item["value"]:
                        edited = cur            # 作者在卡片里改过文字：采纳修改后的内容
                r = BA.accept_proposal(root, item["pid"], edited)
                show_error(r) if not r["ok"] else st.rerun()
            if c2.button("拒绝", key=f"bs_rej_{item['pid']}"):
                r = BA.reject_proposal(root, item["pid"])
                show_error(r) if not r["ok"] else st.rerun()


_WORLD_LABEL = {"rules": "规则", "locations": "地点", "factions": "势力", "timeline": "时间线", "taboos": "禁忌"}


def _world_section(root: str, world: dict, key: str) -> None:
    """世界观的一个分类：标题、该类全部条目、新增表单。各分类互不影响。"""
    label = _WORLD_LABEL[key]
    st.subheader(f"{label}（{len(world[key])}）")
    with st.form(f"bs_world_add_{key}", clear_on_submit=True):
        text = st.text_area(f"新增{label}", key=f"bs_world_text_{key}", height=70)
        cost = st.text_input("代价（仅规则需要）", key=f"bs_world_cost_{key}") if key == "rules" else ""
        if st.form_submit_button("添加") and text.strip():
            item = {"text": text.strip(), "cost": cost.strip()} if key == "rules" else text.strip()
            r = BA.world_add(root, key, item)
            show_error(r) if not r["ok"] else st.rerun()
    items = world[key]
    if not items:
        st.caption("（暂无）")
    for idx, item in enumerate(items):
        with st.container(border=True):
            if isinstance(item, dict):
                st.write(item.get("text", ""))
                if item.get("cost"):
                    st.caption("代价：" + item["cost"])
            else:
                st.write(str(item))
            if st.button("删除", key=f"bs_world_del_{key}_{idx}"):
                r = BA.world_remove(root, key, idx)
                show_error(r) if not r["ok"] else st.rerun()


def _world_tab(root: str) -> None:
    res = BA.get_project(root)
    if not res["ok"]:
        show_error(res)
        return
    world = res["data"]["cards"]["world"]
    st.caption("世界观条目直接编辑，立即生效。规则请写明代价，之后的冲突会更好写。")
    for key in _WORLD_LABEL:                       # 所有分类同时显示，按分区排列
        _world_section(root, world, key)
        st.divider()


def _cast_tab(root: str) -> None:
    res = BA.get_project(root)
    if not res["ok"]:
        show_error(res)
        return
    cast = res["data"]["cards"]["cast"]
    with st.expander("➕ 新增角色", expanded=not cast):
        with st.form("bs_cast_add", clear_on_submit=True):
            name = st.text_input("名字", key="bs_cast_new_name")
            role = st.text_input("定位（主角、对手、导师……）", key="bs_cast_new_role")
            want = st.text_input("想要什么", key="bs_cast_new_want")
            need = st.text_input("需要什么（真正需要的）", key="bs_cast_new_need")
            flaw = st.text_input("缺陷", key="bs_cast_new_flaw")
            if st.form_submit_button("添加角色") and name.strip():
                r = BA.cast_add(root, {"name": name.strip(), "role": role, "want": want, "need": need, "flaw": flaw})
                show_error(r) if not r["ok"] else st.rerun()
    if not cast:
        st.caption("还没有角色。")
    for cid, card in cast.items():
        with st.expander(f"{card.get('name', cid)}　·　{card.get('role', '')}"):
            with st.form(f"bs_cast_edit_{cid}"):
                fields = {}
                fields["name"] = st.text_input("名字", value=card.get("name", ""), key=f"bs_ce_name_{cid}")
                fields["role"] = st.text_input("定位", value=card.get("role", ""), key=f"bs_ce_role_{cid}")
                fields["want"] = st.text_input("想要什么", value=card.get("want", ""), key=f"bs_ce_want_{cid}")
                fields["need"] = st.text_input("需要什么", value=card.get("need", ""), key=f"bs_ce_need_{cid}")
                fields["flaw"] = st.text_input("缺陷", value=card.get("flaw", ""), key=f"bs_ce_flaw_{cid}")
                fields["lie"] = st.text_input("相信的谎言", value=card.get("lie", ""), key=f"bs_ce_lie_{cid}")
                fields["arc"] = st.text_input("弧光（起点到终点）", value=card.get("arc", ""), key=f"bs_ce_arc_{cid}")
                rel_text = st.text_area("关系（每行：角色id:说明，例如 char_abc:弟弟）",
                                        value="\n".join(card.get("relations", [])), key=f"bs_ce_rel_{cid}")
                fields["relations"] = [x.strip() for x in rel_text.splitlines() if x.strip()]
                c1, c2 = st.columns(2)
                save = c1.form_submit_button("保存")
                remove = c2.form_submit_button("删除角色")
            if save:
                r = BA.cast_update(root, cid, fields)
                show_error(r) if not r["ok"] else st.rerun()
            if remove:
                r = BA.cast_remove(root, cid)
                show_error(r) if not r["ok"] else st.rerun()


def _remember_check(r) -> None:
    st.session_state["bs_check_last"] = r["data"]["id"]


def _check_tab(root: str) -> None:
    st.caption("质检只报告，不修改任何卡片。结构检查不调用模型；AI 审阅会额外调用一次模型。")
    c1, c2 = st.columns(2)
    if c1.button("▶ 结构检查", key="bs_check_struct"):
        r = BA.run_check(root, use_llm=False)
        show_error(r) if not r["ok"] else _remember_check(r)
    if c2.button("▶ 结构检查 + AI 审阅", key="bs_check_llm"):
        with st.spinner("AI 正在审阅……"):
            r = BA.run_check(root, use_llm=True)
        show_error(r) if not r["ok"] else _remember_check(r)
    hist = BA.check_history(root)
    if not hist["ok"]:
        show_error(hist)
        return
    for rec in hist["data"][:5]:
        n = len(rec["findings"])
        with st.expander(f"{rec['created_at']} · {'含 AI 审阅' if rec['use_llm'] else '结构检查'} · {n} 条",
                         expanded=rec["id"] == st.session_state.get("bs_check_last")):
            if not rec["findings"]:
                st.success("没有发现问题。")
            for f in rec["findings"]:
                mark = "⚠" if f["severity"] == "warn" else "ℹ"
                st.markdown(f"{mark} **{f['issue']}**　`{f['where']}`")
                if f.get("suggestion"):
                    st.caption("建议：" + f["suggestion"])


def _plot_tab(root: str) -> None:
    res = BA.get_project(root)
    if not res["ok"]:
        show_error(res)
        return
    plot = res["data"]["cards"]["plot"]
    st.caption("剧情骨架：幕、转折点、反转与结局候选。直接编辑，立即生效。")
    for i, act in enumerate(plot["acts"]):
        with st.expander(f"第{i + 1}幕　{act.get('title', '')}"):
            for j, beat in enumerate(act.get("beats", [])):
                c1, c2 = st.columns([8, 1])
                c1.write(f"• {beat}")
                if c2.button("×", key=f"bs_beat_del_{i}_{j}"):
                    r = BA.plot_remove_beat(root, i, j)
                    show_error(r) if not r["ok"] else st.rerun()
            with st.form(f"bs_beat_add_{i}", clear_on_submit=True):
                beat = st.text_input("新增转折点", key=f"bs_beat_text_{i}")
                c1, c2 = st.columns(2)
                add = c1.form_submit_button("添加转折点")
                drop = c2.form_submit_button("删除本幕")
            if add and beat.strip():
                r = BA.plot_add_beat(root, i, beat)
                show_error(r) if not r["ok"] else st.rerun()
            if drop:
                r = BA.plot_remove_act(root, i)
                show_error(r) if not r["ok"] else st.rerun()
    with st.form("bs_act_add", clear_on_submit=True):
        title = st.text_input("新增一幕（标题）", key="bs_act_title")
        if st.form_submit_button("添加幕") and title.strip():
            r = BA.plot_add_act(root, title)
            show_error(r) if not r["ok"] else st.rerun()
    for key, label in (("twists", "反转"), ("endings", "结局候选")):
        st.markdown(f"**{label}**")
        for idx, text in enumerate(plot.get(key, [])):
            c1, c2 = st.columns([8, 1])
            c1.write(f"• {text}")
            if c2.button("×", key=f"bs_{key}_del_{idx}"):
                r = BA.plot_remove_item(root, key, idx)
                show_error(r) if not r["ok"] else st.rerun()
        with st.form(f"bs_{key}_add", clear_on_submit=True):
            text = st.text_input(f"新增{label}", key=f"bs_{key}_text")
            if st.form_submit_button(f"添加{label}") and text.strip():
                r = BA.plot_add_item(root, key, text)
                show_error(r) if not r["ok"] else st.rerun()
    _options_section(root)


def _options_section(root: str) -> None:
    st.divider()
    st.markdown("**备选方案**")
    st.caption("为剧情或命题保留另一种走向。对比后可以合并为主线；合并前的主线会保存，可以撤销。")
    with st.form("bs_option_new", clear_on_submit=True):
        name = st.text_input("备选名称（如：开放式结局）", key="bs_opt_name")
        module = st.radio("对应模块", ["plot", "premise"], horizontal=True,
                          format_func=lambda m: "剧情骨架" if m == "plot" else "核心命题", key="bs_opt_module")
        notes = st.text_area("优缺点（可空）", key="bs_opt_notes", height=60)
        if st.form_submit_button("以当前主线新建备选") and name.strip():
            r = BA.option_create(root, name, module, None, notes)
            show_error(r) if not r["ok"] else st.rerun()
    res = BA.option_list(root)
    if not res["ok"]:
        show_error(res)
        return
    for opt in res["data"]:
        label = {"open": "待定", "merged": "已合并为主线", "discarded": "已放弃"}[opt["status"]]
        with st.container(border=True):
            st.markdown(f"**{opt['name']}**　·　{'剧情' if opt['module'] == 'plot' else '命题'}　·　{label}")
            if opt.get("notes"):
                st.caption(opt["notes"])
            d = BA.option_diff(root, opt["oid"])
            if d["ok"]:
                with st.expander("与当前主线的差异"):
                    st.code(d["data"], language="diff")
            else:
                show_error(d)
            cols = st.columns(3)
            if opt["status"] == "open":
                if cols[0].button("合并为主线", key=f"bs_opt_merge_{opt['oid']}"):
                    r = BA.option_merge(root, opt["oid"])
                    show_error(r) if not r["ok"] else st.rerun()
                if cols[1].button("放弃", key=f"bs_opt_drop_{opt['oid']}"):
                    r = BA.option_discard(root, opt["oid"])
                    show_error(r) if not r["ok"] else st.rerun()
            elif opt["status"] == "merged":
                if cols[0].button("撤销合并", key=f"bs_opt_undo_{opt['oid']}"):
                    r = BA.option_undo_merge(root, opt["oid"])
                    show_error(r) if not r["ok"] else st.rerun()


def _handoff_tab(root: str) -> None:
    res = BA.get_project(root)
    if not res["ok"]:
        show_error(res)
        return
    meta, versions = res["data"]["meta"], res["data"]["versions"]
    st.caption("把一个定版版本交接成新小说：题材卡进入向导意图，整包构思作为种子参考。交接后两者不再自动同步。")
    if not versions:
        st.info("还没有定版版本。先在下方定版，再交接。")
        _snapshot_box(root)
        return
    labels = {v["n"]: f"v{v['n']}　{v.get('note') or '（无备注）'}　{v['created_at']}" for v in versions}
    nums = [v["n"] for v in versions]
    chosen = st.selectbox("交接的版本", nums, index=len(nums) - 1, format_func=lambda n: labels[n], key="bs_ho_ver")
    title = st.text_input("新小说的书名", value=meta.get("title", ""), key="bs_ho_title")
    if st.button("▶ 创建小说", key="bs_ho_go", disabled=not title.strip()):
        r = BA.handoff_to_novel(root, version=chosen, title=title.strip())
        if r["ok"]:
            st.session_state["bs_ho_last"] = r["data"]["novel_root"]
            st.success(f"已创建小说「{r['data']['title']}」（来自 v{r['data']['version']}）。")
        else:
            show_error(r)
    if st.session_state.get("bs_ho_last"):
        st.caption("新小说的工作目录：`" + st.session_state["bs_ho_last"] + "`（在书架中可以找到）")
    linked = BA.linked_novels(root)
    if linked["ok"] and linked["data"]:
        st.markdown("**已交接的小说**")
        for item in linked["data"]:
            st.write(f"• {item['title']}（v{item['version']}，{item['at']}）")
    _snapshot_box(root)


def _snapshot_box(root: str) -> None:
    with st.expander("📌 定版当前草稿"):
        note = st.text_input("版本备注", key="bs_snap_note")
        if st.button("定版", key="bs_snap_go"):
            r = BA.snapshot_version(root, note)
            show_error(r) if not r["ok"] else st.rerun()


# ── 🌱 一句话起稿（S4）──

_SEED_KIND_LABEL = {"seed": "起稿", "revise": "修订", "restore": "回退"}


def _plan_value_text(val: Any) -> str:
    if isinstance(val, str):
        return val
    return json.dumps(val, ensure_ascii=False, indent=2)


@st.fragment(run_every=1)
def _live_seed(root: str, run_id: str) -> None:
    res = BA.seed_state(root, run_id)
    if not res["ok"]:
        show_error(res)
        return
    data = res["data"]
    running = data["status"] == "running"
    st.markdown("**任务过程**" + ("（进行中……）" if running else "（已完成）"))
    _render_events(data["events"], key_prefix=f"seed_{run_id}")
    if not running:
        st.session_state["bs_seed_last"] = run_id
        st.session_state.pop("bs_seed_run", None)
        st.rerun(scope="app")


def _finished_seed(root: str, run_id: str) -> None:
    res = BA.seed_state(root, run_id)
    if not res["ok"]:
        return
    data = res["data"]
    with st.expander(f"上一次任务的过程（{len(data['events'])} 条）", expanded=False):
        _render_events(data["events"], key_prefix=f"seed_done_{run_id}")
    if data["status"] == "error":
        st.error("任务失败：" + str(data.get("error", "")))
        return
    if data["status"] != "done":
        return
    r = data["result"]
    # 同一次任务的结果只应用一次：之后的重跑不能把作者正在看的版本拉回最新
    if st.session_state.get("bs_seed_applied") != run_id:
        st.session_state["bs_seed_applied"] = run_id
        st.session_state["bs_seed_view"] = r["ver"]
    if "changed" in r:
        changed = "、".join(SD.SECTION_LABELS[s] for s in r["changed"]) or "无"
        st.success(f"修订完成：已保存为 v{r['ver']:03d}，改动的部分：{changed}。（草稿层，尚未写入卡片）")
        if r.get("affects"):
            names = "、".join(SD.SECTION_LABELS[s] for s in r["affects"])
            st.info(f"模型提示：{names} 可能需要跟着调整（未自动修改，需要的话再发一次反馈）。")
    else:
        st.success(f"起稿完成：已保存为 v{r['ver']:03d}。（草稿层，尚未写入卡片）")


def _seed_start_box(root: str, has_versions: bool, busy: bool) -> None:
    with st.expander("🌱 一句话起稿：从一个简单想法生成整体构思稿", expanded=not has_versions):
        st.caption("一次生成六个部分：核心命题、故事走向、世界观、角色、核心剧情、待定问题。"
                   "只存进草稿层，**不会写入卡片**；每次起稿新增一个版本，旧版本保留。")
        idea = st.text_area("一句话想法", key="bs_seed_idea", height=80, max_chars=2000,
                            placeholder="例如：一个靠典当记忆维持运转的港口城市")
        cons = st.text_input("额外要求（可空）", key="bs_seed_cons", placeholder="例如：要悬疑，不要后宫")
        c1, c2 = st.columns(2)
        tgt = c1.number_input("目标章数（0 = 让模型判断）", min_value=0, max_value=2000, value=0, step=10, key="bs_seed_target")
        struct = c2.selectbox("结构偏好", ["让模型判断", "三幕", "多幕长篇", "群像", "多线并行", "非线性", "连载卷式"],
                              key="bs_seed_struct")
        if st.button("▶ 起稿", key="bs_seed_go", disabled=busy or not idea.strip()):
            r = BA.seed_start(root, idea.strip(), cons.strip(), target_chapters=int(tgt) or None,
                              structure=None if struct == "让模型判断" else struct)
            if r["ok"]:
                st.session_state["bs_seed_run"] = r["data"]["run"]
                st.session_state.pop("bs_seed_last", None)
                st.rerun()
            show_error(r)


def _seed_version_label(v: Dict[str, Any]) -> str:
    when = str(v.get("at", ""))[:16].replace("T", " ")
    return f"v{v['ver']:03d} · {_SEED_KIND_LABEL.get(v.get('kind', ''), v.get('kind', ''))} · {when}"


def _seed_read_box(root: str, versions: list, busy: bool) -> None:
    nums = [v["ver"] for v in versions]
    by_num = {v["ver"]: v for v in versions}
    if st.session_state.get("bs_seed_view") not in nums:
        st.session_state["bs_seed_view"] = nums[-1]
    view = st.selectbox("查看版本", nums, format_func=lambda n: _seed_version_label(by_num[n]), key="bs_seed_view")
    rec = BA.seed_get(root, view)
    if not rec["ok"]:
        show_error(rec)
        return
    with st.container(border=True):
        seed_idea = rec["data"]["seed"].get("idea", "")
        st.caption(f"想法：{seed_idea or '（无）'} · 原因：{rec['data']['reason'] or '（无）'}")
        st.markdown(SD.draft_markdown(rec["data"]["draft"]))

    st.markdown("#### 反馈修订")
    st.caption(f"以 v{view:03d} 为基础修订，新结果追加为新版本。只改选中的部分，其余部分由程序保留原样。")
    secs = st.multiselect("只改这些部分（留空 = 整稿反馈）", list(SD.SECTIONS),
                          format_func=lambda s: SD.SECTION_LABELS[s], key="bs_seed_secs")
    fb = st.text_area("你的反馈", key="bs_seed_fb", height=80, placeholder="例如：更黑暗一点 / 世界观改成架空近未来")
    if st.button("▶ 按反馈修订", key="bs_seed_rev_go", disabled=busy or not fb.strip()):
        r = BA.seed_revise_start(root, fb.strip(), secs or None, base_ver=view)
        if r["ok"]:
            st.session_state["bs_seed_run"] = r["data"]["run"]
            st.session_state.pop("bs_seed_last", None)
            st.rerun()
        show_error(r)

    _seed_compare_box(root, nums, view, busy)
    _seed_adopt_box(root, view, busy)


def _seed_compare_box(root: str, nums: list, view: int, busy: bool) -> None:
    st.markdown("#### 版本对比与回退")
    if len(nums) < 2:
        st.caption("至少两个版本才能对比。")
    else:
        st.session_state.setdefault("bs_seed_da", nums[-2])
        st.session_state.setdefault("bs_seed_db", nums[-1])
        for k in ("bs_seed_da", "bs_seed_db"):
            if st.session_state[k] not in nums:
                st.session_state[k] = nums[-2] if k == "bs_seed_da" else nums[-1]
        c1, c2 = st.columns(2)
        a = c1.selectbox("旧版", nums, format_func=lambda n: f"v{n:03d}", key="bs_seed_da")
        b = c2.selectbox("新版", nums, format_func=lambda n: f"v{n:03d}", key="bs_seed_db")
        if a != b:
            df = BA.seed_diff(root, a, b)
            if not df["ok"]:
                show_error(df)
            else:
                ch = df["data"]["changed"]
                st.caption("改动的部分：" + ("、".join(SD.SECTION_LABELS[s] for s in ch) if ch else "无"))
                for s in ch:
                    with st.expander(SD.SECTION_LABELS[s]):
                        st.code(df["data"]["diffs"][s], language="diff")
    st.button(f"↩ 回退到 v{view:03d}", key="bs_seed_restore", disabled=busy,
              help="把该版本的内容记为一个新版本，历史不删除。",
              on_click=_seed_restore_cb, args=(root, view))
    if st.session_state.get("bs_seed_restore_err"):
        show_error(st.session_state.pop("bs_seed_restore_err"))


def _seed_restore_cb(root: str, view: int) -> None:
    # 回调在控件创建之前执行，才能修改它的 key（在控件之后改会报错）
    r = BA.seed_restore(root, view)
    if r["ok"]:
        st.session_state["bs_seed_view"] = r["data"]["ver"]
    else:
        st.session_state["bs_seed_restore_err"] = r


def _seed_adopt_box(root: str, view: int, busy: bool) -> None:
    st.markdown("#### 采用到卡片（转成待确认提议）")
    st.caption("不会直接写卡片：每个部分生成提议，进入「对话」标签的待确认提议里逐条采纳；卡片已有不同内容的会标冲突。")
    asecs = st.multiselect("采用哪些部分", list(SD.SECTIONS), default=list(SD.SECTIONS),
                           format_func=lambda s: SD.SECTION_LABELS[s], key="bs_seed_asecs")
    c1, c2 = st.columns(2)
    if c1.button("预览采用结果", key="bs_seed_plan_go", disabled=busy or not asecs):
        st.session_state["bs_seed_plan"] = {"ver": view, "sections": list(asecs), "res": BA.seed_adopt_plan(root, view, asecs)}
    plan = st.session_state.get("bs_seed_plan")
    current = plan if plan and plan["ver"] == view and plan["sections"] == list(asecs) else None
    if current:
        pr = current["res"]
        if not pr["ok"]:
            show_error(pr)
            current = None
        else:
            _seed_plan_box(pr["data"])
    ready = bool(current and current["res"]["data"]["items"])
    if c2.button("登记为待确认提议", key="bs_seed_adopt_go", disabled=busy or not ready,
                 type="primary"):
        r = BA.seed_adopt(root, view, list(asecs))
        if r["ok"]:
            d = r["data"]
            st.session_state.pop("bs_seed_plan", None)
            st.success(f"已登记 {len(d['proposals'])} 条待确认提议（冲突 {d['conflicts']} 条）。"
                       f"请到「对话」标签的待确认提议中逐条采纳。")
        else:
            show_error(r)


def _seed_plan_box(p: Dict[str, Any]) -> None:
    items = p["items"]
    conflicts = sum(1 for i in items if i.get("conflict"))
    st.caption(f"预览：将生成 {len(items)} 条提议，其中冲突 {conflicts} 条；跳过 {len(p['skipped'])} 条。")
    if p["skipped"]:
        st.caption("跳过：" + "；".join(p["skipped"][:8]))
    for it in items:
        head = f"{_KIND_LABEL.get(it['module'], it['module'])} · {_OP_LABEL.get(it['op'], it['op'])}"
        if it.get("target") not in (None, ""):
            head += f" · 目标 {it['target']}"
        if it.get("conflict"):
            head += " ⚠ 冲突"
        with st.container(border=True):
            st.markdown(f"**{head}**")
            st.code(_plan_value_text(it["value"]), language=None)
            if it.get("conflict"):
                st.caption(it["conflict"])


def _seed_tab(root: str) -> None:
    res = BA.seed_versions(root)
    if not res["ok"]:
        show_error(res)
        return
    versions = res["data"]["versions"]
    running = st.session_state.get("bs_seed_run")
    _seed_start_box(root, bool(versions), busy=bool(running))
    if running:
        _live_seed(root, running)
        return
    if st.session_state.get("bs_seed_last"):
        _finished_seed(root, st.session_state["bs_seed_last"])
    if not versions:
        st.info("还没有构思稿。先在上方写下一句话想法，再点「起稿」。")
        return
    _seed_read_box(root, versions, busy=False)
    _seed_inputs_box(root)


_INPUT_KIND = {"seed": "起稿", "revise": "修订"}
_INPUT_STATUS = {"submitted": "运行中", "done": "成功", "failed": "失败", "rejected": "未通过校验"}


def _seed_inputs_box(root: str) -> None:
    """输入记录：每次起稿 / 修订的原始输入（不截断）、状态与结果，便于回看。"""
    res = BA.seed_inputs(root)
    if not res["ok"]:
        show_error(res)
        return
    entries = res["data"]["entries"]
    with st.expander(f"📜 输入记录（{len(entries)} 次）"):
        if not entries:
            st.caption("还没有起稿或修订记录。")
            return
        rows = []
        for e in entries:
            inp = e.get("input") or {}
            if e["kind"] == "seed":
                detail = f"想法：{inp.get('idea', '')}"
                if inp.get("constraints"):
                    detail += f"\n额外要求：{inp['constraints']}"
                if inp.get("target_chapters"):
                    detail += f"\n目标章数：{inp['target_chapters']}"
                if inp.get("structure"):
                    detail += f"\n结构偏好：{inp['structure']}"
            else:
                secs = "、".join(SD.SECTION_LABELS.get(s, s) for s in inp.get("sections") or []) or "整稿"
                detail = f"反馈：{inp.get('feedback', '')}\n范围：{secs}\n基于：" + (
                    f"v{inp['base_ver']:03d}" if inp.get("base_ver") else "最新版本")
            result = (f"v{e['ver']:03d}" if e.get("ver") else "") + (f"{e['error']}" if e.get("error") else "")
            rows.append({"提交时间": e.get("submitted_at", ""), "类型": _INPUT_KIND.get(e["kind"], e["kind"]),
                         "输入（完整）": detail, "状态": _INPUT_STATUS.get(e["status"], e["status"]),
                         "结果": result or "—"})
        st.dataframe(rows, width="stretch", hide_index=True)


def page_brainstorm() -> None:
    st.header("💡 灵感构思工作室")
    st.caption("独立于小说项目：先把灵感、题材、世界观与剧情打磨成构思包，之后再据此创建小说。")
    _project_picker()
    root = _current_root()
    if not root:
        return
    tabs = st.tabs(["对话", "题材卡", "灵感池", "发散", "世界观", "角色", "剧情", "质检", "交接", "🌱 起稿"])
    with tabs[0]:
        _dialogue_tab(root)
    with tabs[1]:
        _brief_tab(root)
    with tabs[2]:
        _ideas_tab(root)
    with tabs[3]:
        _spark_tab(root)
    with tabs[4]:
        _world_tab(root)
    with tabs[5]:
        _cast_tab(root)
    with tabs[6]:
        _plot_tab(root)
    with tabs[7]:
        _check_tab(root)
    with tabs[8]:
        _handoff_tab(root)
    with tabs[9]:
        _seed_tab(root)
