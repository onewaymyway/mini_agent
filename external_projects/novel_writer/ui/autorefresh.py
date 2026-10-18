"""自动刷新：后台任务推进时，驾驶舱与任务页自动更新（流水线、单步操作、待处理、写入记录、任务列表）。

机制：一个每 AUTO_REFRESH_SECONDS 秒运行一次的轻量 fragment，只读取状态文件并计算签名；
签名与上次不同才触发整页刷新。没有变化时什么也不做，因此不会打断作者正在填写的表单或输入框。
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Callable, Dict

import streamlit as st

from novel_writer import state as st_state
from novel_writer.workspace import Workspace

AUTO_REFRESH_SECONDS = 3
_SESSION_PREFIX = "_autorefresh_sig"


def _digest(payload: Dict[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def workspace_signature(ws_root: str) -> str:
    """单本小说的状态签名。心跳时间戳每次都会变，故不纳入，否则会一直刷新。"""
    ws = Workspace(ws_root)
    state = st_state.load_state(ws)
    steps = {
        sid: [e.get("status"), e.get("attempts"), e.get("note"), e.get("summary"), e.get("error")]
        for sid, e in state.get("steps", {}).items()
    }
    worker = st_state.read_worker(ws)
    control = st_state.read_control(ws)
    history = ws.studio / st_state.WRITE_HISTORY_FILE
    return _digest({
        "steps": steps,
        "worker": [worker.get("state"), worker.get("step"), worker.get("pid")],
        "control": [control.get("command"), control.get("run_step")],
        "history_bytes": history.stat().st_size if history.is_file() else 0,
    })


def tasks_signature() -> str:
    """任务页的签名：所有小说的任务列表（不含时间戳类字段）。"""
    from novel_writer import tool_api

    res = tool_api.list_tasks()
    rows = res["data"]["tasks"] if res.get("ok") else []
    return _digest([[r["workspace"], r["kind"], r.get("step"), r.get("status")] for r in rows])


@st.fragment(run_every=AUTO_REFRESH_SECONDS)
def _watch(key: str, signature: Callable[[], str]) -> None:
    sig = signature()
    previous = st.session_state.get(key)
    st.session_state[key] = sig
    if previous is not None and previous != sig:
        st.rerun(scope="app")


def enable_auto_refresh(ws_root: str) -> None:
    """驾驶舱：状态变化时自动刷新整页。在页面里调用一次即可。"""
    _watch(f"{_SESSION_PREFIX}:{ws_root}", lambda: workspace_signature(ws_root))


def enable_tasks_auto_refresh() -> None:
    """任务页：任务列表变化时自动刷新整页。"""
    _watch(f"{_SESSION_PREFIX}:tasks", tasks_signature)
