"""
CDP 客户端 - 最小化实现

仅保留 stock_watch 需要的核心功能：
- 连接浏览器调试端口
- 导航到 URL
- 获取页面内容

不依赖 playwright/selenium，直接通过 HTTP/WebSocket 与 Chrome DevTools Protocol 通信。
"""
from __future__ import annotations

import json
import time
import itertools
import threading
from typing import Any, Optional

import requests
import websocket


class CDPError(RuntimeError):
    """CDP 相关错误。"""


def http_json(host: str, port: int, path: str, timeout: float = 5.0) -> Any:
    """发送 HTTP GET 请求并返回 JSON 响应。"""
    url = f"http://{host}:{port}{path}"
    resp = requests.get(url, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def list_tabs(host: str = "127.0.0.1", port: int = 9222) -> list[dict]:
    """列出所有可连接的 tab（page target）。"""
    targets = http_json(host, port, "/json/list")
    return [t for t in targets if t.get("type") == "page"]


def is_debug_port_alive(host: str = "127.0.0.1", port: int = 9222, timeout: float = 1.0) -> bool:
    """检查调试端口是否可用。"""
    try:
        http_json(host, port, "/json/version", timeout=timeout)
        return True
    except Exception:
        return False


class CDPSession:
    """对单个 tab（page target）建立的 WebSocket 会话。"""

    def __init__(self, ws_url: str, timeout: float = 15.0, origin: str = None):
        self.ws_url = ws_url
        self.timeout = timeout
        self._id_counter = itertools.count(1)
        self._lock = threading.Lock()
        self._pending: dict[int, dict] = {}
        self._events: list[dict] = []
        options = {}
        if origin:
            options["origin"] = origin
        self._ws = websocket.create_connection(ws_url, timeout=timeout, **options)
        self._closed = False
        self._recv_lock = threading.Lock()

    def close(self):
        """关闭 CDP 会话。"""
        if not self._closed:
            try:
                self._ws.close()
            except Exception:
                pass
            self._closed = True

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def _next_id(self) -> int:
        return next(self._id_counter)

    def send(self, method: str, params: Optional[dict] = None, timeout: Optional[float] = None) -> dict:
        """发送命令并阻塞等待响应。"""
        msg_id = self._next_id()
        payload = {"id": msg_id, "method": method, "params": params or {}}
        with self._lock:
            self._ws.send(json.dumps(payload))
        return self._wait_for_id(msg_id, timeout or self.timeout)

    def _wait_for_id(self, msg_id: int, timeout: float) -> dict:
        """等待特定 ID 的响应。"""
        deadline = time.time() + timeout
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
                        raise CDPError(f"CDP error: {data['error']}")
                    return data.get("result", {})
                else:
                    self._pending[data["id"]] = data.get("result", {})
            else:
                self._events.append(data)
        raise CDPError(f"等待 CDP 响应超时 (id={msg_id}, timeout={timeout}s)")

    def eval_js(self, expression: str, await_promise: bool = True, timeout: float = 10.0) -> Any:
        """在页面上下文中执行 JavaScript 并返回值。"""
        params = {"expression": expression}
        if await_promise:
            params["awaitPromise"] = True
        result = self.send("Runtime.evaluate", params, timeout=timeout)
        # 提取结果值
        if "result" in result and "value" in result["result"]:
            return result["result"]["value"]
        if "exceptionDetails" in result:
            exc = result["exceptionDetails"]
            raise CDPError(f"JS 执行失败: {exc.get('text', '')} {exc.get('exception', {}).get('description', '')}")
        return result.get("result", {})

    def get_page_content(self, selector: str = "body", timeout: float = 10.0) -> str:
        """获取页面元素内容。"""
        js = f"""
(function() {{
    const el = document.querySelector('{selector}');
    return el ? el.innerText : '';
}})()
"""
        return self.eval_js(js, await_promise=True, timeout=timeout) or ""
