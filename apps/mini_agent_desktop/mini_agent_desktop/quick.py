"""快速面板的纯逻辑（不依赖 Qt）。

设计要点（见 next_doc/windows_desktop_resident_plan.md §3 与 S2）：
  - 快速面板使用**专属 session**（QUICK_SESSION_ID），daemon 会为它单独建一个
    Agent，因此主会话正在跑长任务时，快速提问不会排在后面；
  - 没有新增任何服务端接口，只组合已有的 /v1/chat、/v1/turns/{id}、
    /v1/history、/v1/interrupt，并都带上 session_id 参数路由到专属 Agent；
  - 结果获取：轮询 turn 状态直到终态，再从专属 session 的历史里取最后一条
    assistant 文本。不走 SSE，避免为一次提问维持长连接。
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Iterable, Optional

import httpx

from mini_agent_desktop.api import DaemonApi

QUICK_SESSION_ID = "desktop-quick"

TERMINAL_STATES = frozenset({"done", "error", "interrupted"})
PENDING = "pending"          # daemon 还没把该 turn 放进列表（排队中）
RECENT_LIMIT = 10


@dataclass
class TaskOutcome:
    turn_id: str
    state: str           # done / error / interrupted / timeout
    reply: str = ""


def _text_of(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text", "")))
        return "\n".join(p for p in parts if p)
    return ""


def extract_reply(messages: Iterable[dict]) -> str:
    """从历史里取最后一条带文本的 assistant 消息（纯工具调用的消息跳过）。"""
    for msg in reversed(list(messages)):
        if not isinstance(msg, dict) or msg.get("role") != "assistant":
            continue
        text = _text_of(msg.get("content"))
        if text.strip():
            return text
    return ""


class QuickSession:
    """专属 session 的薄客户端：每个实例持有一个 DaemonApi。"""

    def __init__(self, api: DaemonApi, session_id: str = QUICK_SESSION_ID) -> None:
        self._api = api
        self.session_id = session_id

    def close(self) -> None:
        self._api.close()

    def submit(self, text: str) -> str:
        text = (text or "").strip()
        if not text:
            raise ValueError("内容为空")
        return self._api.chat(text, self.session_id)

    def turn_state(self, turn_id: str) -> str:
        """返回 running/done/error/interrupted；尚未出现在列表里时返回 PENDING。"""
        try:
            return str(self._api.turn(turn_id, self.session_id).get("state") or PENDING)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return PENDING
            raise

    def reply(self) -> str:
        return extract_reply(self._api.history(self.session_id, limit=20))

    def interrupt(self) -> None:
        self._api.interrupt(self.session_id)

    def recent_turns(self, limit: int = RECENT_LIMIT) -> list[dict]:
        """专属 session 的最近任务，新的在前。"""
        rows = []
        for t in self._api.turns(self.session_id)[:limit]:
            rows.append({
                "turn_id": str(t.get("turn_id", "")),
                "input": str(t.get("input", "")),
                "state": str(t.get("state", "")),
            })
        return rows


def run_task(
    session: QuickSession,
    text: str,
    *,
    on_status: Callable[[str], None] = lambda _s: None,
    poll_interval: float = 1.5,
    timeout: float = 1800.0,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> TaskOutcome:
    """提交一条消息并等待其结束。可在后台线程中调用。

    on_status 每次轮询都会收到当前状态字符串（running/pending/...）。
    超时只是放弃等待，不会中断 daemon 上的 turn。
    """
    turn_id = session.submit(text)
    on_status("submitted")
    deadline = clock() + timeout
    state = PENDING
    while True:
        state = session.turn_state(turn_id)
        if state in TERMINAL_STATES:
            break
        on_status(state)
        if clock() >= deadline:
            return TaskOutcome(turn_id=turn_id, state="timeout")
        sleep(poll_interval)
    reply = session.reply() if state == "done" else ""
    return TaskOutcome(turn_id=turn_id, state=state, reply=reply)


STATE_LABELS = {
    "submitted": "已提交",
    PENDING: "排队中…",
    "running": "运行中…",
    "done": "完成",
    "error": "出错",
    "interrupted": "已中断",
    "timeout": "仍在运行（已停止等待，可稍后在最近任务中查看）",
}


def state_label(state: str) -> str:
    return STATE_LABELS.get(state, state)
