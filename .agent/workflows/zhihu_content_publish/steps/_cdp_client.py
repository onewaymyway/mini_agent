"""
steps/_cdp_client.py — 本 workflow 私有的极简 Chrome DevTools Protocol 客户端。

[browser-cdp 依赖清理] 此前 `05_enrich_questions.py`/`02_search_zhihu.py` 是靠
sys.path 注入 `.claude/skills/browser-cdp` 目录、再 import 它的
`src/core/cdp_client.py` 来复用 CDP 收发逻辑的——这让本 workflow 在
`browser-cdp` 这个 skill 被移除/迁移时会直接跑不起来。实际用到的 CDP 能力
只有"列 tab / 连 tab / 发命令 / 执行一段 JS / 等一个事件"这几个，体量很小，
没有必要为了这几个函数继续依赖一整个 skill 目录，于是原样搬进本文件，成为
workflow 自己的一部分（不是 step，文件名前缀 `_` 标记"内部辅助模块"）。

依赖：`requests`、`websocket-client`（`pip install requests websocket-client`，
与原来 browser-cdp 版本要求一致，不是新增依赖）。

本文件只保留本 workflow 实际用到的能力：
  - `list_tabs()` / `connect_tab()`：tab 发现与建立会话
  - `CDPSession.send()` / `.eval_js()` / `.wait_event()`：发命令、跑 JS、
    等事件（`_goto_and_extract()` 用 `wait_event("Page.loadEventFired")`
    等待知乎问题详情页导航完成，SPA 场景等不到时会退化成固定 sleep）
不追求覆盖 browser-cdp/browser-core 契约的全部原语——那是给通用探索场景用的，
本文件只服务这一个 workflow 的两个明确已知用法。
"""
from __future__ import annotations

import itertools
import json
import threading
import time
from typing import Any, Optional

try:
    import requests
except ImportError as e:  # pragma: no cover
    raise ImportError(
        "本 workflow 需要 `requests` 库：pip install requests"
    ) from e

try:
    import websocket  # websocket-client
except ImportError as e:  # pragma: no cover
    raise ImportError(
        "本 workflow 需要 `websocket-client` 库：pip install websocket-client"
    ) from e


DEFAULT_HOST = "127.0.0.1"


class CDPError(RuntimeError):
    pass


class CDPPortNotListeningError(CDPError):
    """CDP 调试端口没有任何进程在监听（连接被拒绝）。

    只描述"通用 CDP 客户端"能观察到的事实——端口连不上，不对"应该是谁在
    监听、该怎么启动它"做任何假设。这两件事是调用方（具体某个 workflow
    的某个 step）才知道的场景信息，调用方捕获本异常后应自行补充
    `remediation`（人类可读的下一步建议）和更具体的 `error_code` 前缀，
    不要在本文件里硬编码任何具体网站/业务的名字。
    """

    def __init__(self, host: str, port: int):
        super().__init__(f"CDP 端口 {host}:{port} 未监听（连接被拒绝）")
        self.error_code = "CDP_PORT_NOT_LISTENING"
        self.host = host
        self.port = port
        self.remediation: Optional[str] = None


class CDPNoTabsError(CDPError):
    """CDP 端口已监听，但没有任何 `type == "page"` 的 target（tab）。"""

    def __init__(self, host: str, port: int):
        super().__init__(f"CDP 端口 {host}:{port} 已监听，但没有找到任何 tab")
        self.error_code = "CDP_NO_TABS"
        self.host = host
        self.port = port
        self.remediation: Optional[str] = None


def list_tabs(host: str = DEFAULT_HOST, port: int = 9336) -> list[dict]:
    try:
        resp = requests.get(f"http://{host}:{port}/json/list", timeout=5.0)
    except requests.exceptions.ConnectionError as e:
        # 端口连不上是最常见、也最容易被上层 traceback 淹没的一类失败——
        # 原始的 requests.exceptions.ConnectionError/WinError 10061 这类
        # 底层网络异常对读者（人类或 agent）没有直接的行动指引，这里转成
        # 一个带 error_code 的专用异常，方便上层按 error_code 做分类/给出
        # remediation，而不必解析 traceback 文本。
        raise CDPPortNotListeningError(host, port) from e
    resp.raise_for_status()
    targets = resp.json()
    return [t for t in targets if t.get("type") == "page"]


class CDPSession:
    """对单个 tab（page target）建立的 WebSocket 会话，同步阻塞风格。"""

    def __init__(self, ws_url: str, host: str, port: int, timeout: float = 15.0):
        self.timeout = timeout
        self._id_counter = itertools.count(1)
        self._lock = threading.Lock()
        self._pending: dict[int, dict] = {}
        self._events: list[dict] = []
        self._ws = websocket.create_connection(
            ws_url, timeout=timeout, origin=f"http://{host}:{port}"
        )
        self._closed = False

    def close(self) -> None:
        if not self._closed:
            try:
                self._ws.close()
            except Exception:
                pass
            self._closed = True

    def send(self, method: str, params: Optional[dict] = None, timeout: Optional[float] = None) -> dict:
        """发送一条 CDP 命令并阻塞等待其对应的 result（忽略途中收到的事件，
        缓存进 `_events` 供 `wait_event()` 使用）。"""
        msg_id = next(self._id_counter)
        payload = {"id": msg_id, "method": method, "params": params or {}}
        with self._lock:
            self._ws.send(json.dumps(payload))
        deadline = time.time() + (timeout or self.timeout)
        while time.time() < deadline:
            if msg_id in self._pending:
                return self._pending.pop(msg_id)
            try:
                self._ws.settimeout(max(0.1, deadline - time.time()))
                raw = self._ws.recv()
            except websocket.WebSocketTimeoutException:
                continue
            if not raw:
                continue
            data = json.loads(raw)
            if "id" in data:
                if data["id"] == msg_id:
                    if "error" in data:
                        raise CDPError(f"{method}: {data['error']}")
                    return data.get("result", {})
                self._pending[data["id"]] = data.get("result", {})
            elif "method" in data:
                self._events.append(data)
        raise CDPError(f"等待 CDP 响应超时 (method={method}, timeout={timeout or self.timeout}s)")

    def wait_event(self, method: str, timeout: float = 15.0) -> dict:
        """等待某个 CDP 事件（如 `Page.loadEventFired`）。先看之前 `send()`
        途中缓存下来的事件，没有再继续从 WebSocket 读。"""
        for i, evt in enumerate(self._events):
            if evt.get("method") == method:
                return self._events.pop(i)
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                self._ws.settimeout(max(0.1, deadline - time.time()))
                raw = self._ws.recv()
            except websocket.WebSocketTimeoutException:
                continue
            if not raw:
                continue
            data = json.loads(raw)
            if data.get("method") == method:
                return data
            if "id" in data:
                self._pending[data["id"]] = data.get("result", {})
            elif "method" in data:
                self._events.append(data)
        raise CDPError(f"等待事件超时 (method={method}, timeout={timeout}s)")

    def eval_js(self, expression: str, await_promise: bool = True, timeout: Optional[float] = None) -> Any:
        result = self.send(
            "Runtime.evaluate",
            {"expression": expression, "returnByValue": True, "awaitPromise": await_promise},
            timeout=timeout,
        )
        if result.get("exceptionDetails"):
            raise CDPError(f"JS 执行异常: {result['exceptionDetails']}")
        return result.get("result", {}).get("value")


def connect_tab(target: dict, host: str = DEFAULT_HOST, port: int = 9336, timeout: float = 15.0) -> CDPSession:
    ws_url = target.get("webSocketDebuggerUrl")
    if not ws_url:
        raise CDPError(f"target 没有 webSocketDebuggerUrl: {target}")
    return CDPSession(ws_url, host=host, port=port, timeout=timeout)
