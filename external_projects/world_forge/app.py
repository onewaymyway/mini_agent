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

from world_forge import comic_layout, engine, gaps, graph as graph_mod, jobs, perspective, proposals, repo, runlog as runlog_mod, store, timeline  # noqa: E402
from world_forge.providers.factory import make_image_provider, make_text_llm  # noqa: E402
from world_forge.calendar import (MINUTES_PER_UNIT, UNIT_LABELS, clock_minutes, format_time,  # noqa: E402
                                  step_label, validate_step)

st.set_page_config(page_title="架空世界工坊", page_icon="🌍", layout="wide")
st.markdown("""<style>
.block-container {padding-top: 4.5rem;}
/* 图片不超出所在列，避免覆盖相邻的文字 */
div[data-testid="stImage"] img {max-width: 100% !important; height: auto !important;}
div[data-testid="stImage"] {max-width: 100%; overflow: hidden;}
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


KIND_LABEL = {"advance": "推进", "arc": "小故事线", "short": "短篇", "board": "分镜", "comic": "漫画", "style": "风格指南", "sheet": "角色表/场景图",
              "export": "导出"}


COMIC_PAGE_CHOICES = [6, 3, 4, 5, 8, 10, 12, "auto"]


def _page_label(v) -> str:
    return "按故事长度自动（不限制）" if v == "auto" else f"{v} 页"


def _start_job(wid: str, kind: str, label: str, fn, *args, result_key=None, **kwargs) -> bool:
    """提交后台任务（可停止、带实时日志）。同一世界已有任务在运行时给出提示。"""
    try:
        jobs.start(wid, kind, label, fn, *args, result_key=result_key, **kwargs)
        return True
    except jobs.JobBusy as exc:
        st.warning(str(exc))
        return False


def _job_bar_body():
    """顶部任务条：显示当前世界最近任务的状态、实时日志与停止按钮；结束后把结果交给页面并刷新。"""
    job = jobs.latest_for(st.session_state.get("current_wid"))
    if job is None:
        return
    if not job.running and not job.delivered:
        job.delivered = True
        if job.result_key and job.result is not None and job.status in ("done", "stopped"):
            st.session_state[job.result_key] = job.result
        st.rerun(scope="app")   # 任务结束：刷新整个页面，显示新的数据与结果
    if job.running:
        with st.container(border=True):
            c1, c2 = st.columns([5, 1])
            c1.markdown(f"**⏳ 运行中：{job.label}**（已用时 {job.elapsed():.0f} 秒）")
            c2.button("停止", key=f"stop_{job.id}", on_click=jobs.request_stop, args=(job.id,),
                      type="secondary")
            tail = job.entries[-14:]
            st.code("\n".join(runlog_mod.format_entry(e) for e in tail) or "（等待开始……）", language=None)
        return
    if job.status == "error":
        st.error(f"「{job.label}」失败：{job.error}")
    elif job.status == "stopped":
        st.warning(f"「{job.label}」已停止：已完成的部分保留在世界中。")
    else:
        st.caption(f"上次任务已完成：{job.label}（用时 {job.elapsed():.0f} 秒）")


if hasattr(st, "fragment"):
    try:
        _job_bar = st.fragment(run_every=1)(_job_bar_body)   # 运行中每秒刷新任务条
    except TypeError:
        _job_bar = st.fragment(_job_bar_body)                  # 旧版 Streamlit：需手动刷新
else:
    _job_bar = _job_bar_body


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

_job_bar()

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
        if _start_job(wid, "overview", "生成世界观简介", engine.generate_overview, LLM, wid):
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
    else:
        pa1, pa2 = st.columns([3, 1])
        overwrite_pt = pa1.checkbox("覆盖已有人设图（重新生成，旧图保存在 history/ 中）", key="portrait_overwrite")
        n_pt = len(engine.portrait_targets(wid, overwrite=overwrite_pt))
        if pa2.button(f"一键生成所有人设图（{n_pt}）", type="primary", disabled=n_pt == 0,
                      key="portrait_all_btn"):
            if _start_job(wid, "portrait", f"一键生成人设图（{n_pt} 个）", engine.make_all_portraits,
                          LLM, IMG, wid, overwrite=overwrite_pt):
                st.rerun()
        st.caption("默认只为存活且还没有人设图的角色生成；人设图生成后，漫画各格会用它保持人物外观一致。")
    ent_name = {e["id"]: e["name"] for e in entities}
    for c in characters:
        with st.container(border=True):
            pth = repo.resolve_portrait(wid, c) or repo.portrait_path(wid, c["id"])
            has_portrait = pth.is_file() and pth.stat().st_size > 0
            img_col, info_col = st.columns([1, 3])
            with img_col:
                if has_portrait:
                    # 直接读取图片内容显示：同一路径重新生成后，Streamlit 不会继续显示旧缓存
                    st.image(pth.read_bytes(), caption=f"{c['name']} · 人设图")
                else:
                    st.caption("（还没有人设图）")
            with info_col:
                fac = ent_name.get(c.get("faction_id")) or c.get("attrs", {}).get("faction_hint") or "—"
                st.markdown(f"**{c['name']}** · {c.get('role')} · 势力：{fac} · 状态：{c.get('status') or '未记录'}")
                st.write(c.get("summary", ""))
                visual = (c.get("attrs") or {}).get("visual_en")
                if visual:
                    st.caption(f"外观描述（英文，用于漫画保持一致）：{visual}")
                label = "重新生成人设图" if has_portrait else "生成人设图"
                if st.button(label, key=f"pt_{c['id']}"):
                    if _start_job(wid, "portrait", f"生成「{c['name']}」的人设图", engine.make_portrait,
                                  LLM, IMG, wid, c["id"]):
                        st.rerun()
                if not has_portrait:
                    st.caption(f"人设图位置：{repo.portrait_path(wid, c['id'])}（未找到有效图片）")
                hist = sorted((pth.parent / "history").glob(f"{c['id']}.*.png"), reverse=True) \
                    if pth.parent.is_dir() else []
                if hist:
                    with st.expander(f"历史人设图（{len(hist)}，重新生成前的旧图）"):
                        hcols = st.columns(min(4, len(hist)))
                        for i, hp in enumerate(hist):
                            hcols[i % len(hcols)].image(hp.read_bytes(), caption=hp.name.split(".")[1][:15])

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
    view_mode = st.radio("显示方式", ["混合时间线（所有事件）", "按主体视角（每个角色/实体各自的时间线）"],
                         horizontal=True, key="timeline_view")
    if not events:
        st.caption("该分支还没有事件。")
    elif view_mode.startswith("按主体"):
        lanes = perspective.subject_lanes(wid, meta["active_branch"])
        if not lanes["subjects"]:
            st.caption("事件中还没有可以按主体展开的参与者。")
        else:
            st.caption(f"共 {len(lanes['subjects'])} 个主体参与了事件。泳道图中每行是一个主体，"
                       "圆圈里的数字对应下方事件的编号。")
            components.html(perspective.lanes_svg(lanes), height=min(760, 110 + 46 * min(30, len(lanes["subjects"]))),
                            scrolling=True)
            subj_opts = {x["id"]: f"{x['name']}（{x['kind']} · {x['count']} 件）" for x in lanes["subjects"]}
            sid_pick = st.selectbox("查看某个主体的时间线", list(subj_opts), format_func=lambda k: subj_opts[k],
                                    key="subject_pick")
            for item in perspective.subject_detail(lanes, sid_pick):
                with st.container(border=True):
                    st.markdown(f"**#{item['index']} {item['t']} · {item['title']}**")
                    if item["with"]:
                        st.caption("同行：" + "、".join(item["with"]))
                    if item["summary"]:
                        st.write(item["summary"])
                    for ch in item["changes"]:
                        fields = ", ".join(f"{k}={v}" for k, v in (ch.get("payload") or {}).items()) or "（旧记录未保存变更内容）"
                        st.caption(f"变更：{ch['op']}（{fields}）")
    else:
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
            if _start_job(wid, "advance", f"推进 {int(steps)} 步", engine.advance, LLM, wid, int(steps),
                          result_key="advance_report", amount=int(amt), unit=unit):
                st.rerun()
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
    runs = runlog_mod.list_runs(wid, kinds={"advance"})
    if not runs:
        st.caption("还没有推进记录。")
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
    st.markdown("#### 生成小故事线")
    src = st.radio("素材来源", ["手动选择事件", "某个主体的时间线（以它为视角）"], horizontal=True, key="story_src")
    if src.startswith("某个"):
        lanes = perspective.subject_lanes(wid, meta["active_branch"])
        if not lanes["subjects"]:
            st.caption("还没有主体参与过事件，无法按主体生成。")
        else:
            subj_opts = {x["id"]: f"{x['name']}（{x['kind']} · {x['count']} 件）" for x in lanes["subjects"]}
            subj_pick = st.selectbox("选择主体（它将成为故事的主角）", list(subj_opts),
                                     format_func=lambda k: subj_opts[k], key="story_subject")
            subj_evs = next(x for x in lanes["subjects"] if x["id"] == subj_pick)["events"]
            subj_labels = {ev["id"]: f"{ev.get('t', '')} · {ev['title']}" for _, ev in subj_evs}
            chosen_subj_ev = st.multiselect("该主体的事件（默认全选，可去掉不需要的）", list(subj_labels),
                                            default=list(subj_labels), format_func=lambda k: subj_labels[k],
                                            key=f"subj_ev_{subj_pick}")
            if st.button("以该主体的时间线生成小故事线", disabled=not chosen_subj_ev or not meta.get("charter"),
                         key="story_from_subject"):
                if _start_job(wid, "arc", f"以「{lanes['names'].get(subj_pick)}」的视角生成小故事线",
                              engine.make_arc, LLM, wid, event_ids=chosen_subj_ev, subject_id=subj_pick,
                              result_key="story"):
                    st.rerun()
    else:
        ev_labels = {e["id"]: f"{e['t']} · {e['title']}" for e in evs}
        chosen_ev = st.multiselect("选择事件", list(ev_labels), format_func=lambda k: ev_labels[k])
        if st.button("生成小故事线", disabled=not chosen_ev or not meta.get("charter")):
            if _start_job(wid, "arc", "生成小故事线", engine.make_arc, LLM, wid, event_ids=chosen_ev,
                          result_key="story"):
                st.rerun()
    st.markdown("#### 直接创作（不经过时间线）")
    lanes_all = perspective.subject_lanes(wid, meta["active_branch"])
    view_opts = {"": "不指定（自由创作）"}
    view_opts.update({x["id"]: f"{x['name']}（{x['kind']}）" for x in lanes_all["subjects"]})
    with st.form("direct_story"):
        brief = st.text_area("描述一个小事件或一段创作想法")
        vp = st.selectbox("视角（可选）：以某个主体的时间线为素材，并以它叙述", list(view_opts),
                          format_func=lambda k: view_opts[k])
        form = st.radio("形式", ["novel", "notes"], horizontal=True,
                        format_func=lambda x: "短篇小说" if x == "novel" else "故事说明")
        if st.form_submit_button("创作短篇") and (brief.strip() or vp):
            if _start_job(wid, "short", "创作短篇", engine.write_short, LLM, wid, brief=brief, form=form,
                          subject_id=vp or None, result_key="story"):
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
        has_arc = (d / "arc.json").is_file()
        has_board = (d / "storyboard.json").is_file()
        has_comic = (d / "comic.html").is_file()
        st.markdown("**操作**")
        r1 = st.columns(4)
        if smeta["kind"] == "arc" and r1[0].button("成文短篇"):
            if _start_job(wid, "short", "成文短篇", engine.write_short, LLM, wid, arc_sid=sid, result_key="story"):
                st.rerun()
        if has_arc and r1[1].button("重新生成小故事线"):
            if _start_job(wid, "arc", "重新生成小故事线", engine.regenerate_arc, LLM, wid, sid):
                st.rerun()
        if smeta.get("request") and r1[2].button("重新成文"):
            if _start_job(wid, "short", "重新成文", engine.regenerate_short, LLM, wid, sid):
                st.rerun()
        if r1[3].button("导出给 novel_writer"):
            if _start_job(wid, "export", "导出", engine.export_bundle, wid, sid):
                st.rerun()
        st.markdown("**漫画设置**")
        oc = st.columns(3)
        pages_pick = oc[0].selectbox("页数（生成分镜时确定）", COMIC_PAGE_CHOICES, format_func=_page_label,
                                     key="comic_pages")
        reading_pick = oc[1].radio("阅读方向", list(comic_layout.READING_OPTIONS),
                                   format_func=lambda k: comic_layout.READING_LABEL[k], horizontal=True,
                                   key="comic_reading")
        color_pick = oc[2].radio("色彩", list(comic_layout.COLOR_OPTIONS),
                                 format_func=lambda k: comic_layout.COLOR_LABEL[k], horizontal=True,
                                 key="comic_color")
        limits_now = engine.comic_limits(wid)
        cand_pick = oc[0].selectbox("每格候选数（人工选用）", list(range(1, limits_now["candidate_max"] + 1)),
                                    key="comic_cands",
                                    help=f"每格生成的候选张数，默认 1；上限见下方「上限设置」（当前 {limits_now['candidate_max']}）。"
                                         "候选会增加图像调用次数。")
        st.caption("页数在「生成分镜」时确定（节拍表据此规划）；阅读方向与色彩可在「生成漫画图」时调整，无需重新生成分镜。")
        with st.expander(f"上限设置（全世界共用）：图像调用 {limits_now['image_budget']} 次/操作，候选 ≤ {limits_now['candidate_max']} 张"):
            lo_b, hi_b = comic_layout.IMAGE_BUDGET_RANGE
            lo_c, hi_c = comic_layout.CANDIDATE_RANGE
            lim_cols = st.columns(2)
            new_budget = lim_cols[0].number_input("单次操作的图像调用上限", min_value=lo_b, max_value=hi_b,
                                                  value=int(limits_now["image_budget"]), step=1, key="lim_budget")
            new_cand = lim_cols[1].number_input("每格候选数上限", min_value=lo_c, max_value=hi_c,
                                                value=int(limits_now["candidate_max"]), step=1, key="lim_cand")
            if st.button("保存上限设置", key="save_limits"):
                try:
                    engine.set_comic_limits(wid, image_budget=int(new_budget), candidate_max=int(new_cand))
                    st.success("已保存。")
                    st.rerun()
                except engine.EngineError as exc:
                    st.error(str(exc))
        st.markdown("**风格与设定图**")
        rs = st.columns(3)
        if rs[0].button("生成风格指南（全世界一份）", help="线条、主色、光照的英文描述，之后每次出图都会带上"):
            if _start_job(wid, "style", "生成风格指南", engine.make_style_guide, LLM, wid):
                st.rerun()
        if rs[1].button("生成角色表（本故事出场角色）", disabled=not has_board,
                        help="每人生成侧面与愤怒、悲伤、惊讶表情，需要先有立绘"):
            if _start_job(wid, "sheet", "生成角色表", engine.make_character_sheets, IMG, wid, sid):
                st.rerun()
        if rs[2].button("生成场景图（分镜中的地点）", disabled=not has_board):
            if _start_job(wid, "sheet", "生成场景图", engine.make_scene_images, LLM, IMG, wid, sid):
                st.rerun()
        if smeta.get("style_guide") or repo.load_meta(wid).get("style_guide"):
            sg_now = repo.load_meta(wid).get("style_guide") or {}
            if sg_now.get("summary"):
                st.caption(f"风格指南：{sg_now['summary']}")
        r2 = st.columns(3)
        if r2[0].button("重新生成分镜" if has_board else "生成分镜",
                        disabled=smeta["kind"] == "arc" and not has_arc):
            if _start_job(wid, "board", "生成分镜", engine.make_storyboard, LLM, wid, sid,
                          pages=pages_pick, reading=reading_pick, color=color_pick):
                st.rerun()
        if r2[1].button("重新生成漫画图" if has_comic else "生成漫画图", disabled=not has_board):
            if _start_job(wid, "comic", "生成漫画图", engine.render_comic, IMG, wid, sid,
                          reading=reading_pick, color=color_pick, candidates=cand_pick):
                st.rerun()
        if has_board:
            board_now = store.read_json(d / "storyboard.json")
            opts = [(pg["no"], i) for pg in board_now["pages"] for i in range(1, len(pg["panels"]) + 1)]
            st.markdown("**单格重新生成**")
            pick = st.selectbox("选择一格", opts, format_func=lambda x: f"第 {x[0]} 页 第 {x[1]} 格", key="panel_pick")
            if st.button("重新生成这一格", key="regen_panel"):
                if _start_job(wid, "comic", f"重新生成第{pick[0]}页第{pick[1]}格", engine.render_comic,
                              IMG, wid, sid, only=[pick], reading=reading_pick, color=color_pick,
                              candidates=cand_pick):
                    st.rerun()
            pn_now = board_now["pages"][pick[0] - 1]["panels"][pick[1] - 1] if pick[0] - 1 < len(board_now["pages"]) else {}
            cands_now = pn_now.get("candidates") or []
            if len(cands_now) > 1:
                st.caption(f"候选图（当前采用第 {pn_now.get('chosen', 1)} 张）")
                cols = st.columns(len(cands_now))
                for j, rel_c in enumerate(cands_now, start=1):
                    if (d / rel_c).is_file():
                        cols[j - 1].image(str(d / rel_c), caption=f"第 {j} 张", use_container_width=True)
                k_pick = st.selectbox("选用哪一张", list(range(1, len(cands_now) + 1)), key="cand_pick",
                                      format_func=lambda k: f"第 {k} 张")
                if st.button("采用这张候选（重新拼装成品，不调用图像模型）", key="choose_cand"):
                    try:
                        engine.choose_candidate(wid, sid, pick[0], pick[1], k_pick)
                        st.rerun()
                    except engine.EngineError as exc:
                        st.error(str(exc))
        if smeta.get("focus"):
            st.info(f"主体视角：以「{smeta['focus']['name']}」（{smeta['focus']['kind']}）为主角展开；"
                    "成文、分镜与漫画都沿用这一视角。")
        if has_arc:
            arc = store.read_json(d / "arc.json")
            st.markdown(f"**{arc['title']}**　{arc.get('premise', '')}")
            for act in arc.get("acts") or []:
                st.markdown(f"- **{act.get('title', '')}**：{act.get('summary', '')}")
        if (d / "story.md").is_file():
            with st.expander("正文", expanded=smeta["kind"] == "short"):
                st.markdown((d / "story.md").read_text(encoding="utf-8"))
        hist_dir = d / "history"
        if hist_dir.is_dir() and any(hist_dir.iterdir()):
            with st.expander(f"旧版本（{len(list(hist_dir.iterdir()))} 个，重新生成前保留）"):
                for f in sorted(hist_dir.iterdir(), reverse=True):
                    st.caption(f.name)
        board = store.read_json(d / "storyboard.json")
        if board:
            st.markdown("#### 漫画")
            if board.get("stub"):
                st.caption("当前为占位图，未调用真实图像模型。")
            opts_now = board.get("opts") or {}
            if opts_now:
                st.caption(f"{len(board['pages'])} 页 · {comic_layout.READING_LABEL.get(opts_now.get('reading'), '')} · "
                           f"{comic_layout.COLOR_LABEL.get(opts_now.get('color'), '')}")
            zip_file = d / "comic_pages.zip"
            if zip_file.is_file():
                st.download_button("下载全部 PNG 页（zip）", data=zip_file.read_bytes(),
                                   file_name=f"{smeta['title']}_漫画.zip", mime="application/zip", key="dl_comic_zip")
            pngs = sorted((d / "pages").glob("page_*.png")) if (d / "pages").is_dir() else []
            if pngs:
                with st.expander(f"PNG 成品页（{len(pngs)} 页，服务端排字）", expanded=True):
                    for png in pngs:
                        st.image(str(png), use_container_width=True)
                        st.download_button(f"下载 {png.name}", data=png.read_bytes(), file_name=png.name,
                                           mime="image/png", key=f"dl_{png.stem}")
            if pngs:
                ex = st.columns(2)
                if ex[0].button("导出 CBZ 与条漫长图", key="export_comic_files"):
                    try:
                        out_e = engine.export_comic_files(wid, sid)
                        st.success(f"已导出：{Path(out_e['cbz']).name}、{Path(out_e['strip']).name}")
                    except engine.EngineError as exc:
                        st.error(str(exc))
                cbz_f, strip_f = d / "comic.cbz", d / "comic_strip.png"
                if cbz_f.is_file():
                    ex[1].download_button("下载 CBZ", data=cbz_f.read_bytes(), file_name=f"{smeta['title']}.cbz",
                                          mime="application/vnd.comicbook+zip", key="dl_cbz")
                if strip_f.is_file():
                    st.download_button("下载条漫长图（PNG）", data=strip_f.read_bytes(),
                                       file_name=f"{smeta['title']}_条漫.png", mime="image/png", key="dl_strip")
                st.caption("网页阅读器 reader.html 与 pages/ 文件夹放在一起打开（整个故事目录需一起移动）。")
            with st.expander("编辑文字（旁白、音效、对白；保存后重新拼装，不调用图像模型）"):
                eo = [(pg["no"], i) for pg in board["pages"] for i in range(1, len(pg["panels"]) + 1)]
                ed_pick = st.selectbox("选择一格", eo, format_func=lambda x: f"第 {x[0]} 页 第 {x[1]} 格",
                                       key="edit_pick")
                pn_e = board["pages"][ed_pick[0] - 1]["panels"][ed_pick[1] - 1]
                key_base = f"ed_{ed_pick[0]}_{ed_pick[1]}"
                nar_v = st.text_input("旁白", value=pn_e.get("narration", ""), key=f"{key_base}_nar")
                sfx_v = st.text_input("音效", value=pn_e.get("sfx", ""), key=f"{key_base}_sfx")
                dl_vals = []
                for j, dl in enumerate(pn_e.get("dialogue") or []):
                    tag = {"thought": "心声", "shout": "呼喊"}.get(dl.get("kind"), "说话")
                    dl_vals.append(st.text_input(f"{dl.get('speaker', '')}（{tag}）", value=dl.get("text", ""),
                                                 key=f"{key_base}_dl{j}"))
                if st.button("保存文字并重新拼装", key=f"{key_base}_save"):
                    try:
                        engine.edit_panel_text(wid, sid, ed_pick[0], ed_pick[1], narration=nar_v, sfx=sfx_v,
                                               dialogue=dl_vals or None)
                        st.rerun()
                    except engine.EngineError as exc:
                        st.error(str(exc))
            comic_file = d / "comic.html"
            if comic_file.is_file():
                st.caption("网页预览（图片已嵌入；尾巴与完整排字以上方 PNG 为准）")
                components.html(comic_file.read_text(encoding="utf-8"), height=960, scrolling=True)
            with st.expander("分镜脚本（节拍、镜头、版式、对白）", expanded=not comic_file.is_file()):
                for pg in board["pages"]:
                    head = f"**第 {pg['no']} 页**"
                    if pg.get("beat"):
                        head += f"　{pg['beat']}"
                    st.markdown(head)
                    if pg.get("hook"):
                        st.caption(f"页末钩子：{pg['hook']}")
                    for pn in pg["panels"]:
                        size = "整行" if int(pn.get("span") or 1) == 2 else "半行"
                        extra = f"｜音效「{pn['sfx']}」" if pn.get("sfx") else ""
                        st.markdown(f"- **{pn.get('shot', '中景')} · {size}**{extra}：{pn['visual_en']}")
                        if pn.get("narration"):
                            st.caption(f"旁白：{pn['narration']}")
                        for dl in pn.get("dialogue") or []:
                            kind_label = {"thought": "心声", "shout": "呼喊"}.get(dl.get("kind"), "")
                            tag = f"［{kind_label}］" if kind_label else ""
                            st.caption(f"{tag}{dl.get('speaker', '')}（{dl.get('position', '')}）：{dl.get('text', '')}")

    st.markdown("#### 运行日志（故事与漫画）")
    s_runs = runlog_mod.list_runs(wid, kinds={"arc", "short", "board", "comic", "export", "sheet", "style"})
    if not s_runs:
        st.caption("还没有故事或漫画相关的运行记录。")
    else:
        s_labels = {r["path"]: f"{r['stamp']} · {KIND_LABEL.get(r['kind'], r['kind'])}" for r in s_runs}
        s_chosen = st.selectbox("选择一次运行", list(s_labels), format_func=lambda k: s_labels[k], key="story_run_pick")
        s_entries = runlog_mod.read_run(s_chosen)
        lv = {}
        for e in s_entries:
            lv[e.get("level", "info")] = lv.get(e.get("level", "info"), 0) + 1
        st.caption(f"共 {len(s_entries)} 条记录：" + "　".join(f"{k} {v}" for k, v in lv.items()))
        st.code("\n".join(runlog_mod.format_entry(e) for e in s_entries), language=None)

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
            if jobs.is_busy(wid):
                st.error("该世界有任务正在运行，请先停止或等待完成后再删除。")
                res = None
            else:
                res = _safe(engine.delete_world, wid)
            if res:
                remaining = repo.list_world_ids()
                st.session_state.current_wid = remaining[0] if remaining else None
                st.success(f"已删除「{res['title']}」。")
                st.rerun()
    st.markdown("#### 冲突规则")
    st.write("与宪章硬规则冲突、名称重复、引用不存在或已死亡的对象，一律阻止写入，并记录原因，不会静默覆盖。")
