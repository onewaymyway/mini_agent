"""daemon HTTP/SSE 客户端（同步 httpx，供后台线程使用）。"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from typing import Iterable, Iterator, Optional

import httpx


@dataclass
class SseEvent:
    event: str = "message"
    id: str = ""
    data: dict = field(default_factory=dict)


def parse_sse(lines: Iterable[str]) -> Iterator[SseEvent]:
    """把 SSE 文本行流解析成事件。

    - 空行 = 一个事件结束；
    - 以 ":" 开头的是注释（daemon 每 ~20s 发 ": heartbeat ..." 保活），
      产出 event="heartbeat"，供调用方做保活/兜底刷新；
    - data 不是合法 JSON 时放进 {"raw": ...}，不抛异常。
    """
    event, eid, data_lines = "message", "", []
    for raw in lines:
        line = raw.rstrip("\r\n")
        if line == "":
            if data_lines:
                text = "\n".join(data_lines)
                try:
                    data = json.loads(text)
                    if not isinstance(data, dict):
                        data = {"value": data}
                except ValueError:
                    data = {"raw": text}
                yield SseEvent(event=event, id=eid, data=data)
            event, eid, data_lines = "message", "", []
            continue
        if line.startswith(":"):
            yield SseEvent(event="heartbeat")
            continue
        name, _, value = line.partition(":")
        value = value[1:] if value.startswith(" ") else value
        if name == "event":
            event = value
        elif name == "id":
            eid = value
        elif name == "data":
            data_lines.append(value)


class DaemonApi:
    def __init__(self, base_url: str, token: Optional[str], timeout: float = 5.0,
                 transport: Optional[httpx.BaseTransport] = None) -> None:
        self._base = base_url.rstrip("/")
        self._timeout = timeout
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        self._client = httpx.Client(headers=headers, transport=transport,
                                    timeout=httpx.Timeout(timeout, read=45.0))

    def close(self) -> None:
        self._client.close()

    def _get(self, path: str, params: Optional[dict] = None) -> dict:
        r = self._client.get(f"{self._base}{path}", params=params, timeout=self._timeout)
        r.raise_for_status()
        return r.json()

    def _post(self, path: str, body: Optional[dict] = None, params: Optional[dict] = None) -> dict:
        r = self._client.post(f"{self._base}{path}", json=body, params=params, timeout=self._timeout)
        r.raise_for_status()
        return r.json()

    def health(self) -> bool:
        """/v1/health 免鉴权，只能说明服务在，不能说明 token 对。"""
        try:
            return self._client.get(f"{self._base}/v1/health", timeout=self._timeout).status_code == 200
        except httpx.HTTPError:
            return False

    def status(self) -> dict:
        return self._get("/v1/status")

    def summary(self) -> dict:
        return self._get("/v1/desktop/summary")

    # ── S2：快速面板用到的接口 ─────────────────────────────────────────────
    # 所有接口都支持 session_id 参数：带上后 daemon 会路由到该 session 专属的
    # Agent（见 routes.py::_bridge），快速面板借此与主会话互不排队。

    def chat(self, message: str, session_id: str) -> str:
        """提交一条消息，返回 turn_id。"""
        return str(self._post("/v1/chat", {"message": message, "session_id": session_id})["turn_id"])

    def interrupt(self, session_id: str) -> None:
        self._post("/v1/interrupt", params={"session_id": session_id})

    def turns(self, session_id: Optional[str] = None) -> list[dict]:
        params = {"session_id": session_id} if session_id else None
        return list(self._get("/v1/turns", params=params).get("turns", []))

    def turn(self, turn_id: str, session_id: Optional[str] = None) -> dict:
        params = {"session_id": session_id} if session_id else None
        return self._get(f"/v1/turns/{turn_id}", params=params)

    def history(self, session_id: Optional[str] = None, limit: int = 20) -> list[dict]:
        params: dict = {"limit": limit}
        if session_id:
            params["session_id"] = session_id
        return list(self._get("/v1/history", params=params).get("messages", []))

    # ── S3：审批 / 交互回答 / cron 提问 ───────────────────────────────────

    def permissions_pending(self) -> list[dict]:
        return list(self._get("/v1/permissions/pending").get("permissions", []))

    def respond_permission(self, req_id: str, approve: bool, mode: str = "once") -> None:
        self._post(f"/v1/permissions/{req_id}", {"approve": approve, "mode": mode})

    def interactions_pending(self) -> list[dict]:
        return list(self._get("/v1/interactions/pending").get("interactions", []))

    def respond_interaction(self, req_id: str, body: dict) -> None:
        self._post(f"/v1/interactions/{req_id}", body)

    def cron_questions_pending(self, limit: int = 20) -> list[dict]:
        return list(self._get("/v1/cron_questions/pending", params={"limit": limit}).get("questions", []))

    def answer_cron_question(self, question_id: str, answer: str) -> None:
        self._post(f"/v1/cron_questions/{question_id}/answer", {"answer": answer})

    def stream_events(self, stop: threading.Event) -> Iterator[SseEvent]:
        """订阅 /v1/stream（不回放历史：调用方应先取快照再订阅）。
        连接断开/读超时会抛 httpx.HTTPError，由调用方决定重连。"""
        with self._client.stream("GET", f"{self._base}/v1/stream",
                                 params={"replay": "false"}) as resp:
            resp.raise_for_status()
            for ev in parse_sse(resp.iter_lines()):
                if stop.is_set():
                    return
                yield ev
