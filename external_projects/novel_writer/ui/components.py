"""界面共用的小部件与格式化（M2）。业务数据一律经 tool_api 获取。"""

from __future__ import annotations

import streamlit as st

from novel_writer import tool_api

STATUS_LABEL = {
    "pending": "⚪ 待运行",
    "running": "🔵 运行中",
    "waiting_review": "🟡 待批准",
    "waiting_user": "🟠 待答复",
    "passed": "🟢 已通过",
    "failed": "🔴 失败",
    "skipped": "⚪ 已跳过",
    "stale": "🟣 需重跑",
    "interrupted": "🟤 已中断",
    "blocked": "⛔ 未实现",
}
GATE_LABEL = {"manual": "手动", "review": "审批", "auto": "自动", "skip": "跳过"}
REASON_LABEL = {
    "completed": "流水线已完成",
    "waiting_review": "等待你批准一步",
    "waiting_user": "等待你回答一步的问题",
    "manual": "前沿是手动步骤，请点 ▶ 运行",
    "budget_chapters": "达到本次章数上限（已暂停，可直接续跑）",
    "budget_calls": "达到本次 LLM 调用上限（已暂停，可直接续跑）",
    "budget_time": "达到本次时长上限（已暂停，可直接续跑）",
    "paused": "已暂停",
    "stopped": "已停止",
    "failed": "有步骤失败，请查看后重跑",
    "blocked": "遇到尚未实现的步骤",
    "single_done": "已运行一步",
    "running": "有步骤正在运行",
    "already_running": "已有 worker 在运行",
}


def current_ws():
    """当前选中的小说工作目录（字符串），没有则返回 None。"""
    return st.session_state.get("ws") or None


def show_error(result: dict) -> None:
    st.error(result.get("error", {}).get("message", "未知错误"))


def require_ws():
    ws = current_ws()
    if not ws:
        st.info("还没有选中小说。请到「📚 书架」打开一本，或用「➕ 新建小说」创建。")
    return ws


def open_novel(path: str) -> None:
    st.session_state["ws"] = path
    st.session_state["nav"] = "🧭 驾驶舱"


def novel_title(path: str) -> str:
    res = tool_api.get_novel(path)
    if res["ok"]:
        return res["data"]["recover"]["title"] or path
    return path
