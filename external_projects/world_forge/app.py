"""架空世界工坊 · Streamlit 看板（端口 8509）。

启动：streamlit run app.py --server.port 8509
所有写操作都经 engine / proposals，界面不直接改数据文件。
"""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parent
for _p in (PROJECT_ROOT, *PROJECT_ROOT.parents[:2]):
    _src = _p / "src"
    if (_src / "mini_agent").is_dir() and str(_src) not in sys.path:
        sys.path.insert(0, str(_src))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from world_forge import engine, gaps, proposals, repo, store, timeline  # noqa: E402
from world_forge.providers.factory import make_image_provider, make_text_llm  # noqa: E402

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
    cols[0].metric("当前年份", f"第 {meta['clock']['year']} 年")
    cols[1].metric("设定实体", len(entities))
    cols[2].metric("角色", len(characters))
    cols[3].metric("待确认提案", len(pending))
    cols[4].metric("设定缺口", len(gap_list))
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
    left, right = st.columns([3, 2])
    with left:
        st.markdown("#### 待确认提案")
        if not pending:
            st.caption("没有待确认的提案。")
        for p in pending:
            with st.container(border=True):
                head = p["payload"].get("name") or p["payload"].get("title") or p["payload"].get("summary", "")[:30]
                st.markdown(f"**{p['op']}** · {head} {_tag(p['status'])}", unsafe_allow_html=True)
                st.caption(f"来源：{p['source']} · 风险：{p.get('risk')}")
                with st.expander("查看内容"):
                    st.json(p["payload"])
                b1, b2 = st.columns(2)
                if b1.button("批准", key=f"ap_{p['id']}"):
                    _safe(proposals.approve, wid, p["id"])
                    st.rerun()
                if b2.button("拒绝", key=f"rj_{p['id']}"):
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
        st.markdown("#### 设定缺口")
        if st.button("自动补全前 5 个缺口", type="secondary", disabled=not gap_list):
            with st.spinner("正在补全……"):
                res = _safe(engine.fill_gaps, LLM, wid, limit=5)
            if res:
                st.success(f"已提交 {len(res['filled'])} 条补全提案，剩余缺口 {res['remaining']} 个。")
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
        st.markdown("#### 实体一览")
        if entities:
            st.dataframe([{"类别": e["kind"], "名称": e["name"], "描述": e.get("summary", ""),
                           "来源": e.get("source")} for e in entities], use_container_width=True, hide_index=True)

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
                              next(f"第{e['year']}年 · {e['title']}" for e in events if e["id"] == k))
            if st.form_submit_button("开新分支并切换"):
                bid = _safe(timeline.fork, wid, fname or "新分支", at)
                if bid:
                    st.rerun()
    st.markdown("#### 当前分支事件")
    if not events:
        st.caption("该分支还没有事件。")
    for e in events:
        st.markdown(f"**第{e['year']}年 · {e['title']}**")
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
                x1.markdown(f"- 第{e['year']}年 {e['title']}")
            x2.markdown("**B 独有**")
            for e in res["only_b"]:
                x2.markdown(f"- 第{e['year']}年 {e['title']}")

# ---------- 推进

with tabs[5]:
    st.markdown("#### 推进历史")
    if not meta.get("charter"):
        st.warning("宪章未确认，暂不能推进。")
    else:
        steps = st.number_input("推进步数", min_value=1, max_value=10, value=1)
        st.caption(f"每步 {meta['settings'].get('years_per_step', 10)} 年，当前第 {meta['clock']['year']} 年。"
                   "宪章硬规则违反、引用不存在或已死亡的对象，会被自动拦截并记录原因。")
        if st.button("推进", type="primary"):
            with st.spinner("正在推演……"):
                res = _safe(engine.advance, LLM, wid, int(steps))
            if res:
                for s in res["steps"]:
                    st.markdown(f"**第 {s['year']} 年**　" + "　".join(
                        f"{_tag(k)} {v}" for k, v in s["counts"].items()), unsafe_allow_html=True)
                    for r in s["records"]:
                        head = r["payload"].get("title") or r["payload"].get("name") or r["op"]
                        st.markdown(f"{_tag(r['status'])} `{r['op']}` {head}", unsafe_allow_html=True)
                        if r.get("reasons"):
                            st.caption("；".join(r["reasons"]))
                    for sk in s["skipped"]:
                        st.caption(f"跳过：{sk}")
                st.rerun()

# ---------- 故事与漫画

with tabs[6]:
    evs = repo.load_events(wid, meta["active_branch"])
    st.markdown("#### 从事件生成小故事线")
    ev_labels = {e["id"]: f"第{e['year']}年 · {e['title']}" for e in evs}
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
        yps = st.number_input("每次推进的年数", min_value=1, max_value=500,
                              value=int(meta["settings"].get("years_per_step", 10)))
        recent = st.number_input("推进时参考的近期事件数", min_value=1, max_value=50,
                                 value=int(meta["settings"].get("recent_events", 8)))
        if st.form_submit_button("保存设置"):
            m = repo.load_meta(wid)
            m["settings"].update(auto_minor=auto_minor, years_per_step=int(yps), recent_events=int(recent))
            repo.save_meta(m)
            st.success("已保存。")
            st.rerun()
    st.markdown("#### 冲突规则")
    st.write("与宪章硬规则冲突、名称重复、引用不存在或已死亡的对象，一律阻止写入，并记录原因，不会静默覆盖。")
