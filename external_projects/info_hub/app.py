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
    start_tick_loop(settings, stop, settings.tick_interval_minutes * 60)
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
tab_reports, tab_sources, tab_add, tab_runs, tab_login = st.tabs(
    ["报告", "信息源", "添加信息源", "运行记录", "登录"])


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
                        items.append((src["source_id"], it["id"], it["title"]))
            if not items:
                st.caption("本报告没有条目。")
            else:
                pick = st.selectbox("条目", range(len(items)),
                                    format_func=lambda i: items[i][2][:60], key="item_pick")
                src_id, item_id, title = items[pick]
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
        cat_names = {c["id"]: c["name"] for c in data["categories"]}
        df = pd.DataFrame([{
            "启用": s["enabled"],
            "名称": s["name"],
            "ID": s["id"],
            "分类": cat_names.get(s["category"], s["category"]),
            "类型": f"{s['kind']} / {s['adapter']}" + (" · 需登录" if s.get("browser_site") else ""),
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
