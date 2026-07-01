"""
mini_agent_client.py
=====================
mini_agent `/v1/*` HTTP API 的轻量异步客户端封装。

设计目标：
- 不引入重依赖：优先使用 httpx（若已安装），否则退化到标准库 urllib
  （通过 asyncio.run_in_executor 跑同步请求），与 weixin/api.py 的
  策略保持一致。
- 只封装 weixin 插件需要用到的端点，不追求覆盖 mini_agent 全部 API。
- 每个方法都可以传入独立的 token，方便"每个微信用户用各自 token 调用"
  这种多用户场景；也可以在构造 Client 时传入默认 token（用于 owner 操作，
  比如创建新用户）。

用法::

    client = MiniAgentClient(base_url="http://localhost:8080")

    # owner 操作（建用户）
    user_id, token = await client.create_user(
        owner_token=OWNER_TOKEN, name="wx_xxx", role="user",
    )

    # 该用户自己的操作
    turn_id, session_id = await client.chat(token=token, message="你好")
    result = await client.wait_turn_result(token=token, turn_id=turn_id)
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Optional

logger = logging.getLogger(__name__)

try:
    import httpx  # type: ignore
    _HAS_HTTPX = True
except ImportError:  # pragma: no cover
    httpx = None  # type: ignore
    _HAS_HTTPX = False


class MiniAgentAPIError(Exception):
    """mini_agent API 返回非 2xx 或请求失败时抛出。"""

    def __init__(self, message: str, status: Optional[int] = None, body: Optional[str] = None):
        super().__init__(message)
        self.status = status
        self.body = body


@dataclass
class TurnResult:
    turn_id: str
    state: str  # "running" | "done" | "error" | "interrupted"
    text: str = ""
    timed_out: bool = False


class MiniAgentClient:
    """mini_agent `/v1/*` API 的轻量客户端。"""

    def __init__(
        self,
        base_url: str,
        default_token: Optional[str] = None,
        timeout_s: float = 30.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.default_token = default_token
        self.timeout_s = timeout_s

    # ------------------------------------------------------------------
    # 底层请求
    # ------------------------------------------------------------------

    def _url(self, path: str) -> str:
        if not path.startswith("/"):
            path = "/" + path
        return f"{self.base_url}/v1{path}"

    async def _request(
        self,
        method: str,
        path: str,
        token: Optional[str] = None,
        params: Optional[dict[str, Any]] = None,
        json_body: Optional[dict[str, Any]] = None,
    ) -> Any:
        url = self._url(path)
        tok = token or self.default_token
        headers = {"Content-Type": "application/json"}
        if tok:
            headers["Authorization"] = f"Bearer {tok}"

        if _HAS_HTTPX:
            return await self._request_httpx(method, url, headers, params, json_body)
        return await self._request_urllib(method, url, headers, params, json_body)

    async def _request_httpx(self, method, url, headers, params, json_body):
        async with httpx.AsyncClient(timeout=self.timeout_s) as client:  # type: ignore
            resp = await client.request(method, url, headers=headers, params=params, json=json_body)
        return self._handle_response(resp.status_code, resp.text)

    async def _request_urllib(self, method, url, headers, params, json_body):
        if params:
            from urllib.parse import urlencode

            url = f"{url}?{urlencode({k: v for k, v in params.items() if v is not None})}"

        data = json.dumps(json_body).encode() if json_body is not None else None
        req = urllib.request.Request(url, data=data, headers=headers, method=method)

        loop = asyncio.get_running_loop()

        def _do() -> tuple[int, str]:
            try:
                with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                    return resp.status, resp.read().decode()
            except urllib.error.HTTPError as e:
                return e.code, e.read().decode() if e.fp else ""

        status, body = await loop.run_in_executor(None, _do)
        return self._handle_response(status, body)

    @staticmethod
    def _handle_response(status: int, body: str) -> Any:
        try:
            parsed = json.loads(body) if body else None
        except json.JSONDecodeError:
            parsed = None
        if status >= 400:
            detail = parsed.get("detail") if isinstance(parsed, dict) else body
            raise MiniAgentAPIError(f"mini_agent API {status}: {detail}", status=status, body=body)
        return parsed

    # ------------------------------------------------------------------
    # 用户管理（owner 专用）
    # ------------------------------------------------------------------

    async def create_user(
        self,
        owner_token: str,
        name: str,
        role: str = "user",
        trust_level: Optional[str] = None,
        meta: Optional[dict] = None,
    ) -> tuple[str, str]:
        """创建一个 mini_agent 用户，返回 (user_id, token)。"""
        body: dict[str, Any] = {"name": name, "role": role}
        if trust_level is not None:
            body["trust_level"] = trust_level
        if meta is not None:
            body["meta"] = meta
        resp = await self._request("POST", "/users", token=owner_token, json_body=body)
        if not resp or not resp.get("ok"):
            raise MiniAgentAPIError(f"create_user failed: {resp}")
        return resp["user_id"], resp["token"]

    # ------------------------------------------------------------------
    # 对话
    # ------------------------------------------------------------------

    async def chat(
        self, token: str, message: str, session_id: Optional[str] = None
    ) -> tuple[str, Optional[str]]:
        """发送一条消息，返回 (turn_id, session_id)。"""
        body: dict[str, Any] = {"message": message}
        if session_id:
            body["session_id"] = session_id
        resp = await self._request("POST", "/chat", token=token, json_body=body)
        return resp["turn_id"], resp.get("session_id")

    async def get_turn(self, token: str, turn_id: str) -> dict:
        return await self._request("GET", f"/turns/{turn_id}", token=token)

    async def get_history(self, token: str) -> list[dict]:
        resp = await self._request("GET", "/history", token=token)
        return resp.get("messages", []) if resp else []

    async def get_status(self, token: str) -> dict:
        return await self._request("GET", "/status", token=token)

    async def interrupt(self, token: str) -> bool:
        resp = await self._request("POST", "/interrupt", token=token)
        return bool(resp and resp.get("ok"))

    async def wait_turn_result(
        self,
        token: str,
        turn_id: str,
        poll_interval_s: float = 1.5,
        timeout_s: float = 180.0,
    ) -> TurnResult:
        """
        轮询直到某个 turn 结束，返回最终回复文本。

        取结果的策略：
          1. 轮询 GET /v1/turns/{turn_id}，直到 state 不再是 "running"。
          2. state == "done" 时，读 GET /v1/history，取末尾的
             assistant 消息拼成文本（mini_agent 目前没有直接在
             TurnInfo 里带最终文本的字段，历史记录里的最后一条
             assistant 消息即为本轮回复）。
          3. state 是 "error" / "interrupted" 时，直接返回对应状态，
             文本留空，由调用方决定提示语。
        """
        deadline = time.monotonic() + timeout_s
        state = "running"
        while time.monotonic() < deadline:
            info = await self.get_turn(token, turn_id)
            state = info.get("state", "running")
            if state != "running":
                break
            await asyncio.sleep(poll_interval_s)
        else:
            return TurnResult(turn_id=turn_id, state="running", timed_out=True)

        text = ""
        if state == "done":
            text = await self._extract_last_assistant_text(token)
        return TurnResult(turn_id=turn_id, state=state, text=text)

    async def _extract_last_assistant_text(self, token: str) -> str:
        messages = await self.get_history(token)
        for m in reversed(messages):
            role = m.get("role")
            if role == "assistant":
                content = m.get("content", "")
                if isinstance(content, list):
                    # 兼容 content 为 block 列表的情况，只拼文本块
                    parts = [
                        b.get("text", "") for b in content
                        if isinstance(b, dict) and b.get("type") == "text"
                    ]
                    return "".join(parts).strip()
                return str(content).strip()
        return ""

    # ------------------------------------------------------------------
    # Session 管理
    # ------------------------------------------------------------------

    async def list_sessions(self, token: str, limit: int = 50) -> dict:
        return await self._request("GET", "/sessions", token=token, params={"limit": limit})

    async def new_session(self, token: str) -> dict:
        return await self._request("POST", "/sessions/new", token=token)

    async def resume_session(self, token: str, session_id: str) -> dict:
        return await self._request("POST", f"/sessions/{session_id}/resume", token=token)

    async def delete_session(self, token: str, session_id: str) -> dict:
        return await self._request("DELETE", f"/sessions/{session_id}", token=token)

    # ------------------------------------------------------------------
    # 文件系统（只读）
    # ------------------------------------------------------------------

    async def fs_list(self, token: str, path: str = "") -> dict:
        return await self._request("GET", "/fs/list", token=token, params={"path": path} if path else None)

    async def fs_read(self, token: str, path: str) -> dict:
        return await self._request("GET", "/fs/read", token=token, params={"path": path})

    async def fs_search(self, token: str, query: str) -> dict:
        return await self._request("GET", "/fs/search", token=token, params={"q": query})

    # ------------------------------------------------------------------
    # 权限审批
    # ------------------------------------------------------------------

    async def list_pending_permissions(self, token: str) -> list[dict]:
        resp = await self._request("GET", "/permissions/pending", token=token)
        return resp.get("permissions", []) if resp else []

    async def respond_permission(
        self, token: str, req_id: str, approve: bool, mode: str = "once", edited_input: Optional[dict] = None
    ) -> bool:
        body: dict[str, Any] = {"approve": approve, "mode": mode}
        if edited_input is not None:
            body["edited_input"] = edited_input
        resp = await self._request("POST", f"/permissions/{req_id}", token=token, json_body=body)
        return bool(resp and resp.get("ok"))
