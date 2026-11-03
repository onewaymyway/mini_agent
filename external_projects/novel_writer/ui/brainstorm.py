"""💡 灵感构思工作室（独立入口）。B1：题材卡、灵感池、发散。对话与提议在 B2 加入。"""

from __future__ import annotations

import streamlit as st

from novel_writer.brainstorm import api as BA
from novel_writer.brainstorm import chat as CH
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
_OP_LABEL = {"add": "新增", "set": "修改"}


def _dialogue_tab(root: str) -> None:
    hist = BA.chat_history(root, limit=40)
    if not hist["ok"]:
        show_error(hist)
        return
    top = st.columns([3, 1])
    mode = top[0].radio("模式", list(CH.MODES), horizontal=True, key="bs_chat_mode")
    if top[1].button("新开对话", key="bs_chat_new", help="丢弃续接的会话，之后不再记得之前的内容（记录保留）"):
        BA.chat_new(root)
        st.rerun()
    for rec in hist["data"]:
        role = "user" if rec["role"] == "user" else "assistant"
        with st.chat_message(role):
            st.write(rec["text"])
    text = st.chat_input("说点什么……（例如：我想写一个会吞噬记忆的港口）", key="bs_chat_input")
    if text:
        with st.spinner("思考中……"):
            r = BA.chat_send(root, text, mode)
        show_error(r) if not r["ok"] else st.rerun()
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
            st.json(item["value"], expanded=False)
            if item.get("reason"):
                st.caption("理由：" + item["reason"])
            if item.get("error"):
                st.warning("上次采纳失败：" + item["error"])
            c1, c2 = st.columns(2)
            if c1.button("采纳", key=f"bs_acc_{item['pid']}"):
                r = BA.accept_proposal(root, item["pid"])
                show_error(r) if not r["ok"] else st.rerun()
            if c2.button("拒绝", key=f"bs_rej_{item['pid']}"):
                r = BA.reject_proposal(root, item["pid"])
                show_error(r) if not r["ok"] else st.rerun()


_WORLD_LABEL = {"rules": "规则", "locations": "地点", "factions": "势力", "timeline": "时间线", "taboos": "禁忌"}


def _world_tab(root: str) -> None:
    res = BA.get_project(root)
    if not res["ok"]:
        show_error(res)
        return
    world = res["data"]["cards"]["world"]
    st.caption("世界观条目直接编辑，立即生效。规则请写明代价，之后的冲突会更好写。")
    key = st.selectbox("分类", list(_WORLD_LABEL), format_func=lambda k: _WORLD_LABEL[k], key="bs_world_kind")
    with st.form(f"bs_world_add_{key}", clear_on_submit=True):
        text = st.text_area(f"新增{_WORLD_LABEL[key]}", key=f"bs_world_text_{key}", height=70)
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


def page_brainstorm() -> None:
    st.header("💡 灵感构思工作室")
    st.caption("独立于小说项目：先把灵感、题材、世界观与剧情打磨成构思包，之后再据此创建小说。")
    _project_picker()
    root = _current_root()
    if not root:
        return
    tabs = st.tabs(["对话", "题材卡", "灵感池", "发散", "世界观", "角色", "剧情", "质检", "交接"])
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
