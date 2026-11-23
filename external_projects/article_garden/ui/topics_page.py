"""ui/topics_page.py — 💡 选题（P4）：缺口、三列看板（待审 / 已放行 / 已拒绝或放弃）、放行与拒绝（拒绝必须给原因）。"""

from __future__ import annotations

import streamlit as st

from article_garden import joblog
from article_garden.planning import states
from article_garden import settings
from ui import state
from ui.jobs import spawn
from ui.topbar import render_topbar

TYPE_NAMES = {"G1": "结构空洞", "G1s": "内容偏浅", "G2": "悬空概念", "G3": "前置缺失",
              "G4": "对比空缺", "G5": "层级断档", "G6": "缺综述", "OPEN": "开放命题"}


def render_topics_page() -> None:
    ctx = state.current_ctx()
    if ctx is None:
        return
    render_topbar(ctx, "💡 选题")
    busy = joblog.is_running(ctx.root, scopes=("plan",))
    if st.button("检测缺口并生成选题（后台）", disabled=busy, type="primary", key="plan_run"):
        spawn("plan_topics.py", ctx.id)
        st.success("已在后台检测，完成后刷新本页。")
    eff, _ = settings.effective_settings(ctx)
    st.caption(f"挡位 {eff['automation']['gear']} ｜ 每轮最多 {eff['budget']['max_topics_per_cycle']} 个 ｜ "
               f"自动放行：{'开' if eff['automation']['auto_approve'] else '关'}")

    rows = states.load_topics(ctx)
    gaps_doc = ctx.path("plan", "gaps.json")
    if gaps_doc.is_file():
        import json
        gaps = json.loads(gaps_doc.read_text(encoding="utf-8")).get("gaps", [])
        with st.expander(f"缺口（{len(gaps)} 处，来自最近一次检测）", expanded=False):
            if gaps:
                st.dataframe([{"类型": TYPE_NAMES.get(g["type"], g["type"]), "说明": g["reason"],
                               "严重度": g["severity"]} for g in gaps], hide_index=True, use_container_width=True)
            else:
                st.caption("没有发现缺口。")
    if not rows:
        st.info("还没有选题。先完成概念与知识树，再点击上方「检测缺口并生成选题」。")
        return

    c1, c2, c3 = st.columns(3)
    buckets = {"待审": [r for r in rows if r["status"] == states.PROPOSED],
               "已放行": [r for r in rows if r["status"] == states.APPROVED],
               "已拒绝或放弃": [r for r in rows if r["status"] in states.TERMINAL]}
    for col, label in ((c1, "待审"), (c2, "已放行")):
        with col:
            st.markdown(f"**{label}（{len(buckets[label])}）**")
            for r in buckets[label]:
                _card(ctx, r, editable=label == "待审")
    with c3:
        st.markdown(f"**已拒绝或放弃（{len(buckets['已拒绝或放弃'])}）**")
        for r in buckets["已拒绝或放弃"]:
            st.caption(f"{r['id']} {r['title']}：{r.get('reason_rejected', r['status'])}")


def _card(ctx, r, editable: bool) -> None:
    with st.container(border=True):
        st.markdown(f"**{r['title']}**　`{r['id']}`")
        st.caption(f"{r['kind']} ｜ {r['level']} ｜ {TYPE_NAMES.get(r['gap_type'], r['gap_type'])}")
        st.write(r["reason"])
        if r.get("flags"):
            st.warning("、".join(r["flags"]))
        st.caption("必须覆盖：" + "、".join(r.get("must_cover") or []))
        with st.expander(f"分数 {r['score']}"):
            st.json(r.get("breakdown", {}))
        if editable:
            new_title = st.text_input("修改标题", value=r["title"], key=f"title_{r['id']}")
            if new_title != r["title"] and st.button("保存标题", key=f"savet_{r['id']}"):
                _try(lambda: states.set_title(ctx, r["id"], new_title))
            if st.button("放行", key=f"ok_{r['id']}", type="primary"):
                _try(lambda: states.approve(ctx, r["id"]))
            reason = st.selectbox("拒绝原因", states.REJECT_REASONS, key=f"rr_{r['id']}")
            if st.button("拒绝", key=f"no_{r['id']}"):
                _try(lambda: states.reject(ctx, r["id"], reason))


def _try(fn) -> None:
    try:
        fn()
        st.rerun()
    except ValueError as exc:
        st.error(str(exc))
