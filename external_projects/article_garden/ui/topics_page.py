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

    _delete_section(ctx, rows)

    st.divider()
    st.markdown("**修改选题状态**")
    st.caption("可在「待放行」「已放行」「已拒绝」「已放弃」之间手动调整。拒绝或放弃需要填写原因；"
               "恢复或重新放行会清除对应的拒绝记录。调研中、成文中的选题不能手动修改。")
    editable = [r for r in rows if r["status"] not in states.TRANSIENT]
    if not editable:
        st.info("当前没有可以修改状态的选题。")
        return
    labels = {r["id"]: f"{r['id']}  {r['title']}（{states.LABELS.get(r['status'], r['status'])}）" for r in editable}
    tid = st.selectbox("选择选题", list(labels), format_func=lambda i: labels[i], key="st_sel")
    cur = next(r for r in editable if r["id"] == tid)["status"]
    targets = states.allowed_targets(cur)
    if not targets:
        st.info(f"「{states.LABELS.get(cur, cur)}」的选题没有可直接改为的状态（它已进入后续流程）。")
        return
    to = st.selectbox("改为", targets, format_func=lambda x: states.LABELS.get(x, x), key="st_to")
    note = st.text_input("原因（改为「已拒绝」或「已放弃」时必填）", key="st_note")
    if st.button("确认修改状态", key="st_go", type="primary"):
        _try(lambda: states.set_status(ctx, tid, to, note))


def _delete_section(ctx, rows) -> None:
    st.divider()
    st.markdown("**删除选题**")
    st.caption("可删除：待放行、已放行、已拒绝、已放弃的选题（可多选）。已调研、成文失败、已成稿、已审阅、已发布和进行中的选题不能删除。"
               "删除只移除选题记录，已生成的调研与草稿文件不会被改动；被删除的选题会留存在 plan/deleted.jsonl。")
    deletable = [r for r in rows if r["status"] in states.DELETABLE]
    if not deletable:
        st.info("当前没有可以删除的选题。")
        return
    labels = {r["id"]: f"{r['id']}  {r['title']}（{states.LABELS.get(r['status'], r['status'])}）" for r in deletable}
    picked = st.multiselect("选择要删除的选题", list(labels), format_func=lambda i: labels[i], key="del_sel")
    again = st.checkbox("允许之后重新提出同一缺口的选题（默认不勾选：删除后同一缺口不会再被提出）", key="del_again")
    note = st.text_input("备注（可选）", key="del_note")
    sure = st.checkbox(f"确认删除所选的 {len(picked)} 个选题", key="del_sure")
    if st.button("删除所选选题", key="del_go", disabled=not (picked and sure)):
        _try(lambda: states.delete_topics(ctx, picked, allow_repropose=again, note=note))


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
