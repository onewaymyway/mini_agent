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
    # 绕过系统代理：本地 CDP 端口不应经过代理
    proxies = {"http": None, "https": None}
    resp = requests.get(url, timeout=timeout, proxies=proxies)
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
        if "result" in result:
            res = result["result"]
            # 简单类型（字符串、数字等）直接返回 value
            if "value" in res:
                return res["value"]
            # 复杂对象需要通过 objectId 获取属性
            if "objectId" in res:
                return self._get_object_properties(res["objectId"])
        if "exceptionDetails" in result:
            exc = result["exceptionDetails"]
            raise CDPError(f"JS 执行失败: {exc.get('text', '')} {exc.get('exception', {}).get('description', '')}")
        return result.get("result", {})

    def _unwrap_value(self, prop_value: dict) -> Any:
        """递归解包单个属性值。"""
        if "value" in prop_value:
            return prop_value["value"]
        if "objectId" in prop_value:
            obj_id = prop_value["objectId"]
            cls = prop_value.get("className", "")
            if cls == "Array":
                # 数组：递归获取每个元素
                result = self.send("Runtime.getProperties",
                                 {"objectId": obj_id, "ownProperties": True},
                                 timeout=self.timeout)
                props = result.get("result", [])
                arr = []
                for p in props:
                    if p.get("name", "").isdigit():
                        arr.append(self._unwrap_value(p.get("value", {})))
                return arr
            # 普通对象：获取所有属性
            result = self.send("Runtime.getProperties",
                             {"objectId": obj_id, "ownProperties": True},
                             timeout=self.timeout)
            obj = {}
            for p in result.get("result", []):
                name = p.get("name", "")
                if not name.startswith("__") and name != "constructor":
                    obj[name] = self._unwrap_value(p.get("value", {}))
            return obj
        return None

    def _get_object_properties(self, object_id: str, depth: int = 0, max_depth: int = 10) -> Any:
        """递归获取对象的所有属性（用于处理 CDP 返回的 objectId 引用）。"""
        if depth > max_depth:
            return f"<{object_id}> (max depth reached)"
        try:
            result = self.send("Runtime.getProperties",
                             {"objectId": object_id, "ownProperties": True},
                             timeout=self.timeout)
            props = result.get("result", [])
            obj = {}
            for prop in props:
                name = prop.get("name", "")
                if name.startswith("__") or name == "constructor":
                    continue
                obj[name] = self._unwrap_value(prop.get("value", {}))
            # 特殊处理数组
            if isinstance(obj, dict) and obj.get("length", 0) > 0:
                return [obj[str(i)] for i in range(obj["length"]) if str(i) in obj]
            return obj
        except Exception as e:
            logger.warning(f"获取对象属性失败 {object_id}: {e}")
            return f"<{object_id}> error: {e}"

    def get_page_content(self, selector: str = "body", timeout: float = 10.0) -> str:
        """获取页面元素内容。"""
        js = f"""
(function() {{
    const el = document.querySelector('{selector}');
    return el ? el.innerText : '';
}})()
"""
        return self.eval_js(js, await_promise=True, timeout=timeout) or ""
