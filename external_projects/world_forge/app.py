"""架空世界工坊 · Streamlit 看板（端口 8509）。

启动：streamlit run app.py --server.port 8509
所有写操作都经 engine / proposals，界面不直接改数据文件。
"""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

PROJECT_ROOT = Path(__file__).resolve().parent
for _p in (PROJECT_ROOT, *PROJECT_ROOT.parents[:2]):
    _src = _p / "src"
    if (_src / "mini_agent").is_dir() and str(_src) not in sys.path:
        sys.path.insert(0, str(_src))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from world_forge import engine, gaps, graph as graph_mod, proposals, repo, runlog as runlog_mod, store, timeline  # noqa: E402
from world_forge.providers.factory import make_image_provider, make_text_llm  # noqa: E402
from world_forge.calendar import (MINUTES_PER_UNIT, UNIT_LABELS, clock_minutes, format_time,  # noqa: E402
                                  step_label, validate_step)

st.set_page_config(page_title="架空世界工坊", page_icon="🌍", layout="wide")
st.markdown("""<style>
.block-container {padding-top: 4.5rem;}
.wf-card {border:1px solid #2a3350;border-radius:10px;padding:12px 14px;background:#141a2b;}
.wf-tag {display:inline-block;padding:1px 8px;border-radius:999px;font-size:12px;margin-right:4px;}
</style>""", unsafe_allow_html=True)


@st.cache_resource(show_spinner=False)
def _providers():
    llm, llm_note = make_text_llm()
    img, img_note = make_image_provider()
    return llm, llm_note, img, img_note


LLM, LLM_NOTE, IMG, IMG_NOTE = _providers()
STATUS_LABEL = {"applied": "已写入", "pending": "待确认", "blocked": "已拦截", "rejected": "已拒绝"}
STATUS_COLOR = {"applied": "#2e7d5b", "pending": "#b7791f", "blocked": "#9b2c2c", "rejected": "#555"}


def _tag(status: str) -> str:
    return (f"<span class='wf-tag' style='background:{STATUS_COLOR.get(status, '#444')}'>"
            f"{STATUS_LABEL.get(status, status)}</span>")


def _safe(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001 - 看板把错误直接呈现给用户
        st.error(f"{type(exc).__name__}：{exc}")
        return None


# ---------- 侧边栏：选择世界

worlds = repo.list_world_ids()
titles = {w: repo.load_meta(w)["title"] for w in worlds}
# 当前世界存放在 current_wid（不作为控件的 key）：控件实例化之后不能再直接改它的 session_state 键。
if st.session_state.get("current_wid") not in worlds:
    st.session_state.current_wid = worlds[0] if worlds else None

with st.sidebar:
    st.header("🌍 世界")
    if worlds:
        chosen = st.selectbox("当前世界", worlds, index=worlds.index(st.session_state.current_wid),
                              format_func=lambda w: f"{titles[w]}（{w}）")
        st.session_state.current_wid = chosen
    else:
        st.info("还没有世界，请到「新建」创建。")
    st.caption(f"文本：{LLM_NOTE}")
    st.caption(f"图像：{IMG_NOTE}")

wid = st.session_state.current_wid

tabs = st.tabs(["🆕 新建", "📊 总览", "📚 设定与提案", "👤 角色", "🕰️ 时间线", "⏩ 推进", "🎞️ 故事与漫画", "⚙️ 设置"])

# ---------- 新建

with tabs[0]:
    st.subheader("从一句概念开始")
    with st.form("create_world"):
        concept = st.text_area("初始概念", placeholder="例：蒸汽与符文并存的海上帝国，500 年前的大崩塌")
        c1, c2, c3 = st.columns(3)
        title = c1.text_input("标题（可空，由模型生成）")
        genre = c2.text_input("题材", placeholder="奇幻 / 科幻 / 架空历史 …")
        tone = c3.text_input("基调", placeholder="悲壮 / 轻快 / 冷峻 …")
        go = st.form_submit_button("生成宪章草案", type="primary")
    if go:
        with st.spinner("正在生成宪章与首批设定……"):
            res = _safe(engine.create_world, LLM, concept=concept, title=title, genre=genre, tone=tone)
        if res:
            st.session_state.current_wid = res["world_id"]
            st.success(f"已创建世界 {res['world_id']}。宪章需要在「设定与提案」中批准后才能推进历史。")
            st.rerun()

# ---------- 以下页面都需要选中世界

if not wid:
    for t in tabs[1:]:
        with t:
            st.info("请先在「新建」中创建世界。")
    st.stop()

meta = repo.load_meta(wid)
entities = repo.load_entities(wid)
characters = repo.load_characters(wid)
proposals_all = repo.load_proposals(wid)
pending = [p for p in proposals_all if p["status"] == "pending"]
gap_list = gaps.find_gaps(wid)
branch_name = repo.load_branches(wid).get(meta["active_branch"], {}).get("name", meta["active_branch"])

# ---------- 总览

with tabs[1]:
    st.subheader(meta["title"])
    st.caption(f"{wid} · 状态：{'宪章已确认' if meta.get('charter') else '宪章待确认'} · 当前分支：{branch_name}")
    cols = st.columns(5)
    cols[0].metric("当前时间", format_time(clock_minutes(meta)))
    cols[1].metric("设定实体", len(entities))
    cols[2].metric("角色", len(characters))
    cols[3].metric("待确认提案", len(pending))
    cols[4].metric("设定缺口", len(gap_list))
    st.markdown("#### 世界观简介")
    ov = engine.get_overview(wid)
    if ov:
        stale = ov.get("minutes") != clock_minutes(meta)
        st.caption(f"生成于 {ov.get('generated_at', '')}（当时 {ov.get('time', '')}）"
                   + ("　· 世界已推进，建议重新生成" if stale else ""))
        with st.container(border=True):
            st.markdown(ov["text"])
    else:
        st.caption("还没有世界观简介。宪章确认后可生成，用于快速了解当前世界观。")
    can_overview = bool(meta.get("charter"))
    if st.button("重新生成世界观简介" if ov else "生成世界观简介", type="primary", disabled=not can_overview,
                 key="gen_overview"):
        with st.spinner("正在撰写世界观简介……"):
            res = _safe(engine.generate_overview, LLM, wid)
        if res:
            st.rerun()
    if meta.get("charter"):
        ch = meta["charter"]
        st.markdown(f"**题材** {ch.get('genre', '')}　**基调** {ch.get('tone', '')}")
        st.write(ch.get("summary", ""))
        st.markdown("**世界硬规则**")
        for rule in ch.get("hard_rules") or []:
            st.markdown(f"- {rule}")
    else:
        st.warning("宪章尚未确认。请到「设定与提案」批准 set_charter 提案。")

# ---------- 设定与提案

with tabs[2]:
    st.markdown("#### 扩展世界观")
    e1, e2 = st.columns(2)
    with e1:
        st.caption("自动完善：优先补全缺口，再主动新增少量实体与关系。")
        if st.button("自动完善世界观", type="primary", disabled=not meta.get("charter"), key="enrich_btn"):
            with st.spinner("正在完善世界观……"):
                res = _safe(engine.enrich_world, LLM, wid)
            if res:
                st.session_state.expand_report = res
                st.rerun()
    with e2:
        with st.form("request_expand", clear_on_submit=True):
            req = st.text_area("按要求扩展", placeholder="例：北方增加一个以冰原为家的游牧部族，与帝国保持距离")
            cnt = st.number_input("最多新增实体数", min_value=1, max_value=engine.MAX_EXPAND_COUNT, value=3)
            if st.form_submit_button("按要求扩展", disabled=not meta.get("charter")) and req.strip():
                with st.spinner("正在扩展……"):
                    res = _safe(engine.expand_world, LLM, wid, request=req, count=int(cnt))
                if res:
                    st.session_state.expand_report = {"expand": res}
                    st.rerun()
    rep = st.session_state.get("expand_report")
    if rep:
        with st.container(border=True):
            if "fill" in rep:
                st.markdown(f"**补全缺口**：{len(rep['fill']['filled'])} 个，剩余 {rep['fill']['remaining']} 个")
            ex = rep["expand"]
            st.markdown("**扩展结果**：" + ("　".join(f"{STATUS_LABEL.get(k, k)} {v}" for k, v in ex["counts"].items())
                                          or "没有新增内容"))
            for r in ex["records"]:
                head = r["payload"].get("name") or r["payload"].get("title") or r["op"]
                st.markdown(f"{_tag(r['status'])} `{r['op']}` {head}", unsafe_allow_html=True)
                if r.get("reasons"):
                    st.caption("；".join(r["reasons"]))
            for sk in ex["skipped"]:
                st.caption(f"跳过：{sk}")
            if st.button("收起结果", key="close_expand_report"):
                del st.session_state["expand_report"]
                st.rerun()
    st.caption("新增内容遵循同一套规则：次要内容自动写入，重大内容进入下方待确认队列，冲突内容被拦截。")
    st.divider()

    st.markdown("#### 按要求修改设定")
    obj_opts = {e["id"]: f"{e['name']}（{e['kind']}）" for e in entities}
    obj_opts.update({c["id"]: f"{c['name']}（角色）" for c in characters})
    sel_ids = st.multiselect("选择要修改的对象（不选则由系统自动判断涉及哪些对象）", list(obj_opts),
                             format_func=lambda k: obj_opts[k], key="mod_targets")
    with st.form("modify_form"):
        req = st.text_area("修改要求", placeholder="例：把北港改为冬季封冻，只能靠破冰船通行；让老兵在第三年死亡")
        if st.form_submit_button("提交修改", type="primary", disabled=not meta.get("charter")):
            if not req.strip():
                st.warning("请先填写修改要求。")
            else:
                with st.spinner("正在分析并修改……"):
                    res = _safe(engine.modify_world, LLM, wid, req, list(sel_ids) or None)
                if res:
                    st.session_state.modify_report = res
                    st.rerun()
    mrep = st.session_state.get("modify_report")
    if mrep:
        with st.container(border=True):
            if not mrep["results"]:
                st.info("没有需要修改的内容。")
            else:
                st.markdown("**修改结果**：" + "　".join(f"{STATUS_LABEL.get(k, k)} {v}"
                                                  for k, v in mrep["counts"].items()))
            for r in mrep["results"]:
                st.markdown(f"**{r['target']}** {_tag(r['proposal']['status'])}", unsafe_allow_html=True)
                if r["reason"]:
                    st.caption(f"原因：{r['reason']}")
                for k in r["after"]:
                    st.markdown(f"- `{k}`：{r['before'].get(k) or '（空）'} → {r['after'][k]}")
                if r["proposal"].get("reasons"):
                    st.caption("；".join(r["proposal"]["reasons"]))
            for sk in mrep["skipped"]:
                st.caption(f"跳过「{sk['target']}」：{sk['reason']}")
            if st.button("收起结果", key="close_modify_report"):
                del st.session_state["modify_report"]
                st.rerun()
    st.divider()

    left, right = st.columns([3, 2])
    with left:
        st.markdown("#### 待确认提案")
        brep = st.session_state.get("batch_report")
        if brep:
            with st.container(border=True):
                counts = {}
                for r in brep["results"]:
                    counts[r["status"]] = counts.get(r["status"], 0) + 1
                st.markdown(f"**{brep['action']}结果**：" + "　".join(
                    f"{STATUS_LABEL.get(k, '失败' if k == 'error' else k)} {v}" for k, v in counts.items()))
                for r in brep["results"]:
                    if r["status"] == "applied":
                        continue
                    detail = r["error"] or "；".join(r["reasons"]) or "未处理"
                    st.caption(f"{r['op'] or '提案'} {r['id']}：{detail}")
                if st.button("收起结果", key="close_batch_report"):
                    del st.session_state["batch_report"]
                    st.rerun()
        if not pending:
            st.caption("没有待确认的提案。")
        else:
            chosen = [p["id"] for p in pending if st.session_state.get(f"sel_{p['id']}")]
            confirm_all = st.checkbox(f"我已查看全部 {len(pending)} 条待确认提案，确认可以全部处理",
                                      key="confirm_all_pending")
            b1, b2, b3, b4 = st.columns(4)
            if b1.button(f"批准所选（{len(chosen)}）", type="primary", disabled=not chosen, key="batch_ap_sel"):
                with st.spinner("正在批准……"):
                    res = proposals.approve_many(wid, chosen)
                st.session_state.batch_report = {"action": "批准所选", "results": res}
                st.rerun()
            if b2.button(f"拒绝所选（{len(chosen)}）", disabled=not chosen, key="batch_rj_sel"):
                res = proposals.reject_many(wid, chosen)
                st.session_state.batch_report = {"action": "拒绝所选", "results": res}
                st.rerun()
            if b3.button(f"全部批准（{len(pending)}）", disabled=not confirm_all, key="batch_ap_all"):
                with st.spinner("正在批准全部提案……"):
                    res = proposals.approve_many(wid, [p["id"] for p in pending])
                st.session_state.batch_report = {"action": "全部批准", "results": res}
                st.rerun()
            if b4.button(f"全部拒绝（{len(pending)}）", disabled=not confirm_all, key="batch_rj_all"):
                res = proposals.reject_many(wid, [p["id"] for p in pending])
                st.session_state.batch_report = {"action": "全部拒绝", "results": res}
                st.rerun()
            st.caption("批准会真正写入设定，请先勾选确认再使用「全部」按钮。批准按列表顺序逐条检查，"
                       "后面的提案会基于前面已写入的结果进行一致性检查。")
            for p in pending:
                with st.container(border=True):
                    head = p["payload"].get("name") or p["payload"].get("title") or p["payload"].get("summary", "")[:30]
                    top = st.columns([1, 9])
                    top[0].checkbox("选", key=f"sel_{p['id']}", label_visibility="collapsed")
                    top[1].markdown(f"**{p['op']}** · {head} {_tag(p['status'])}", unsafe_allow_html=True)
                    st.caption(f"来源：{p['source']} · 风险：{p.get('risk')}")
                    with st.expander("查看内容"):
                        st.json(p["payload"])
                    ia, ir = st.columns(2)
                    if ia.button("批准", key=f"ap_{p['id']}"):
                        _safe(proposals.approve, wid, p["id"])
                        st.rerun()
                    if ir.button("拒绝", key=f"rj_{p['id']}"):
                        _safe(proposals.reject, wid, p["id"])
                        st.rerun()
        st.markdown("#### 最近被拦截或处理的提案")
        recent = [p for p in proposals_all if p["status"] in ("blocked", "applied", "rejected")][-12:][::-1]
        for p in recent:
            st.markdown(f"{_tag(p['status'])} `{p['op']}` {p['payload'].get('name') or p['payload'].get('title', '')}",
                        unsafe_allow_html=True)
            if p.get("reasons"):
                st.caption("；".join(p["reasons"]))
    with right:
        st.markdown("#### 实体关系")
        min_deg = st.number_input("每个实体的目标关系数", min_value=1, max_value=6, value=2, key="rel_min_deg")
        if st.button("自动补全实体关系", disabled=not meta.get("charter") or len(entities) < 2,
                     key="complete_rel_btn"):
            with st.spinner("正在为实体建立关系……"):
                res = _safe(engine.complete_relations, LLM, wid, limit=5, min_degree=int(min_deg))
            if res:
                st.session_state.rel_report = res
                st.rerun()
        rr = st.session_state.get("rel_report")
        if rr:
            st.info(f"本次处理 {rr['processed']} 个实体（目标：每个实体至少 {rr['min_degree']} 条关系）。"
                    + ("新增关系：" + "　".join(f"{STATUS_LABEL.get(k, k)} {v}" for k, v in rr["counts"].items())
                       if rr["counts"] else "本次没有新增关系。")
                    + (f"　仍有 {rr['remaining']} 个实体关系不足，可再次点击。" if rr["remaining"]
                       else "　所有实体都已达到目标。"))
            for sk in rr["skipped"]:
                st.caption(sk)
            if st.button("收起结果", key="close_rel_report"):
                del st.session_state["rel_report"]
                st.rerun()
        st.markdown("#### 设定缺口")
        if st.button("自动补全前 5 个可处理的缺口", type="secondary", disabled=not gap_list):
            with st.spinner("正在补全……"):
                res = _safe(engine.fill_gaps, LLM, wid, limit=5)
            if res:
                st.session_state.fill_report = res
                st.rerun()
        rep = st.session_state.get("fill_report")
        if rep:
            st.info(f"本次补全 {len(rep['filled'])} 个缺口（可处理共 {rep['fixable_total']} 个），"
                    f"剩余缺口 {rep['remaining']} 个。")
            for sk in rep["skipped"]:
                st.caption(f"跳过「{sk['gap']['label']}」：{sk['reason']}")
            if st.button("收起结果", key="close_fill_report"):
                del st.session_state["fill_report"]
                st.rerun()
        for g in gap_list[:30]:
            c1, c2 = st.columns([4, 1])
            c1.write(g["label"])
            if g["kind"] == "missing_summary" and c2.button("补全", key=f"gf_{g['id']}"):
                with st.spinner("补全中……"):
                    _safe(engine.fill_gaps, LLM, wid, gap_ids=[g["id"]], limit=1)
                st.rerun()
        st.markdown("#### 手动添加设定")
        with st.form("manual_entity", clear_on_submit=True):
            kind = st.selectbox("类别", proposals.KINDS)
            name = st.text_input("名称")
            summary = st.text_area("描述")
            if st.form_submit_button("提交"):
                rec = _safe(proposals.submit, wid, "create_entity",
                            payload={"kind": kind, "name": name, "summary": summary}, source="user")
                if rec:
                    st.write(_tag(rec["status"]), unsafe_allow_html=True)
                    if rec["reasons"]:
                        st.warning("；".join(rec["reasons"]))

# ---- 全宽：实体一览 + 关系图（放在“设定与提案”标签页底部，跨整个页面宽度）
with tabs[2]:
    st.divider()
    st.markdown("#### 实体一览")
    if entities:
        st.dataframe([{"类别": e["kind"], "名称": e["name"], "描述": e.get("summary", ""),
                       "来源": e.get("source")} for e in entities],
                     use_container_width=True, hide_index=True, height=min(640, 60 + 35 * len(entities)))
    else:
        st.caption("还没有实体。")

    st.markdown("#### 关系图")
    st.caption("节点为设定实体与角色，连线为关系；角色与所属势力之间自动连线为「隶属」。")
    graph_all = graph_mod.build_graph(wid)
    focus_opts = {"": "全部"}
    focus_opts.update({n["id"]: f"{n['label']}（{n['group']}）" for n in graph_all["nodes"]})
    gc1, gc2 = st.columns([3, 1])
    focus = gc1.selectbox("聚焦某个节点（显示它的邻居关系）", list(focus_opts), format_func=lambda k: focus_opts[k],
                          key="graph_focus")
    depth = gc2.number_input("邻居层数", min_value=1, max_value=3, value=1, key="graph_depth")
    graph = graph_mod.build_graph(wid, focus=focus or None, depth=int(depth))
    components.html(graph_mod.render_html(graph, height=640), height=660, scrolling=False)

# ---------- 角色

with tabs[3]:
    st.markdown("#### 角色")
    if not characters:
        st.caption("暂无角色。推进历史或批准角色提案后会出现在这里。")
    ent_name = {e["id"]: e["name"] for e in entities}
    for c in characters:
        with st.container(border=True):
            c1, c2 = st.columns([3, 1])
            fac = ent_name.get(c.get("faction_id")) or c.get("attrs", {}).get("faction_hint") or "—"
            c1.markdown(f"**{c['name']}** · {c.get('role')} · 势力：{fac} · 状态：{c.get('status')}")
            c1.write(c.get("summary", ""))
            pth = repo.portrait_path(wid, c["id"])
            if pth.is_file():
                c2.image(str(pth), width=140)
            if c.get("status") == "alive" and c2.button("生成立绘", key=f"pt_{c['id']}"):
                with st.spinner("生成立绘……"):
                    res = _safe(engine.make_portrait, LLM, IMG, wid, c["id"])
                if res and not res["ok"]:
                    st.warning(f"立绘失败：{res['error']}")
                st.rerun()

# ---------- 时间线

with tabs[4]:
    branches = timeline.list_branches(wid)
    st.markdown("#### 分支")
    bl, br = st.columns([2, 3])
    with bl:
        labels = {b["id"]: f"{'▶ ' if b['active'] else ''}{b['name']}（{b['events']} 个事件）" for b in branches}
        chosen = st.radio("切换到分支", list(labels), format_func=lambda k: labels[k],
                          index=[b["id"] for b in branches].index(meta["active_branch"]))
        if chosen != meta["active_branch"]:
            _safe(timeline.switch, wid, chosen)
            st.rerun()
    with br:
        events = repo.load_events(wid, meta["active_branch"])
        with st.form("fork_branch"):
            fname = st.text_input("新分支名称", placeholder="例：如果当年选择了另一条路")
            opts = [None] + [e["id"] for e in events]
            at = st.selectbox("从哪个事件之后分叉（None 表示从末尾）", opts,
                              format_func=lambda k: "末尾" if k is None else
                              next(f"{e['t']} · {e['title']}" for e in events if e["id"] == k))
            if st.form_submit_button("开新分支并切换"):
                bid = _safe(timeline.fork, wid, fname or "新分支", at)
                if bid:
                    st.rerun()
    st.markdown("#### 当前分支事件")
    if not events:
        st.caption("该分支还没有事件。")
    for e in events:
        st.markdown(f"**{e['t']} · {e['title']}**")
        st.write(e.get("summary", ""))
    if len(branches) > 1:
        st.markdown("#### 分支对比")
        a, b = st.columns(2)
        ids = [x["id"] for x in branches]
        ca = a.selectbox("分支 A", ids, format_func=lambda k: labels.get(k, k), key="cmp_a")
        cb = b.selectbox("分支 B", ids, index=min(1, len(ids) - 1), format_func=lambda k: labels.get(k, k), key="cmp_b")
        if ca != cb:
            res = timeline.compare(wid, ca, cb)
            st.write(f"共同历史 {res['common']} 个事件；A 独有 {len(res['only_a'])} 个，B 独有 {len(res['only_b'])} 个。")
            x1, x2 = st.columns(2)
            x1.markdown("**A 独有**")
            for e in res["only_a"]:
                x1.markdown(f"- {e['t']} {e['title']}")
            x2.markdown("**B 独有**")
            for e in res["only_b"]:
                x2.markdown(f"- {e['t']} {e['title']}")

# ---------- 推进

with tabs[5]:
    st.markdown("#### 推进历史")
    if not meta.get("charter"):
        st.warning("宪章未确认，暂不能推进。")
    else:
        steps = st.number_input("推进步数", min_value=1, max_value=10, value=1)
        tc1, tc2 = st.columns(2)
        amt = tc1.number_input("每步时长", min_value=1, max_value=100000,
                               value=int(meta["settings"]["step_amount"]), key="adv_amount")
        unit_keys = list(UNIT_LABELS)
        unit = tc2.selectbox("时间单位", unit_keys, index=unit_keys.index(meta["settings"]["step_unit"]),
                             format_func=lambda u: UNIT_LABELS[u], key="adv_unit")
        st.caption(f"本次每步 {step_label(int(amt), unit)}（默认取世界设置，可在此临时调整）。"
                   f"当前时间：{format_time(clock_minutes(meta))}。"
                   "推进过程会实时显示在下方日志中，并保存到本世界的运行日志。")
        if st.button("推进", type="primary"):
            box = st.status("正在推进……", expanded=True)
            sink = lambda e: box.text(runlog_mod.format_entry(e))  # noqa: E731
            try:
                res = engine.advance(LLM, wid, int(steps), log=runlog_mod.RunLog(wid, "advance", sink=sink),
                                     amount=int(amt), unit=unit)
                box.update(label="推进完成", state="complete", expanded=False)
                st.session_state.advance_report = res
            except Exception as exc:  # noqa: BLE001
                box.update(label="推进失败", state="error", expanded=True)
                st.error(f"{type(exc).__name__}：{exc}")
                st.session_state.pop("advance_report", None)
        rep = st.session_state.get("advance_report")
        if rep:
            st.markdown("**本次推进结果**")
            for s_ in rep["steps"]:
                st.markdown(f"{s_['time']}　" + "　".join(
                    f"{_tag(k)} {v}" for k, v in s_["counts"].items()), unsafe_allow_html=True)
                for r in s_["records"]:
                    head = r["payload"].get("title") or r["payload"].get("name") or r["op"]
                    st.markdown(f"{_tag(r['status'])} `{r['op']}` {head}", unsafe_allow_html=True)
                    if r.get("reasons"):
                        st.caption("；".join(r["reasons"]))
            if st.button("收起结果", key="close_advance_report"):
                del st.session_state["advance_report"]
                st.rerun()

    st.markdown("#### 运行日志")
    runs = runlog_mod.list_runs(wid)
    if not runs:
        st.caption("还没有运行记录。")
    else:
        labels = {r["path"]: f"{r['stamp']} · {'推进' if r['kind'] == 'advance' else r['kind']}" for r in runs}
        chosen = st.selectbox("选择一次运行", list(labels), format_func=lambda k: labels[k], key="run_pick")
        entries = runlog_mod.read_run(chosen)
        lvl_count = {}
        for e in entries:
            lvl_count[e.get("level", "info")] = lvl_count.get(e.get("level", "info"), 0) + 1
        st.caption(f"共 {len(entries)} 条记录：" + "　".join(f"{k} {v}" for k, v in lvl_count.items()))
        st.code("\n".join(runlog_mod.format_entry(e) for e in entries), language=None)

# ---------- 故事与漫画

with tabs[6]:
    evs = repo.load_events(wid, meta["active_branch"])
    st.markdown("#### 从事件生成小故事线")
    ev_labels = {e["id"]: f"{e['t']} · {e['title']}" for e in evs}
    chosen_ev = st.multiselect("选择事件", list(ev_labels), format_func=lambda k: ev_labels[k])
    if st.button("生成小故事线", disabled=not chosen_ev or not meta.get("charter")):
        with st.spinner("整理故事线……"):
            sid = _safe(engine.make_arc, LLM, wid, event_ids=chosen_ev)
        if sid:
            st.session_state.story = sid
            st.rerun()
    st.markdown("#### 直接创作（不经过时间线）")
    with st.form("direct_story"):
        brief = st.text_area("描述一个小事件或一段创作想法")
        form = st.radio("形式", ["novel", "notes"], horizontal=True,
                        format_func=lambda x: "短篇小说" if x == "novel" else "故事说明")
        if st.form_submit_button("创作短篇") and brief.strip():
            with st.spinner("创作中……"):
                sid = _safe(engine.write_short, LLM, wid, brief=brief, form=form)
            if sid:
                st.session_state.story = sid
                st.rerun()
    stories = repo.list_story_ids(wid)
    st.markdown("#### 故事")
    if not stories:
        st.caption("还没有故事。")
    else:
        sid = st.selectbox("选择故事", stories, index=stories.index(st.session_state.get("story", stories[-1]))
                           if st.session_state.get("story") in stories else len(stories) - 1,
                           format_func=lambda s: f"{repo.load_story_meta(wid, s)['title']}（{repo.load_story_meta(wid, s)['kind']}）")
        smeta = repo.load_story_meta(wid, sid)
        d = repo.story_dir(wid, sid)
        a1, a2, a3, a4 = st.columns(4)
        if smeta["kind"] == "arc" and a1.button("成文短篇"):
            with st.spinner("成文中……"):
                new = _safe(engine.write_short, LLM, wid, arc_sid=sid)
            if new:
                st.session_state.story = new
                st.rerun()
        if a2.button("生成分镜", disabled=smeta["kind"] == "arc" and not (d / "arc.json").is_file()):
            with st.spinner("绘制分镜脚本……"):
                _safe(engine.make_storyboard, LLM, wid, sid)
            st.rerun()
        if a3.button("生成漫画图", disabled=not (d / "storyboard.json").is_file()):
            with st.spinner("正在生成分镜图（可能需要数分钟）……"):
                res = _safe(engine.render_comic, IMG, wid, sid)
            if res:
                st.success(f"完成：成功 {res['ok']} 格，失败 {res['failed']} 格（{res['provider']}）")
                st.rerun()
        if a4.button("导出给 novel_writer"):
            out = _safe(engine.export_bundle, wid, sid)
            if out:
                st.success(f"已导出到 {out}")
        if (d / "arc.json").is_file():
            arc = store.read_json(d / "arc.json")
            st.markdown(f"**{arc['title']}**　{arc.get('premise', '')}")
            for act in arc.get("acts") or []:
                st.markdown(f"- **{act.get('title', '')}**：{act.get('summary', '')}")
        if (d / "story.md").is_file():
            with st.expander("正文", expanded=smeta["kind"] == "short"):
                st.markdown((d / "story.md").read_text(encoding="utf-8"))
        board = store.read_json(d / "storyboard.json")
        if board:
            st.markdown("#### 漫画")
            if board.get("stub"):
                st.caption("当前为占位图，未调用真实图像模型。")
            for pg in board["pages"]:
                st.markdown(f"**第 {pg['no']} 页**")
                cols = st.columns(min(3, len(pg["panels"])))
                for i, pn in enumerate(pg["panels"]):
                    with cols[i % len(cols)]:
                        if pn.get("image") and (d / pn["image"]).is_file():
                            st.image(str(d / pn["image"]))
                        else:
                            st.caption(f"（未生成画面：{pn.get('image_error') or '—'}）")
                        if pn.get("narration"):
                            st.caption(pn["narration"])
                        for dl in pn.get("dialogue") or []:
                            st.markdown(f"**{dl.get('speaker', '')}**：{dl.get('text', '')}")

# ---------- 设置

with tabs[7]:
    st.markdown("#### 世界设置")
    st.caption("这些设置随世界保存，只影响之后的写入与推进。")
    with st.form("settings"):
        auto_minor = st.toggle("次要设定自动写入（地名、物品、次要角色等）",
                               value=bool(meta["settings"].get("auto_minor", True)))
        sc1, sc2 = st.columns(2)
        step_amt = sc1.number_input("默认每步时长", min_value=1, max_value=100000,
                                    value=int(meta["settings"]["step_amount"]))
        unit_keys = list(UNIT_LABELS)
        step_unit = sc2.selectbox("默认时间单位", unit_keys, index=unit_keys.index(meta["settings"]["step_unit"]),
                                  format_func=lambda u: UNIT_LABELS[u])
        st.caption("历法：1 年 = 12 月，1 月 = 30 天，1 星期 = 7 天（周一至周日）。")
        recent = st.number_input("推进时参考的近期事件数", min_value=1, max_value=50,
                                 value=int(meta["settings"].get("recent_events", 8)))
        if st.form_submit_button("保存设置"):
            m = repo.load_meta(wid)
            try:
                amount_v, unit_v = validate_step(step_amt, step_unit)
            except ValueError as exc:
                st.error(str(exc))
                st.stop()
            m["settings"].update(auto_minor=auto_minor, step_amount=amount_v, step_unit=unit_v,
                                 recent_events=int(recent))
            repo.save_meta(m)
            st.success("已保存。")
            st.rerun()
    st.markdown("#### 危险操作：删除世界")
    st.caption("将永久删除该世界的全部数据（设定、角色、时间线、分支、提案、故事、导出），无法恢复。")
    with st.expander("删除当前世界"):
        typed = st.text_input(f"输入世界 id「{wid}」以确认", key="delete_confirm_input")
        if st.button("永久删除该世界", type="primary", disabled=typed != wid, key="delete_world_btn"):
            res = _safe(engine.delete_world, wid)
            if res:
                remaining = repo.list_world_ids()
                st.session_state.current_wid = remaining[0] if remaining else None
                st.success(f"已删除「{res['title']}」。")
                st.rerun()
    st.markdown("#### 冲突规则")
    st.write("与宪章硬规则冲突、名称重复、引用不存在或已死亡的对象，一律阻止写入，并记录原因，不会静默覆盖。")
