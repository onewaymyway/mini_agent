"""ui/settings_page.py — ⚙ 设置：章程 · 自动化 · 预算 · 模型与搜索 · 校验阈值 · 全局默认 · 文章库管理。

两层模型：「全局默认」与「本文章库覆盖」。本库覆盖的项会标出并可一键恢复继承。
自动化开关只存在于文章库层（新库一律保守默认）。P0 阶段自动化开关只是被记录，对应能力随 P1–P7 逐期生效。
"""

from __future__ import annotations

from typing import Any, Dict

import streamlit as st

from article_garden import charter as charter_mod
from article_garden import charter_form as cf
from article_garden import domains, paths, settings
from article_garden.providers.text import text_model_status
from ui import state
from ui.topbar import render_topbar

GROUP_TITLES = {"automation": "自动化", "budget": "预算与限制", "llm": "模型", "search": "搜索", "thresholds": "校验阈值"}


def render_settings_page() -> None:
    ctx = state.current_ctx()
    if ctx is None:
        return
    render_topbar(ctx, "⚙ 设置")
    tabs = st.tabs(["章程", "自动化", "预算与限制", "模型与搜索", "校验阈值", "全局默认", "文章库管理"])
    with tabs[0]:
        _charter_tab(ctx)
    with tabs[1]:
        st.info("自动化开关**只属于本文章库**，新建库一律为保守默认（全关、G0）。"
                "P0 阶段这些开关仅被记录，对应能力在 P1–P7 随各期实现后才会生效。")
        _group_editor("domain", "automation", ctx)
    with tabs[2]:
        _group_editor("domain", "budget", ctx)
    with tabs[3]:
        _group_editor("domain", "llm", ctx)
        _group_editor("domain", "search", ctx)
        ok, detail = text_model_status()
        st.markdown(("✅ 文本模型已就绪：" if ok else "⚠️ 文本模型未就绪：") + detail)
        st.caption("模型与搜索使用 mini_agent 的 provider 配置；P1 起的摘要与成文需要它。")
    with tabs[4]:
        _group_editor("domain", "thresholds", ctx)
    with tabs[5]:
        _global_tab()
    with tabs[6]:
        _manage_tab(ctx)


# ------------------------------------------------------------------ 章程

def _charter_tab(ctx: domains.DomainContext) -> None:
    charter, warns = ctx.load_charter()
    for w in warns:
        st.warning(w)
    st.caption("章程是所有环节共同遵守的“宪法”：选题越界检查、成文文风与术语统一、来源筛选都以它为准。每行一项。")
    with st.form("charter_form"):
        c1, c2 = st.columns(2)
        domain = c1.text_input("领域名称", charter["domain"])
        primary = c2.text_input("主要读者", charter["audience"]["primary"])
        s1, s2 = st.columns(2)
        scope_in = s1.text_area("范围内的方向（scope.in）", cf.list_to_lines(charter["scope"]["in"]), height=200,
                                help="知识树的顶层骨架来自这里；不在其中的选题会被剔除")
        scope_out = s2.text_area("范围外（scope.out）", cf.list_to_lines(charter["scope"]["out"]), height=200,
                                 help="命中的主题在选题阶段直接剔除，优先于 scope.in")
        levels = st.text_area("读者层级", cf.list_to_lines(charter["audience"]["levels"]), height=90)
        t1, t2, t3 = st.columns([2, 1, 1])
        tone = t1.text_input("语气", charter["style"]["tone"])
        lo = t2.number_input("字数下限", 100, 100000, int(charter["style"]["length"][0]), step=100)
        hi = t3.number_input("字数上限", 100, 200000, int(charter["style"]["length"][1]), step=100)
        must = st.text_area("必备小节", cf.list_to_lines(charter["style"]["must_have"]), height=90)
        ai_disc = st.checkbox("文末加 AI 生成声明", charter["style"]["ai_disclosure"])
        st.markdown("**术语表**（保证全库用词一致；别名用逗号分隔）")
        rows = st.data_editor(cf.terms_to_rows(charter["terms"]) or [{"规范术语": "", "别名（逗号分隔）": ""}],
                              num_rows="dynamic", use_container_width=True, key="terms_editor")
        p1, p2, p3 = st.columns(3)
        prefer = p1.text_area("偏好的来源类型", cf.list_to_lines(charter["sources"]["prefer"]), height=110)
        avoid = p2.text_area("避免的来源类型", cf.list_to_lines(charter["sources"]["avoid"]), height=110)
        cred = p3.selectbox("来源最低可信度（A 最高）", list(charter_mod.CREDIBILITY_LEVELS),
                            index=charter_mod.CREDIBILITY_LEVELS.index(charter["sources"]["min_credibility"]))
        disclaimer = st.text_area("免责声明（可空）", charter["risk"]["disclaimer"], height=70)
        forbidden = st.text_area("禁用断言（成文校验命中即不通过）", cf.list_to_lines(charter["risk"]["forbidden_claims"]), height=90)
        saved = st.form_submit_button("保存章程")
    if saved:
        if hi < lo:
            st.error("字数上限不能小于下限")
            return
        new = cf.form_to_charter(domain=domain, scope_in=scope_in, scope_out=scope_out, primary=primary,
                                 levels=levels, tone=tone, length_min=lo, length_max=hi, must_have=must,
                                 ai_disclosure=ai_disc, term_rows=list(rows), prefer=prefer, avoid=avoid,
                                 min_credibility=cred, disclaimer=disclaimer, forbidden=forbidden)
        for w in ctx.save_charter(new):
            st.warning(w)
        st.success("章程已保存（旧版本备份为 charter.yaml.bak）")


# ------------------------------------------------------------------ 设置项（规格驱动）

def _widget(spec: Dict[str, Any], key: str, value: Any):
    t = spec["type"]
    if t == "bool":
        return st.checkbox(spec["label"], value=bool(value), key=key, help=spec["help"])
    if t == "int":
        return int(st.number_input(spec["label"], min_value=spec.get("min"), max_value=spec.get("max"),
                                   value=int(value), step=1, key=key, help=spec["help"]))
    if t == "float":
        return float(st.number_input(spec["label"], min_value=spec.get("min"), max_value=spec.get("max"),
                                     value=float(value), step=0.05, format="%.2f", key=key, help=spec["help"]))
    choices = list(spec["choices"])
    return st.selectbox(spec["label"], choices, index=choices.index(value), key=key, help=spec["help"])


def _group_editor(layer: str, group: str, ctx: domains.DomainContext | None) -> None:
    eff, warns = settings.effective_settings(ctx if layer == "domain" else None)
    if layer == "domain" and group == "budget":
        st.caption(f"全局每日上限（所有文章库合计）为 {eff['budget']['global_max_llm_calls_per_day']}，"
                   f"只能在「全局默认」中修改，保存位置：`{paths.global_settings_path()}`。")
    for w in warns:
        st.warning(w)
    overridden = settings.override_keys(ctx) if layer == "domain" else set()
    if layer == "global":
        g, _ = settings.load_global_overrides()
        overridden = {(gr, k) for gr, items in g.items() for k in items}
    st.markdown(f"##### {GROUP_TITLES[group]}")
    with st.form(f"form_{layer}_{group}"):
        values: Dict[str, Any] = {}
        for key, spec in settings.SPEC[group].items():
            allowed = spec["scope"] == "both" or spec["scope"] == layer
            if not allowed:
                continue
            tag = "　🔸本库覆盖" if (layer == "domain" and (group, key) in overridden) else ""
            if tag:
                st.caption(f"{spec['label']}{tag}")
            values[key] = _widget(spec, f"{layer}:{group}:{key}", eff[group][key])
        restore = []
        if overridden:
            names = {k: settings.SPEC[group][k]["label"] for (g, k) in overridden if g == group and k in values}
            if names:
                restore = st.multiselect("恢复为继承（清除覆盖）", list(names), format_func=lambda k: names[k],
                                         key=f"restore:{layer}:{group}")
        submitted = st.form_submit_button("保存")
    if submitted:
        try:
            for key, val in values.items():
                if key in restore:
                    settings.clear_override(layer, group, key, ctx)
                elif val != eff[group][key] or (group, key) in overridden:
                    settings.save_override(layer, group, key, val, ctx)
        except ValueError as exc:
            st.error(str(exc))
            return
        st.success("已保存")
        st.rerun()


def _global_tab() -> None:
    st.caption(f"全局默认保存在 `{paths.global_settings_path()}`，所有文章库继承；文章库里可逐项覆盖。"
               "自动化开关不在此处（只属于各文章库）。")
    for group in ("budget", "llm", "search", "thresholds"):
        _group_editor("global", group, None)


# ------------------------------------------------------------------ 文章库管理

def _manage_tab(ctx: domains.DomainContext) -> None:
    info = ctx.info()
    st.markdown(f"##### 文章库：{info['name']}（id：`{ctx.id}`）")
    with st.form("rename_form"):
        new_name = st.text_input("显示名称", info["name"])
        if st.form_submit_button("重命名") and new_name.strip() != info["name"]:
            try:
                domains.rename_domain(ctx.id, new_name)
                st.rerun()
            except domains.DomainError as exc:
                st.error(str(exc))
    st.write(f"数据位置：`{ctx.root}`")

    c1, c2 = st.columns(2)
    if c1.button("📦 导出整个文章库为 zip"):
        target = domains.export_zip(ctx.id, paths.data_root() / "backups")
        st.success(f"已导出：{target}")
    if c2.button("🗄 归档此文章库", help="只从切换器隐藏、不参与定时运行，不删除任何数据；可在「所有文章库」里恢复"):
        domains.archive_domain(ctx.id)
        st.session_state.pop("domain_id", None)
        st.rerun()

    with st.expander("⚠️ 危险操作：彻底删除此文章库"):
        st.warning("删除前会自动导出 zip 备份，备份成功后才删除。此操作不可撤销。")
        confirm = st.text_input(f"请输入文章库名称「{info['name']}」确认", key="del_confirm")
        if st.button("彻底删除", type="primary", disabled=confirm.strip() != info["name"]):
            try:
                backup = domains.delete_domain(ctx.id, confirm)
            except domains.DomainError as exc:
                st.error(str(exc))
            else:
                st.session_state.pop("domain_id", None)
                st.session_state["flash"] = f"已删除，备份在 {backup}"
                st.rerun()

    archived = [d for d in domains.list_domains(include_archived=True) if d.get("status") == domains.STATUS_ARCHIVED]
    if archived:
        st.markdown("##### 已归档的文章库")
        for d in archived:
            a, b = st.columns([4, 1])
            a.write(f"{d['name']}（`{d['id']}`）")
            if b.button("恢复", key=f"restore_{d['id']}"):
                domains.restore_domain(d["id"])
                st.rerun()
