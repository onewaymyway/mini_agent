"""⚙ 任务：书架上所有小说的后台任务总览，并可停止、取消排队、清除残留、删除请求。"""

from __future__ import annotations

import streamlit as st

from novel_writer import tool_api
from ui.components import show_error

_KIND_ICON = {"running": "🔵", "stale": "🟤", "queued": "🟡", "request": "⚪"}


def page_tasks() -> None:
    st.header("⚙ 后台任务")
    st.caption("书架上所有小说的后台运行、排队和待运行请求。停止是协作式的：当前步骤结束后生效。")
    if st.button("🔄 刷新", key="tk_refresh"):
        st.rerun()

    res = tool_api.list_tasks()
    if not res["ok"]:
        show_error(res)
        return
    tasks = res["data"]["tasks"]
    if not tasks:
        st.success("当前没有后台任务、排队指令或待运行请求。")
        return

    running = sum(1 for t in tasks if t["kind"] == "running")
    queued = sum(1 for t in tasks if t["kind"] == "queued")
    stale = sum(1 for t in tasks if t["kind"] == "stale")
    requests = sum(1 for t in tasks if t["kind"] == "request")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("运行中", running)
    c2.metric("排队中", queued)
    c3.metric("残留", stale)
    c4.metric("待运行请求", requests)

    for i, t in enumerate(tasks):
        key = f"tk_{i}"
        with st.container(border=True):
            left, right = st.columns([5, 2])
            with left:
                icon = _KIND_ICON.get(t["kind"], "•")
                st.markdown(f"{icon} **{t['kind_label']}**　·　{t['title']}")
                step = t.get("step") or ""
                title = t.get("step_title") or ""
                st.caption(f"{step}　{title}".strip() + (f"　｜　{t['detail']}" if t.get("detail") else ""))
            with right:
                if t["kind"] == "running":
                    if st.button("⏹ 停止", key=f"{key}_stop", use_container_width=True):
                        r = tool_api.stop_novel(t["workspace"])
                        _after(r, "已请求停止，当前步骤结束后生效。")
                elif t["kind"] == "queued":
                    if st.button("✖ 取消排队", key=f"{key}_cancel", use_container_width=True):
                        _after(tool_api.cancel_queued(t["workspace"]), "已取消排队。")
                elif t["kind"] == "stale":
                    if st.button("🧹 清除残留", key=f"{key}_clear", use_container_width=True):
                        _after(tool_api.clear_stale_worker(t["workspace"]), "已清除残留状态。")
                elif t["kind"] == "request":
                    if st.button("🗑 删除请求", key=f"{key}_del", use_container_width=True):
                        _after(tool_api.delete_request(t["workspace"], t["request_kind"], t["request_id"]),
                               "已删除该请求。")


def _after(res: dict, ok_text: str) -> None:
    if res["ok"]:
        st.success(ok_text)
        st.rerun()
    else:
        show_error(res)
