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
INBOX_LABEL = {
    "approve": "待批准",
    "answer": "待答复",
    "escalate": "升级人工",
    "failed": "失败",
    "blocked": "未实现",
    "stale": "需重跑",
    "budget_pause": "预算暂停",
    "patrol_finding": "巡检整改项",
}
INBOX_ICON = {"block": "🔴", "warn": "🟡", "info": "🔵"}
ASSIST_LABEL = {"polish": "润色", "expand": "扩写", "continue": "续写"}
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


# ── 只读表格 ─────────────────────────────────────────────────────────────────

def _cell_to_text(value):
    """单元格统一为文本：None / NaN 变成空串。混合类型（如整数与空串）会让 Arrow 无法转换。"""
    if value is None:
        return ""
    try:
        if value != value:  # NaN
            return ""
    except Exception:  # noqa: BLE001
        pass
    return str(value)


def arrow_safe(df):
    """返回一份可被 Arrow 序列化的副本：所有 object 列（以及对象索引）统一转为文本。

    列表（字典列表）先转为 DataFrame；其它类型原样返回，交给 st.dataframe 处理。
    """
    import pandas as pd

    if isinstance(df, list):
        df = pd.DataFrame(df)
    if not isinstance(df, pd.DataFrame):
        return df
    out = df.copy()
    for col in out.columns:
        if out[col].dtype == object:
            out[col] = out[col].map(_cell_to_text)
    if out.index.dtype == object:
        out.index = out.index.map(_cell_to_text)
    return out


def show_df(df, **kwargs):
    """只读表格的统一入口：先做 Arrow 兼容处理，再交给 st.dataframe。"""
    return st.dataframe(arrow_safe(df), **kwargs)
