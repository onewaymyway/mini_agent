#!/usr/bin/env python3
"""info_hub Streamlit 看板。

功能：
- 报告：查看各计划生成的报告，整份听报或逐条朗读（浏览器语音，不生成音频文件）
- 信息源：查看、启用/停用、立即抓取
- 添加信息源：表单添加，或对话生成（LLM 草稿 → 预览 → 修改 → 确认）
- 运行记录、登录状态
- 后台调度：看板进程内每隔 tick_interval_minutes 执行一次到期任务

启动方式：
    cd external_projects/info_hub
    streamlit run app.py --server.port 8504

业务逻辑全部复用 server/api.py，（旧的 HTML 看板与 `cli.py serve` 已移除，Streamlit 是唯一看板。）
"""
from __future__ import annotations

import os
import sys
import threading
from datetime import date
from pathlib import Path
from typing import Any, Optional

import pandas as pd
import streamlit as st

# INFO_HUB_ROOT 可指定另一份项目目录（测试或多实例时使用），默认为本文件所在目录
PROJECT_ROOT = Path(os.environ.get("INFO_HUB_ROOT") or Path(__file__).resolve().parent).resolve()
sys.path.insert(0, str(PROJECT_ROOT))

from core.feedback import item_key  # noqa: E402
from core.settings import load_settings  # noqa: E402
from dashboard.speech import speech_player_html  # noqa: E402
from server import api  # noqa: E402
from server.api import ApiError  # noqa: E402

st.set_page_config(page_title="info_hub 看板", page_icon="📰", layout="wide")


# ---------- 后台调度（每个 Streamlit 进程只启动一次） ----------
@st.cache_resource(show_spinner=False)
def _background_scheduler(root: str) -> Optional[threading.Event]:
    if os.environ.get("INFO_HUB_BACKGROUND_TICK", "1") != "1":
        return None
    settings = load_settings(Path(root))
    if not settings.raw.get("streamlit", {}).get("background_tick", True):
        return None
    from core.tick import start_tick_loop
    stop = threading.Event()
    start_tick_loop(settings, stop)  # 间隔每次从 settings 读取，看板修改后自动生效
    return stop


def call(fn, *args, **kwargs) -> Any:
    """调用 api 函数；业务错误以 st.error 展示，返回 None。"""
    try:
        return fn(*args, **kwargs)
    except ApiError as exc:
        st.error(exc.message)
    except Exception as exc:  # noqa: BLE001 - 看板不应崩溃
        st.error(f"{type(exc).__name__}: {exc}")
    return None


def fmt_time(iso: Optional[str]) -> str:
    return iso.replace("T", " ")[:16] if iso else "从未"


def open_button(url: Optional[str], key: str, *, sid: str = "", title: str = "", view: str = "",
                label: str = "打开") -> None:
    """在专用浏览器中打开链接（看板上所有显示抓取内容地址的地方都用它）。

    sid 不为空时按条目打开：反馈工具条绑定该条目，并记录一次“打开”；
    sid 为空时只打开原始链接（候选信息源地址、信息源配置地址等没有条目的场景）。
    """
    if not url:
        return
    if st.button(label, key=key, help="在专用浏览器中打开" + ("，页面侧边的反馈工具条可用" if sid else "")):
        out = call(api.open_in_dedicated_browser, settings, {
            "url": url, "source_id": sid, "item_key": item_key(url, title) if sid else "",
            "view": view, "title": title})
        if out is not None:
            st.toast("已在专用浏览器中打开")


settings = load_settings(PROJECT_ROOT)
_background_scheduler(str(PROJECT_ROOT))

# ---------- 侧边栏 ----------
with st.sidebar:
    st.header("info_hub")
    status = api.provider_status(settings)
    st.caption("对话生成模型配置")
    if status["source"] == "none":
        st.error(status["label"])
    else:
        st.info(status["label"])

    bg_on = _background_scheduler(str(PROJECT_ROOT)) is not None
    st.caption(f"后台调度：{'运行中，每 ' + str(settings.tick_interval_minutes) + ' 分钟' if bg_on else '未启用'}")

    if st.button("立即调度", use_container_width=True):
        res = call(api.run_tick_now, settings)
        if res is not None:
            st.success(f"运行 {len(res['ran_sources'])} 个源，生成报告 {len(res['generated_reports'])} 份")
            st.rerun()

    plans = [p["id"] for p in settings.reports if "id" in p]
    plan = st.selectbox("报告计划", plans, key="gen_plan") if plans else None
    if plan and st.button("生成报告", use_container_width=True):
        res = call(api.generate_report_now, settings, plan)
        if res is not None:
            st.success(f"已生成：{res['markdown']}")
            st.rerun()

st.title("信息看板")
(tab_reports, tab_sources, tab_edit, tab_results, tab_feedback, tab_subs, tab_add, tab_runs, tab_status,
 tab_login) = st.tabs(
    ["报告", "信息源", "编辑信息源", "抓取结果", "反馈记录", "订阅", "添加信息源", "运行记录", "调度状态", "登录"])


# ---------- 报告 ----------
with tab_reports:
    listing = call(api.list_reports, settings) or {"reports": []}
    reports = listing["reports"]
    if not reports:
        st.info("还没有报告。可以在侧边栏生成一份，或等待调度。")
    else:
        labels = [f"{r['plan_id']} · {fmt_time(r['generated_at'])}" for r in reports]
        idx = st.selectbox("选择报告", range(len(reports)), format_func=lambda i: labels[i], key="report_pick")
        r = reports[idx]
        s = r.get("summary") or {}
        c1, c2, c3 = st.columns(3)
        c1.metric("新增条目", s.get("new_items", 0))
        c2.metric("涉及信息源", s.get("source_count", 0))
        c3.metric("异常", s.get("error_count", 0))

        md = call(api.read_report, settings, r["markdown"])
        digest = call(api.read_report, settings, r["json"])
        left, right = st.columns([3, 2])
        with left:
            st.subheader("报告内容")
            if md:
                st.markdown(md["markdown"])
        with right:
            st.subheader("听报")
            segs = call(api.report_speech, settings, r["json"])
            if segs:
                st.components.v1.html(speech_player_html(segs["segments"], f"{r['plan_id']} 整份报告"),
                                      height=190)
            st.subheader("单条朗读")
            items = []
            for sec in (digest or {}).get("json", {}).get("sections", []):
                for src in sec.get("sources", []):
                    for it in src.get("items", []):
                        items.append((src["source_id"], it["id"], it["title"], it.get("url", "")))
            if not items:
                st.caption("本报告没有条目。")
            else:
                pick = st.selectbox("条目", range(len(items)),
                                    format_func=lambda i: items[i][2][:60], key="item_pick")
                src_id, item_id, title, item_url = items[pick]
                open_button(item_url, f"op_rep_{src_id}_{item_id}", sid=src_id, title=title)
                one = call(api.item_speech, settings, src_id, item_id)
                if one:
                    st.components.v1.html(speech_player_html(one["segments"], title), height=190)


# ---------- 信息源 ----------
with tab_sources:
    data = call(api.list_sources, settings) or {"sources": [], "categories": []}
    rows = [s for s in data["sources"] if not s.get("config_errors")]
    bad = [s for s in data["sources"] if s.get("config_errors")]
    if bad:
        for s in bad:
            st.error(f"{s['id']}：" + "；".join(s["config_errors"]))
    if not rows:
        st.info("还没有信息源。到“添加信息源”页签添加。")
    else:
        st.caption("要修改信息源的配置，请到“编辑信息源”页签。")
        cat_names = {c["id"]: c["name"] for c in data["categories"]}
        df = pd.DataFrame([{
            "启用": s["enabled"],
            "名称": s["name"],
            "ID": s["id"],
            "分类": cat_names.get(s["category"], s["category"]),
            "类型": f"{s['kind']} / {s['adapter']}" + (" · 需登录" if s.get("browser_site") else ""),
            "健康": {"ok": "🟢 正常", "degraded": f"🔴 连续失败 {s.get('failures', 0)} 次",
                     "paused": "⛔ 已暂停"}.get(s.get("health"), "🟢 正常"),
            "频率": s["every"],
            "上次运行": fmt_time(s.get("last_run")),
            "最近条目": s.get("last_item_count"),
        } for s in rows])
        edited = st.data_editor(
            df, hide_index=True, use_container_width=True, key="sources_editor",
            disabled=[c for c in df.columns if c != "启用"],
        )
        if st.button("保存启用状态"):
            changed = 0
            for before, after in zip(df.itertuples(), edited.itertuples()):
                if before.启用 != after.启用:
                    if call(api.set_source_enabled, settings, before.ID, bool(after.启用)) is not None:
                        changed += 1
            if changed:
                st.success(f"已更新 {changed} 个信息源")
                st.rerun()
            else:
                st.caption("没有变化。")

        sick = [s for s in rows if s.get("health") in ("degraded", "paused")]
        if sick:
            st.subheader("异常信息源")
            for s_ in sick:
                c1, c2 = st.columns([5, 1])
                c1.error(f"{s_['name']}（{s_['id']}）"
                         + ("已自动暂停" if s_["health"] == "paused" else "连续失败")
                         + f"，失败 {s_.get('failures', 0)} 次：{s_.get('last_error') or ''}")
                if c2.button("恢复", key=f"resume_{s_['id']}"):
                    call(api.resume_source, settings, s_["id"])
                    st.rerun()

        st.subheader("立即抓取")
        ids = [s["id"] for s in rows]
        names = {s["id"]: s["name"] for s in rows}
        target = st.selectbox("信息源", ids, format_func=lambda i: names[i], key="run_pick")
        if st.button("抓取一次"):
            with st.spinner("抓取中…"):
                res = call(api.run_source_now, settings, target)
            if res is not None:
                if res["ok"]:
                    st.success(f"抓取 {res['item_count']} 条，新增 {res['new_count']} 条")
                else:
                    st.error(f"失败：{res['error']}")


# ---------- 订阅 ----------
with tab_subs:
    subs = (call(api.list_subscriptions, settings) or {"subscriptions": []})["subscriptions"]
    if not subs:
        st.info("还没有订阅源。订阅源的 kind 为 subscription，配置方式见 PROJECT.md“订阅源”一节。")
    for sub in subs:
        with st.container(border=True):
            st.markdown(f"**{sub['name']}** · `{sub['id']}` · {sub['checker']} · 每 {sub['every']}"
                        + ("" if sub["enabled"] else " · 已停用"))
            if sub["current_title"]:
                st.markdown(f"当前进度：{sub['current_title']}")
                open_button(sub["current_url"], f"op_sub_{sub['id']}", sid=sub["id"], title=sub["current_title"])
                st.caption(f"最近检查 {fmt_time(sub['checked_at'])} · 最近更新 {fmt_time(sub['updated_at'])}")
            else:
                st.caption("尚未检查。到“信息源”页签点“抓取一次”记录基线。")
            if sub["recent_updates"]:
                st.markdown("最近更新：")
                for n, u in enumerate(sub["recent_updates"]):
                    prev = f"（上次：{u['previous_title']}）" if u["previous_title"] else ""
                    uc1, uc2 = st.columns([8, 1])
                    uc1.markdown(f"- {fmt_time(u['fetched_at'])} {u['title']}{prev}")
                    with uc2:
                        open_button(u["url"], f"op_subu_{sub['id']}_{n}", sid=sub["id"], title=u["title"])


# ---------- 添加信息源 ----------
with tab_add:
    cat_options = [c["id"] for c in settings.categories]
    cat_names = {c["id"]: c["name"] for c in settings.categories}
    sites = (call(api.login_status, settings) or {"sites": []})["sites"]
    site_ids = [s["site"] for s in sites]
    form_tab, dialog_tab = st.tabs(["表单添加", "对话生成"])

    with form_tab:
        with st.form("add_form"):
            c1, c2, c3 = st.columns(3)
            name = c1.text_input("名称")
            sid = c2.text_input("ID（小写字母与下划线）", placeholder="zhihu_hot")
            category = c3.selectbox("分类", cat_options, format_func=lambda c: cat_names.get(c, c))
            c4, c5, c6 = st.columns(3)
            kind = c4.selectbox("类型", ["hot_list", "feed", "recommend", "custom"])
            adapter = c5.selectbox("适配器", ["rss", "json_api", "html_list"])
            every = c6.text_input("频率", value="60m", help="如 60m / 3h / 1d")
            url = st.text_input("地址", placeholder="https://...")
            fields: dict[str, str] = {}
            list_selector = items_path = ""
            if adapter == "html_list":
                c7, c8, c9 = st.columns(3)
                list_selector = c7.text_input("列表选择器", placeholder=".hot-item")
                fields["title"] = c8.text_input("标题选择器", placeholder=".title")
                link = c9.text_input("链接选择器", placeholder="a@href")
                if link:
                    fields["url"] = link
            elif adapter == "json_api":
                c7, c8, c9 = st.columns(3)
                items_path = c7.text_input("列表路径", placeholder="data.list")
                fields["title"] = c8.text_input("标题字段", placeholder="title")
                link = c9.text_input("链接字段", placeholder="url")
                if link:
                    fields["url"] = link
            needs_login = st.checkbox("需要登录（共享浏览器会话）")
            site = st.selectbox("站点", site_ids, disabled=not needs_login) if site_ids else None
            save_force = st.checkbox("与已有源相似时仍然保存")
            col_p, col_s = st.columns(2)
            do_preview = col_p.form_submit_button("预览")
            do_save = col_s.form_submit_button("保存")

        form_source = {
            "id": sid.strip(), "name": name.strip(), "category": category, "kind": kind,
            "adapter": adapter, "every": every.strip() or "60m", "url": url.strip(),
            "max_items": 30, "tags": [], "list_selector": list_selector.strip(),
            "items_path": items_path.strip(), "fields": {k: v.strip() for k, v in fields.items() if v.strip()},
        }
        if needs_login and site:
            form_source["browser_required"] = True
            form_source["browser_site"] = site

        if do_preview:
            res = call(api.preview_form, settings, {"source": form_source})
            if res is not None:
                if res["errors"]:
                    for e in res["errors"]:
                        st.error(e)
                if res["duplicates"]:
                    st.warning("与已有信息源相似：" + "、".join(res["duplicates"]))
                p = res.get("preview")
                if p:
                    if p["ok"]:
                        st.success(f"共 {p['total']} 条，前 {len(p['items'])} 条如下")
                        st.dataframe(pd.DataFrame(p["items"]), hide_index=True, use_container_width=True)
                    else:
                        st.error(f"抓取失败：{p['error']}")
        if do_save:
            res = call(api.create_form, settings, {"source": form_source, "force": save_force})
            if res is not None:
                st.success(f"已保存信息源 {res['id']}")

    with dialog_tab:
        st.caption("填写地址与需求，模型会生成草稿。草稿不会运行脚本，预览或确认时才会运行。")
        with st.form("dialog_form"):
            d_url = st.text_input("地址", key="d_url", placeholder="https://...")
            d1, d2 = st.columns(2)
            d_name = d1.text_input("名称", key="d_name")
            d_cat = d2.selectbox("分类", cat_options, key="d_cat", format_func=lambda c: cat_names.get(c, c))
            d_hint = st.text_area("需求描述", key="d_hint", placeholder="例如：只要标题和链接，忽略广告")
            d_browser = st.checkbox("页面需要登录态（使用共享浏览器渲染）", key="d_browser")
            d_gen = st.form_submit_button("生成草稿")
        if d_gen:
            with st.spinner("抓取页面并请求模型…"):
                draft = call(api.generate_source, settings, {
                    "url": d_url, "name": d_name, "hint": d_hint, "category": d_cat,
                    "use_browser": d_browser,
                })
            if draft:
                st.session_state["draft_id"] = draft["id"]
                st.success(f"已生成草稿 {draft['id']}")

        drafts = (call(api.list_drafts, settings) or {"drafts": []})["drafts"]
        if drafts:
            ids = [d["id"] for d in drafts]
            current = st.session_state.get("draft_id")
            pick = st.selectbox("草稿", ids, index=ids.index(current) if current in ids else 0,
                                format_func=lambda i: next(d["name"] or i for d in drafts if d["id"] == i),
                                key="draft_pick")
            st.session_state["draft_id"] = pick
        draft_id = st.session_state.get("draft_id")
        if draft_id:
            draft = call(api.get_draft, settings, draft_id)
            if draft:
                st.markdown(f"**{draft.get('name') or draft['id']}**（`{draft['id']}`）")
                st.write(draft.get("explanation", ""))
                st.caption(f"适配器：{draft.get('adapter')} · 由 {draft.get('llm_provider') or '未知'} 生成"
                           + (" · 含脚本，确认前请阅读 data/drafts/{}/script.py".format(draft_id)
                              if draft.get("has_script") else ""))
                for e in draft.get("errors") or []:
                    st.error(e)
                if draft.get("duplicates"):
                    st.warning("与已有信息源相似：" + "、".join(draft["duplicates"]))
                prev = draft.get("preview")
                if prev:
                    if prev.get("ok"):
                        st.success(f"预览：共 {prev['total']} 条")
                        if prev.get("items"):
                            st.dataframe(pd.DataFrame(prev["items"]), hide_index=True, use_container_width=True)
                    else:
                        st.error(f"预览失败：{prev.get('error')}")
                with st.expander("源配置"):
                    st.json(draft.get("source", {}))

                b1, b2, b3 = st.columns(3)
                if b1.button("预览草稿"):
                    with st.spinner("抓取中…"):
                        call(api.preview_draft, settings, draft_id)
                        st.rerun()
                if b2.button("放弃草稿"):
                    call(api.discard_draft, settings, draft_id)
                    st.session_state.pop("draft_id", None)
                    st.rerun()
                with st.form("revise_form"):
                    feedback = st.text_input("修改意见", placeholder="例如：标题选择器不对，应该用 h2")
                    if st.form_submit_button("按意见修改"):
                        with st.spinner("请求模型修改…"):
                            res = call(api.revise_draft, settings, draft_id, {"feedback": feedback})
                        if res is not None:
                            st.rerun()
                with st.form("confirm_form"):
                    force = st.checkbox("与已有源相似时仍然添加")
                    if st.form_submit_button("确认添加"):
                        res = call(api.confirm_draft, settings, draft_id, {"force": force})
                        if res is not None:
                            st.session_state.pop("draft_id", None)
                            st.success(f"已添加信息源 {res['id']}")
                            st.rerun()


with tab_edit:
    if not rows:
        st.info("还没有信息源。到“添加信息源”页签添加。")
    else:
        st.subheader("编辑信息源")
        edit_ids = [s["id"] for s in rows]
        edit_names = {s["id"]: f"{s['name']}（{s['id']}）" for s in rows}
        pick = st.selectbox("选择要编辑的信息源", edit_ids, format_func=lambda i: edit_names[i], key="edit_pick")
        detail = call(api.get_source, settings, pick)
        if detail:
            cat_ids = [c["id"] for c in settings.categories]
            cat_label = {c["id"]: c["name"] for c in settings.categories}
            kinds = ["hot_list", "feed", "recommend", "custom", "subscription"]
            adapters = ["rss", "json_api", "html_list"]
            sites_for_edit = [x["site"] for x in (call(api.login_status, settings) or {"sites": []})["sites"]]
            k = lambda name: f"ed_{pick}_{name}"  # noqa: E731 - 每个源独立的控件 key，切换源时不串值
            with st.form(f"edit_form_{pick}"):
                e1, e2, e3 = st.columns(3)
                e_name = e1.text_input("名称", value=detail["name"], key=k("name"))
                e_cat = e2.selectbox("分类", cat_ids, format_func=lambda c: cat_label.get(c, c),
                                     index=cat_ids.index(detail["category"]) if detail["category"] in cat_ids else 0,
                                     key=k("cat"))
                e_kind = e3.selectbox("类型", kinds, index=kinds.index(detail["kind"]) if detail["kind"] in kinds else 1,
                                      key=k("kind"))
                e4, e5, e6 = st.columns(3)
                e_adapter = e4.selectbox("适配器", adapters,
                                         index=adapters.index(detail["adapter"]) if detail["adapter"] in adapters else 0,
                                         key=k("adapter"), help="切换适配器后，原选择器不再生效，请重新填写")
                e_every = e5.text_input("频率", value=detail["every"], key=k("every"))
                e_max = e6.number_input("最多条目", min_value=1, max_value=500, value=int(detail["max_items"] or 30),
                                        key=k("max"))
                e_url = st.text_input("地址", value=detail["url"], key=k("url"))
                e_tags = st.text_input("标签（逗号分隔）", value=", ".join(detail["tags"]), key=k("tags"))
                e_fields = {}
                e_list = e_items = ""
                if e_adapter == "html_list":
                    e7, e8, e9 = st.columns(3)
                    e_list = e7.text_input("列表选择器", value=detail["list_selector"], key=k("list"))
                    e_fields["title"] = e8.text_input("标题选择器", value=detail["fields"]["title"], key=k("ftitle"))
                    e_fields["url"] = e9.text_input("链接选择器", value=detail["fields"]["url"], key=k("furl"))
                elif e_adapter == "json_api":
                    e7, e8, e9 = st.columns(3)
                    e_items = e7.text_input("列表路径", value=detail["items_path"], key=k("items"))
                    e_fields["title"] = e8.text_input("标题字段", value=detail["fields"]["title"], key=k("ftitle"))
                    e_fields["url"] = e9.text_input("链接字段", value=detail["fields"]["url"], key=k("furl"))
                e_login = st.checkbox("需要登录（共享浏览器会话）", value=detail["browser_required"], key=k("login"))
                e_site = st.selectbox("站点", sites_for_edit or [detail["browser_site"]],
                                      index=(sites_for_edit.index(detail["browser_site"])
                                             if detail["browser_site"] in sites_for_edit else 0),
                                      disabled=not e_login, key=k("site")) if (sites_for_edit or detail["browser_site"]) else None
                e_wait = st.text_input("等待元素选择器（浏览器源）", value=detail["wait_selector"], key=k("wait"))
                e_enabled = st.checkbox("启用", value=detail["enabled"], key=k("enabled"))
                e_force = st.checkbox("与已有源相似时仍然保存", key=k("force"))
                do_save_edit = st.form_submit_button("保存修改")
            open_button(detail["url"], f"op_edit_{pick}", label="在专用浏览器中打开该地址")
            if do_save_edit:
                body = {"source": {
                    "id": pick, "name": e_name.strip(), "category": e_cat, "kind": e_kind, "adapter": e_adapter,
                    "every": e_every.strip() or "60m", "url": e_url.strip(), "max_items": int(e_max),
                    "tags": e_tags, "enabled": bool(e_enabled), "list_selector": e_list.strip(),
                    "items_path": e_items.strip(), "fields": {kk: vv.strip() for kk, vv in e_fields.items()},
                }, "force": bool(e_force)}
                if e_login and e_site:
                    body["source"]["browser_required"] = True
                    body["source"]["browser_site"] = e_site
                    body["source"]["wait_selector"] = e_wait.strip()
                res = call(api.update_source, settings, pick, body)
                if res is not None:
                    st.success(f"已保存 {pick}")
                    st.rerun()


# ---------- 抓取结果 ----------
@st.fragment(run_every=60)
def _results_tab():
    _services_panel()
    _profile_panel()
    _candidates_panel()
    _evaluation_panel()
    _srcs = [x for x in (call(api.list_sources, settings) or {"sources": []})["sources"]
             if not x.get("config_errors")]
    if not _srcs:
        st.info("还没有信息源。")
    else:
        _names = {x["id"]: f"{x['name']}（{x['id']}）" for x in _srcs}
        rc1, rc2 = st.columns([2, 2])
        sid = rc1.selectbox("信息源", list(_names), format_func=lambda k: _names[k], key="res_src")
        snaps = (call(api.list_snapshots, settings, sid) or {"snapshots": []})["snapshots"]
        if not snaps:
            st.info("该信息源还没有抓取结果。到“信息源”页签点“立即抓取”，或在终端运行 python cli.py run " + sid)
        else:
            labels = {x["id"]: f"{fmt_time(x['fetched_at'])} · {x['item_count']} 条 · 新增 {x['new_count']}" for x in snaps}
            snap_id = rc2.selectbox("抓取时间", list(labels), format_func=lambda k: labels[k], key="res_snap")
            snap = call(api.read_snapshot, settings, sid, snap_id)
            if snap:
                st.caption(f"抓取于 {fmt_time(snap['fetched_at'])}，共 {snap['item_count']} 条，新增 {snap['new_count']} 条")
                if snap.get("meta"):
                    with st.expander("调试信息（解析统计、字段映射、原始响应开头）"):
                        st.json(snap["meta"])
                ch = snap["changes"]
                titles = {it.get("id"): it.get("title", "") for it in snap["items"]}
                if ch.get("new") or ch.get("rising") or ch.get("falling") or ch.get("dropped"):
                    for key, label in (("new", "🆕 新上榜/新增"), ("rising", "🔺 上升"),
                                       ("falling", "🔻 下降"), ("dropped", "⬇️ 掉出")):
                        entries = ch.get(key) or []
                        if entries:
                            with st.expander(f"{label}（{len(entries)}）"):
                                for e in entries:
                                    t = e.get("title") or titles.get(e.get("id")) or e.get("id", "")
                                    extra = ""
                                    if "to" in e and "from" in e:
                                        extra = f"（{e['from']} → {e['to']}）"
                                    elif "from" in e:
                                        extra = f"（原第 {e['from']} 名）"
                                    st.write(f"- {t}{extra}")
                kw = st.text_input("按标题过滤", key="res_kw").strip()
                rows = [it for it in snap["items"] if kw in (it.get("title") or "") or kw in (it.get("summary") or "")]
                rank_on = st.toggle("按我的兴趣排序", key=f"rank_on_{sid}",
                                    help="按兴趣画像重新排序，最近标记为不感兴趣的条目沉底；只影响显示顺序。")
                if rank_on and rows:
                    ranked = call(api.rank_snapshot, settings, sid, rows)
                    if ranked:
                        rows = ranked["items"]
                view = "ranked" if rank_on else "plain"
                guard = f"imp_{sid}_{snap_id}_{view}"
                if rows and guard not in st.session_state:  # 每个列表视图每次会话只记一次曝光
                    from core.feedback import item_key as _ik
                    call(api.record_impression_api, settings, {
                        "source_id": sid, "snapshot_id": snap_id, "view": view,
                        "item_keys": [_ik(r.get("url", ""), r.get("title", "")) for r in rows[:60]]})
                    st.session_state[guard] = True
                if rows:
                    fb_icon = {"interested": "👍", "not_interested": "👎", "later": "⏰"}
                    limit_key = f"res_n_{sid}_{snap_id}_{view}"
                    shown = st.session_state.get(limit_key, 30)
                    # 每个条目最近一次反馈（用于高亮按钮，避免重复记录同一动作）
                    latest: dict[str, str] = {}
                    for ev in (call(api.list_feedback_history, settings) or {"events": []})["events"]:
                        latest.setdefault(ev.get("item_key", ""), ev.get("action", ""))
                    for n, it in enumerate(rows[:shown]):
                        r1, r2, r3, r4, r5, r6 = st.columns([0.9, 7, 2.6, 0.9, 0.9, 1.4], vertical_alignment="center")
                        r1.write(it.get("rank") or n + 1)
                        r2.markdown(f"**{it.get('title') or '（无标题）'}**")
                        if it.get("summary"):
                            r2.caption(str(it["summary"])[:140])
                        meta = [str(x) for x in (it.get("heat"), it.get("published_at")) if x]
                        if rank_on and it.get("score") is not None:
                            meta.append(f"推荐分 {it['score']}")
                        r3.caption(" · ".join(m for m in meta if m))
                        ik = item_key(it.get("url") or "", it.get("title") or "")
                        cur = latest.get(ik, "")
                        for col, act, icon, tip in ((r4, "interested", "👍", "感兴趣"),
                                                    (r5, "not_interested", "👎", "不感兴趣")):
                            if col.button(icon, key=f"fb_{act}_{sid}_{snap_id}_{view}_{n}",
                                          type="primary" if cur == act else "secondary",
                                          help=f"标记为{tip}" + ("（当前状态）" if cur == act else "")):
                                if cur == act:
                                    st.toast(f"该条目已标记为{tip}")
                                elif call(api.record_feedback, settings, {
                                        "action": act, "source_id": sid, "url": it.get("url") or "",
                                        "title": it.get("title") or "", "view": view}) is not None:
                                    st.toast(f"已记录：{tip}")
                                    st.rerun()
                        with r6:
                            open_button(it.get("url"), f"op_res_{sid}_{snap_id}_{view}_{n}",
                                        sid=sid, title=it.get("title") or "", view=view)
                    if len(rows) > shown:
                        if st.button(f"显示更多（还有 {len(rows) - shown} 条）", key=f"more_{limit_key}"):
                            st.session_state[limit_key] = shown + 30
                            st.rerun()
                else:
                    st.info("没有匹配的条目。")
                _reading_panel(sid, snap["items"], view)


# ---------- 兴趣画像（第二阶段） ----------
def _profile_panel() -> None:
    data = call(api.get_profile, settings)
    if not data:
        return
    prof_data = data["profile"]
    explicit = prof_data.get("explicit", {})
    with st.expander("🎯 我的兴趣画像", expanded=False):
        st.caption(f"基于 {data['event_count']} 条反馈；画像更新于 {fmt_time(prof_data.get('updated_at'))}。"
                   "反馈变化后会自动重算，也可手动重算。")
        with st.form("profile_explicit_form"):
            topics = st.text_input("关注主题（逗号分隔，明确想看的）",
                                   value="、".join(explicit.get("topics", [])))
            exclude = st.text_input("屏蔽词（逗号分隔，出现即大幅降权）",
                                    value="、".join(explicit.get("exclude", [])))
            if st.form_submit_button("保存显式偏好"):
                if call(api.save_explicit_profile, settings, {"topics": topics, "exclude": exclude}) is not None:
                    st.toast("已保存显式偏好")
                    st.rerun()

        learned = prof_data.get("learned", {}).get("keywords", {})
        pos = sorted([(k, v) for k, v in learned.items() if v["score"] > 0], key=lambda kv: -kv[1]["score"])[:12]
        neg = sorted([(k, v) for k, v in learned.items() if v["score"] < 0], key=lambda kv: kv[1]["score"])[:12]
        c1, c2 = st.columns(2)
        c1.markdown("**学到的偏好**")
        c1.write("、".join(k for k, _ in pos) or "（暂无）")
        c2.markdown("**学到的排斥**")
        c2.write("、".join(k for k, _ in neg) or "（暂无）")

        llm = prof_data.get("llm") or {}
        if llm.get("summary"):
            st.info(f"LLM 归纳：{llm['summary']}（{fmt_time(llm.get('generated_at'))}，{llm.get('provider', '')}）")
            st.caption("关键词：" + "、".join(llm.get("keywords", [])) + "｜避开：" + "、".join(llm.get("avoid", [])))

        b1, b2, b3 = st.columns(3)
        if b1.button("重新计算（统计）", key="prof_rebuild"):
            if call(api.rebuild_profile, settings) is not None:
                st.rerun()
        llm_ok = data.get("llm_enabled", False)
        if b2.button("用 LLM 归纳兴趣", key="prof_llm", disabled=not llm_ok,
                     help="" if llm_ok else "已在配置中关闭"):
            with st.spinner("正在请模型归纳…"):
                out = call(api.summarize_profile_with_llm, settings)
            if out is not None:
                st.rerun()
        if b3.button("清空学到的画像", key="prof_clear"):
            if call(api.clear_learned_profile, settings) is not None:
                st.rerun()


# ---------- 服务管理：接收服务与专用浏览器（看板内启动/停止） ----------
def _services_panel() -> None:
    rs = call(api.receiver_status, settings)
    bs = call(api.browser_overview, settings)
    info = call(api.receiver_info, settings)
    if rs is None or bs is None or info is None:
        return
    with st.expander("🧩 服务管理：反馈接收服务与专用浏览器", expanded=False):
        st.caption("专用浏览器使用 info_hub 自己的数据目录，与系统浏览器隔离。"
                   "插件在该浏览器中加载一次（开发者模式 → 加载已解压的扩展程序 → extension/infohub-feedback），然后在下方“插件配对”一键完成连接。")

        # --- 接收服务 ---
        st.markdown("**反馈接收服务**")
        mode_text = {"managed": "运行中（由看板启动）", "external": "运行中（非看板启动）",
                     "stopped": "未运行"}[rs["mode"]]
        st.write(f"状态：{mode_text} · 端口 {rs['port']}" + (f" · pid {rs['pid']}" if rs.get("pid") else ""))
        if rs["mode"] == "stopped" and not rs["port_free"]:
            st.warning(f"端口 {rs['port']} 已被其他程序占用，请换一个端口。")
        c1, c2, c3 = st.columns([2, 1, 1])
        new_port = c1.number_input("端口（1024–65535）", min_value=1024, max_value=65535,
                                   value=int(rs["port"]), key="rcv_port_input")
        if c2.button("保存端口", key="rcv_save_port"):
            out = call(api.receiver_set_port, settings, {"port": int(new_port)})
            if out is not None:
                st.toast(out["note"])
                st.rerun()
        running = rs["mode"] != "stopped"
        if c3.button("停止服务", key="rcv_stop", disabled=rs["mode"] != "managed"):
            if call(api.receiver_stop, settings) is not None:
                st.toast("接收服务已停止")
                st.rerun()
        if st.button("启动接收服务", key="rcv_start", disabled=running, type="primary"):
            with st.spinner("正在启动接收服务…"):
                out = call(api.receiver_start, settings, {})
            if out is not None:
                st.toast("接收服务已启动")
                st.rerun()
        with st.expander("接收服务日志（最近 20 行）"):
            st.code("\n".join(rs["log_tail"]) or "（暂无日志）", language=None)

        st.divider()
        # --- 插件配对（免手填令牌） ---
        st.markdown("**插件配对**")
        ps = call(api.pairing_status, settings) or {"open": False, "remaining_seconds": 0, "paired": None}
        if rs["mode"] == "stopped":
            st.warning("接收服务未运行，请先启动接收服务再配对。")
        if ps["open"]:
            st.info(f"配对窗口已开启，约剩 {max(1, ps['remaining_seconds'] // 60)} 分钟。"
                    "请在专用浏览器中刷新任意网页（或点一下页面侧边的反馈按钮），插件会自动取得令牌。"
                    "完成后刷新本页可看到“已配对”。")
            if st.button("取消配对", key="pair_cancel"):
                call(api.pairing_cancel, settings)
                st.rerun()
        else:
            if ps["paired"]:
                st.success(f"已配对（{fmt_time(ps['paired'].get('time'))}）。令牌重新生成或更换浏览器后，可再次配对。")
            else:
                st.caption("尚未配对。点击下方按钮后，插件会在 5 分钟内自动取得令牌，无需手动填写。")
            if st.button("配对插件（开启 5 分钟窗口）", key="pair_start", type="primary",
                         disabled=rs["mode"] == "stopped"):
                if call(api.pairing_start, settings) is not None:
                    st.rerun()
        st.caption(f"插件默认连接端口 18765；若修改过端口，请在扩展选项中同步一次。当前接收服务：`{info['url']}`")
        with st.expander("手动配置（排错用）"):
            st.write(f"令牌文件：`{info['token_path']}`")
            st.code(info["token"], language=None)

        st.divider()
        # --- 专用浏览器 ---
        st.markdown("**专用浏览器**")
        state_text = {"running": f"运行中（pid {', '.join(map(str, bs['pids']))}）",
                      "stopped": "未运行",
                      "unknown": "无法检测（请安装 psutil）"}[bs["state"]]
        st.write(f"状态：{state_text}")
        if bs["state"] == "running":
            if bs["debug_ready"]:
                st.write(f"调试端口 {bs['debug_port']}：可用（抓取可附着到该浏览器）")
            else:
                st.warning("专用浏览器在运行，但没有开启调试端口，CDP 抓取不可用。"
                           "请关闭它后用“启动专用浏览器”重新启动。")
        st.write(f"数据目录：`{bs['profile']}`" + ("" if bs["profile_exists"] else "（首次启动时创建）"))
        mode_labels = {"launch": "独立启动（旧方式）", "cdp": "附着到专用浏览器"}
        mode_choice = st.radio("抓取方式", list(mode_labels), index=list(mode_labels).index(bs["scrape_mode"]),
                               format_func=lambda m: mode_labels[m], horizontal=True, key="db_mode_radio")
        if st.button("保存抓取方式", key="db_save_mode", disabled=mode_choice == bs["scrape_mode"]):
            if call(api.browser_set_scrape_mode, settings, {"scrape_mode": mode_choice}) is not None:
                st.toast("抓取方式已保存")
                st.rerun()
        if bs["scrape_mode"] == "cdp":
            st.caption("抓取时会在专用浏览器中打开后台标签页，完成后关闭。专用浏览器必须处于运行状态。")
        if bs["executable"]:
            st.write(f"浏览器程序：`{bs['executable']}`")
        else:
            st.warning("未找到 Chrome 或 Edge，请在下方填写浏览器程序的完整路径。")
        exe_input = st.text_input("浏览器程序路径（留空则自动查找）", value=bs["executable"],
                                  key="db_exe_input")
        e1, e2, e3 = st.columns(3)
        if e1.button("保存路径", key="db_save_exe"):
            if call(api.browser_set_executable, settings, {"executable": exe_input}) is not None:
                st.toast("浏览器路径已保存")
                st.rerun()
        if e2.button("启动专用浏览器", key="db_start", disabled=bs["state"] == "running"):
            if call(api.browser_start, settings) is not None:
                st.toast("专用浏览器已启动")
                st.rerun()
        if e3.button("关闭专用浏览器", key="db_stop", disabled=bs["state"] != "running",
                     help="只关闭本项目专用数据目录对应的浏览器窗口，不影响系统浏览器。"):
            if call(api.browser_stop, settings) is not None:
                st.toast("专用浏览器已关闭")
                st.rerun()

# ---------- 推荐效果评估（第四阶段） ----------
def _evaluation_panel() -> None:
    days = 30
    data = call(api.evaluation_report, settings, days)
    if not data:
        return
    with st.expander("📊 推荐效果评估（近 30 天）", expanded=False):
        v = data["views"]
        rows = []
        for name, key in (("按兴趣排序", "ranked"), ("原始顺序", "plain")):
            x = v[key]
            pct = lambda r: "—" if r is None else f"{r:.0%}"  # noqa: E731
            rows.append({"视图": name, "曝光条目": x["impressions"], "点击率": pct(x["ctr"]),
                         "反馈数": x["feedback"], "感兴趣": pct(x["interested_rate"]),
                         "不感兴趣": pct(x["not_interested_rate"]), "稍后看": pct(x["later_rate"])})
        st.dataframe(pd.DataFrame(rows), hide_index=True)
        for msg in data.get("recommendations", []):
            st.info(msg)
        by_t = data["candidates"]["by_template"]
        if by_t:
            st.caption("候选信息源采纳情况")
            st.dataframe(pd.DataFrame([{
                "模板": c["name"], "生成": c["generated"], "加入": c["accepted"], "忽略": c["rejected"],
                "待确认": c["pending"], "采纳率": "—" if c["adoption_rate"] is None else f"{c['adoption_rate']:.0%}",
            } for c in by_t.values()]), hide_index=True)
        by_r = data["candidates"]["by_reason"]
        if by_r:
            names = {"explicit": "显式主题", "learned": "学到的偏好"}
            st.caption("按查询词来源：" + "；".join(
                f"{names.get(k, k)} 加入 {x['accepted']} / 忽略 {x['rejected']}" for k, x in by_r.items()))

# ---------- 候选收件箱（第三阶段） ----------
def _candidates_panel() -> None:
    data = call(api.list_candidates_api, settings)
    if not data:
        return
    pending = [c for c in data["candidates"] if c["status"] == "pending"]
    with st.expander(f"📥 候选信息源（待确认 {len(pending)} 条）", expanded=bool(pending)):
        if data.get("config_error"):
            st.error(data["config_error"])
            return
        if not data["templates"]:
            st.info("还没有搜索模板。请在 config/discovery.yaml 中配置 templates 后再生成候选。")
        lim = data.get("limits", {})
        st.caption(f"候选默认停用，只有点“加入信息源”才会抓取。每日最多生成 {lim.get('daily')} 条，"
                   f"待确认最多 {lim.get('pending')} 条。")
        if st.button("根据我的兴趣生成候选", key="cand_gen", disabled=not data["templates"]):
            out = call(api.generate_candidates, settings)
            if out is not None:
                st.toast(f"新生成 {out['added']} 条候选")
                st.rerun()
        if not pending:
            st.caption("当前没有待确认的候选。")
        for c in pending:
            with st.container(border=True):
                st.markdown(f"**{c['template_name']}** · 查询词：`{c['query']}`")
                st.caption(f"原因：{c['reason']} · 生成于 {fmt_time(c['created_at'])}")
                st.code(c["url"], language=None)
                open_button(c["url"], f"op_cand_{c['id']}", label="在专用浏览器中打开该地址")
                b1, b2 = st.columns(2)
                if b1.button("加入信息源", key=f"cand_ok_{c['id']}", type="primary"):
                    if call(api.accept_candidate, settings, c["id"]) is not None:
                        st.toast(f"已加入信息源：{c['id']}")
                        st.rerun()
                if b2.button("忽略", key=f"cand_no_{c['id']}"):
                    if call(api.reject_candidate, settings, c["id"]) is not None:
                        st.rerun()

# ---------- 阅读与反馈（第一阶段） ----------
def _reading_panel(sid: str, items: list[dict], view: str = "plain") -> None:
    from core.feedback import ACTIONS, REASONS

    cands = [it for it in items if it.get("url")]
    if not cands:
        return
    st.divider()
    st.subheader("阅读与反馈")
    st.caption("每条结果右侧的“打开”按钮会在专用浏览器中打开原文，并在页面侧边显示反馈工具条；"
               "也可以在这里选择条目直接记录反馈。")
    pick = st.selectbox("选择条目", list(range(len(cands))),
                        format_func=lambda i: (cands[i].get("title") or cands[i]["url"])[:60],
                        key=f"rd_pick_{sid}")
    it = cands[pick]
    url, title = it["url"], it.get("title") or ""
    key = item_key(url, title)
    open_button(url, f"rd_dedicated_{key}", sid=sid, title=title, view=view)

    with st.form(f"fb_form_{key}", clear_on_submit=True):
        st.caption("反馈（原因标签与备注均可不填）")
        reasons = st.multiselect("原因", list(REASONS), format_func=lambda r: REASONS[r],
                                 key=f"fb_reason_{key}")
        note = st.text_input("备注", key=f"fb_note_{key}")
        c1, c2, c3 = st.columns(3)
        pressed = None
        if c1.form_submit_button("👍 感兴趣"):
            pressed = "interested"
        if c2.form_submit_button("👎 不感兴趣"):
            pressed = "not_interested"
        if c3.form_submit_button("⏰ 稍后看"):
            pressed = "later"
    if pressed:
        body = {"action": pressed, "source_id": sid, "url": url, "title": title,
                "reasons": reasons, "note": note, "open_mode": "dedicated", "view": view}
        if call(api.record_feedback, settings, body) is not None:
            st.toast(f"已记录：{ACTIONS[pressed]}")

    fb = call(api.item_feedback, settings, key)
    if fb:
        counts = " · ".join(f"{ACTIONS[a]} {n}" for a, n in fb["counts"].items())
        with st.expander(f"该条目的反馈记录（{counts}）"):
            if not fb["events"]:
                st.caption("还没有反馈。")
            for e in fb["events"]:
                rs = "、".join(REASONS.get(r, r) for r in e.get("reasons", []))
                extra = f"｜原因：{rs}" if rs else ""
                extra += f"｜备注：{e['note']}" if e.get("note") else ""
                st.write(f"- {fmt_time(e['time'])} · {ACTIONS.get(e['action'], e['action'])}{extra}")


with tab_results:
    _results_tab()


# ---------- 反馈记录 ----------
def _feedback_history_tab() -> None:
    from core.feedback import ACTIONS, REASONS
    icons = {"interested": "👍", "not_interested": "👎", "later": "⏰"}
    base = call(api.list_feedback_history, settings)
    if base is None:
        return
    if not base["total"]:
        st.info("还没有反馈记录。在专用浏览器里点页面侧边的反馈按钮，或在“抓取结果”下方的反馈表单中提交后，这里会显示。")
        return
    names = {x["id"]: x["name"] for x in (call(api.list_sources, settings) or {"sources": []})["sources"]}
    names["manual"] = "手动反馈（非看板条目）"
    src_label = lambda sid: f"{names.get(sid, sid)}（{sid}）"  # noqa: E731

    f1, f2, f3, f4 = st.columns([2, 3, 2, 2])
    act = f1.selectbox("动作", [""] + list(ACTIONS), key="fh_action",
                       format_func=lambda a: "全部" if not a else f"{icons[a]} {ACTIONS[a]}")
    src = f2.selectbox("信息源", [""] + base["sources"], key="fh_source",
                       format_func=lambda x: "全部" if not x else src_label(x))
    days = f3.selectbox("时间范围", [0, 1, 7, 30, 90], key="fh_days",
                        format_func=lambda d: "全部" if not d else f"最近 {d} 天")
    kw = f4.text_input("关键词", key="fh_kw", help="匹配标题、备注和链接，不区分大小写")
    res = call(api.list_feedback_history, settings, action=act, source_id=src, keyword=kw, days=days)
    if res is None:
        return
    events = res["events"]
    m = st.columns(4)
    m[0].metric("筛选结果", len(events), help=f"全部记录 {res['total']} 条")
    for col, a in zip(m[1:], ACTIONS):
        col.metric(f"{icons[a]} {ACTIONS[a]}", res["counts"][a])
    if not events:
        st.info("没有符合筛选条件的记录。")
        return

    limit_key = f"fh_n_{act}_{src}_{days}_{kw}"
    shown = st.session_state.get(limit_key, 30)
    for n, e in enumerate(events[:shown]):
        with st.container(border=True):
            c1, c2 = st.columns([9, 1.6], vertical_alignment="center")
            c1.markdown(f"{icons.get(e['action'], '')} **{ACTIONS.get(e['action'], e['action'])}** · "
                        f"{e.get('title') or e.get('url') or '（无标题）'}")
            parts = [fmt_time(e.get("time")), src_label(e.get("source_id", ""))]
            if e.get("reasons"):
                parts.append("原因：" + "、".join(REASONS.get(r, r) for r in e["reasons"]))
            if e.get("note"):
                parts.append("备注：" + e["note"])
            c1.caption(" · ".join(parts))
            with c2:
                src_id = e.get("source_id", "")
                # 手动反馈没有看板条目，只打开原始链接；其余按条目打开（工具条可用）
                open_button(e.get("url"), f"op_fh_{e.get('id', n)}",
                            sid="" if src_id == "manual" else src_id, title=e.get("title") or "",
                            view=e.get("view") or "")
    if len(events) > shown:
        if st.button(f"显示更多（还有 {len(events) - shown} 条）", key=f"more_{limit_key}"):
            st.session_state[limit_key] = shown + 30
            st.rerun()


with tab_feedback:
    _feedback_history_tab()


# ---------- 运行记录 ----------
with tab_runs:
    day = st.date_input("日期", value=date.today(), key="runs_day")
    runs = (call(api.list_runs, settings, day.isoformat()) or {"runs": []})["runs"]
    if runs:
        st.dataframe(pd.DataFrame(runs).rename(columns={
            "time": "时间", "source_id": "信息源", "status": "结果", "detail": "详情"}),
            hide_index=True, use_container_width=True)
    else:
        st.info("当天没有运行记录。")


# ---------- 调度状态 ----------
@st.fragment(run_every=30)
def _status_tab():
    res = call(api.tick_status, settings)
    if not res:
        st.info("无法读取调度状态。")
        return
    tk = res["tick"]
    interval = res["tick_interval_minutes"]
    if tk.get("stalled"):
        st.error(f"疑似卡住：tick 正在运行，已 {tk.get('running_for_sec', 0) // 60} 分钟未结束，"
                 f"当前源 {tk.get('current_source') or '（无）'}。")
    elif tk.get("loop_suspected_dead"):
        st.error("后台调度可能已停止：超过 2 个 tick 周期没有任何活动。"
                 "请确认 Streamlit 看板进程仍在运行，并查看它的终端输出。")
    elif tk.get("running"):
        st.info(f"tick 正在运行，当前源：{tk.get('current_source') or '（无）'}")
    else:
        st.success("后台调度正常（空闲等待下一次 tick）")

    st.markdown("""<style>
    div[data-testid="stMetricValue"] { font-size: 1.0rem !important; line-height: 1.3 !important;
        white-space: normal !important; overflow: visible !important; word-break: break-all; }
    div[data-testid="stMetricLabel"] p { font-size: 0.8rem !important; white-space: normal !important; }
    </style>""", unsafe_allow_html=True)
    with st.form("tick_interval_form"):
        ti1, ti2 = st.columns([3, 1])
        new_minutes = ti1.number_input("后台 tick 间隔（分钟）", min_value=1, max_value=1440,
                                       value=int(interval), step=1, key="tick_interval_input",
                                       help="修改后约 5 秒内生效，无需重启看板")
        save_interval = ti2.form_submit_button("保存间隔")
    if save_interval:
        if call(api.set_tick_interval, settings, int(new_minutes)) is not None:
            st.session_state["tick_interval_msg"] = f"后台 tick 间隔已改为 {int(new_minutes)} 分钟，约 5 秒内生效"
            st.rerun()
    if msg := st.session_state.pop("tick_interval_msg", None):
        st.success(msg)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("最后活动", fmt_time(tk.get("last_activity_at")))
    c2.metric("上次 tick 结束", fmt_time(tk.get("tick_finished_at")))
    c3.metric("tick 间隔", f"{interval} 分钟")
    c4.metric("调度文件锁", res["scheduler_lock"])
    st.caption("每 30 秒自动刷新。Windows 下文件锁不可检测，仅 Linux/macOS 显示锁状态。")

    st.subheader("各信息源")
    if res["sources"]:
        st.dataframe(pd.DataFrame([{
            "信息源": f"{r['name']}（{r['id']}）",
            "状态": "已暂停" if r["paused"] else ("启用" if r["enabled"] else "停用"),
            "频率": r["every"],
            "上次运行": fmt_time(r["last_run"]) if r["last_run"] else "从未",
            "下次到期": r["next_due"] if r["next_due"] in ("立即", "频率配置错误") else fmt_time(r["next_due"]),
            "连续失败": r["failures"],
            "最近结果": ("成功" if r["last_outcome"] and r["last_outcome"]["ok"]
                         else ("失败" if r["last_outcome"] else "")),
            "最近错误": ((r["last_outcome"] or {}).get("error") or "") if (r["last_outcome"] and not r["last_outcome"]["ok"])
                        else (r["last_error"] if r["failures"] else "") or "",
        } for r in res["sources"]]), hide_index=True, use_container_width=True,
            column_config={"信息源": st.column_config.TextColumn(width="large"),
                           "最近错误": st.column_config.TextColumn(width="large")})
    if res["reports"]:
        st.subheader("报告计划")
        st.dataframe(pd.DataFrame([{
            "计划": r["id"],
            "类型": "按间隔" if r.get("every") else f"每日 {r.get('at')}",
            "间隔/时刻": r.get("every") or r.get("at") or "",
            "窗口": r.get("window") or "",
            "上次生成": fmt_time(r["last_run"]) if r["last_run"] else "从未",
        } for r in res["reports"]]), hide_index=True, use_container_width=True)
        for r in res["reports"]:
            with st.form(f"report_form_{r['id']}"):
                st.markdown(f"**修改报告计划：{r['id']}**")
                rc1, rc2 = st.columns(2)
                body: dict[str, Any] = {}
                if r.get("every"):
                    body["every"] = rc1.text_input("间隔（如 3h、30m、1d）", value=r["every"],
                                                   key=f"rep_every_{r['id']}")
                if r.get("at"):
                    body["at"] = rc1.text_input("每日时刻（HH:MM）", value=r["at"], key=f"rep_at_{r['id']}")
                if r.get("window") or r.get("every") or r.get("at"):
                    body["window"] = rc2.text_input("统计窗口（如 3h、24h）", value=r.get("window") or "",
                                                    key=f"rep_win_{r['id']}")
                if st.form_submit_button("保存报告计划"):
                    if call(api.set_report_schedule, settings, r["id"], body) is not None:
                        st.session_state["report_msg"] = f"报告计划 {r['id']} 已保存，下一轮 tick 起生效"
                        st.rerun()
        if msg := st.session_state.pop("report_msg", None):
            st.success(msg)
    errors = tk.get("errors") or []
    st.subheader(f"最近错误（{len(errors)}）")
    if errors:
        st.dataframe(pd.DataFrame([{"时间": fmt_time(e["at"]), "位置": e["where"], "信息": e["message"]}
                                   for e in reversed(errors)]), hide_index=True, use_container_width=True,
                     column_config={"信息": st.column_config.TextColumn(width="large"),
                                    "时间": st.column_config.TextColumn(width="medium")})
    else:
        st.info("没有记录到错误。")


with tab_status:
    _status_tab()


# ---------- 登录 ----------
with tab_login:
    st.caption("登录需要在终端执行：系统会打开有头浏览器，完成登录后回到终端按回车。登录态保存在共享 profile 中。")
    sites = (call(api.login_status, settings) or {"sites": []})["sites"]
    if not sites:
        st.info("config/sites.yaml 中没有登记站点。")
    else:
        st.dataframe(pd.DataFrame([{
            "站点": s["name"],
            "状态": {"logged_in": "已登录", "expired": "需要重新登录", "unknown": "未登录过"}.get(s["status"], s["status"]),
            "最近登录": fmt_time(s.get("last_login_at")),
            "命令": s["login_command"],
            "错误": s.get("last_error") or "",
        } for s in sites]), hide_index=True, use_container_width=True)
