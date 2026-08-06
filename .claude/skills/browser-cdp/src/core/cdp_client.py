"""
cdp_client.py - Chrome DevTools Protocol 轻量客户端

不依赖 Playwright/Selenium，直接通过 HTTP(/json/*) 做 tab 发现，
通过 WebSocket 发送 CDP 命令 / 接收事件。

设计目标：
- 连接的是"真实浏览器"（用户手动打开、或本脚本用 --remote-debugging-port 拉起的），
  可以是用户桌面上的 Chrome，也可以是无 GUI 服务器上 --headless=new 跑的 Chrome。
- 同步阻塞风格，便于在简单 CLI 脚本里调用，不引入 asyncio 心智负担。
"""
from __future__ import annotations

import json
import time
import itertools
import threading
from typing import Any, Optional

import requests
import websocket  # websocket-client


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 9222


class CDPError(RuntimeError):
    pass


def http_json(host: str, port: int, path: str, timeout: float = 5.0) -> Any:
    url = f"http://{host}:{port}{path}"
    resp = requests.get(url, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def list_tabs(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> list[dict]:
    """列出所有可连接的 target（tab/page），只保留 type=page 的。"""
    targets = http_json(host, port, "/json/list")
    return [t for t in targets if t.get("type") == "page"]


def version_info(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> dict:
    return http_json(host, port, "/json/version")


def new_tab(url: str = "about:blank", host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> dict:
    import urllib.parse
    url_encoded = urllib.parse.quote(url)
    resp = requests.post(f"http://{host}:{port}/json/new", data=url_encoded, timeout=5.0)
    resp.raise_for_status()
    return resp.json()


def close_tab(target_id: str, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> None:
    http_json(host, port, f"/json/close/{target_id}")


def activate_tab(target_id: str, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> None:
    http_json(host, port, f"/json/activate/{target_id}")


def is_debug_port_alive(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT, timeout: float = 1.0) -> bool:
    try:
        version_info(host, port)
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

    # -- event subscription --------------------------------------------
    def subscribe(self, method: str, callback):
        """注册事件回调"""
        if not hasattr(self, '_subscribers'):
            self._subscribers: dict[str, list] = {}
        if method not in self._subscribers:
            self._subscribers[method] = []
        self._subscribers[method].append(callback)

    def unsubscribe(self, method: str, callback):
        """移除事件回调"""
        if hasattr(self, '_subscribers') and method in self._subscribers:
            if callback in self._subscribers[method]:
                self._subscribers[method].remove(callback)

    def _fire_event(self, method: str, params: dict):
        """触发事件回调"""
        if hasattr(self, '_subscribers') and method in self._subscribers:
            for cb in self._subscribers[method]:
                try:
                    cb(params)
                except Exception as e:
                    logger.warning(f"事件回调执行失败: {e}")

    # -- low level -----------------------------------------------------
    def _next_id(self) -> int:
        return next(self._id_counter)

    def send(self, method: str, params: Optional[dict] = None, timeout: Optional[float] = None) -> dict:
        """发送命令并阻塞等待其对应的 result。"""
        msg_id = self._next_id()
        payload = {"id": msg_id, "method": method, "params": params or {}}
        with self._lock:
            self._ws.send(json.dumps(payload))
        return self._wait_for_id(msg_id, timeout or self.timeout)

    def _wait_for_id(self, msg_id: int, timeout: float) -> dict:
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
                        raise CDPError(f"{method_hint(data)}: {data['error']}")
                    return data.get("result", {})
                else:
                    self._pending[data["id"]] = data.get("result", {})
            else:
                self._events.append(data)
                self._fire_event(data.get("method", ""), data.get("params", {}))
        raise CDPError(f"等待 CDP 响应超时 (id={msg_id}, timeout={timeout}s)")

    def wait_event(self, method: str, timeout: float = 10.0, match: Optional[dict] = None) -> dict:
        """阻塞等待某个事件 (如 Page.loadEventFired)。match 用于按 params 字段过滤。"""
        deadline = time.time() + timeout
        # 先看已缓存的事件
        for ev in list(self._events):
            if ev.get("method") == method and _match_params(ev.get("params", {}), match):
                self._events.remove(ev)
                return ev
        while time.time() < deadline:
            try:
                self._ws.settimeout(max(0.1, deadline - time.time()))
                raw = self._ws.recv()
            except websocket.WebSocketTimeoutException:
                continue
            if not raw:
                continue
            data = json.loads(raw)
            if "id" in data:
                self._pending[data["id"]] = data.get("result", {})
                continue
            if data.get("method") == method and _match_params(data.get("params", {}), match):
                return data
            self._events.append(data)
        raise CDPError(f"等待事件超时: {method}")

    def drain_events(self, method_prefix: Optional[str] = None, duration: float = 1.0) -> list[dict]:
        """在 duration 秒内非阻塞式收集事件（用于抓 console/network 日志）。"""
        collected = list(self._events)
        self._events.clear()
        deadline = time.time() + duration
        self._ws.settimeout(0.2)
        while time.time() < deadline:
            try:
                raw = self._ws.recv()
            except websocket.WebSocketTimeoutException:
                continue
            except Exception:
                break
            if not raw:
                continue
            data = json.loads(raw)
            if "id" in data:
                self._pending[data["id"]] = data.get("result", {})
                continue
            collected.append(data)
        if method_prefix:
            collected = [e for e in collected if e.get("method", "").startswith(method_prefix)]
        return collected

    # -- convenience -----------------------------------------------------
    def get_all_cookies(self, timeout: Optional[float] = None) -> list:
        """获取当前页面的所有 cookies。"""
        result = self.send("Network.getAllCookies", {}, timeout=timeout)
        return result.get("cookies", [])

    def set_cookie(self, name: str, value: str, domain: str = "", path: str = "/", 
                   secure: bool = True, http_only: bool = False, same_site: str = "Lax",
                   expires: float = -1, timeout: Optional[float] = None) -> dict:
        """设置 cookie。"""
        params = {
            "name": name,
            "value": value,
            "domain": domain,
            "path": path,
            "secure": secure,
            "httpOnly": http_only,
            "sameSite": same_site,
        }
        if expires > 0:
            params["expires"] = expires
        return self.send("Network.setCookie", params, timeout=timeout)

    def delete_cookie(self, name: str, domain: str = "", path: str = "/", 
                      timeout: Optional[float] = None) -> dict:
        """删除 cookie。"""
        return self.send("Network.deleteCookies", {
            "name": name,
            "domain": domain,
            "path": path,
        }, timeout=timeout)

    def clear_all_cookies(self, timeout: Optional[float] = None) -> dict:
        """清除所有 cookies。"""
        return self.send("Network.clearBrowserCookies", {}, timeout=timeout)

    def eval_js(self, expression: str, await_promise: bool = False, timeout: Optional[float] = None) -> Any:
        result = self.send(
            "Runtime.evaluate",
            {
                "expression": expression,
                "returnByValue": True,
                "awaitPromise": await_promise,
            },
            timeout=timeout,
        )
        if result.get("exceptionDetails"):
            raise CDPError(f"JS 执行异常: {result['exceptionDetails']}")
        return result.get("result", {}).get("value")

    def set_extra_http_headers(self, headers: dict) -> dict:
        """设置额外的 HTTP 请求头"""
        return self.send("Network.setExtraHTTPHeaders", {
            "headers": headers
        })

    def query_selector_all(self, selector: str) -> list:
        """通过 JS 查询所有匹配元素"""
        return self.eval_js(f"""
            () => {{
                const elements = document.querySelectorAll({selector!r});
                return Array.from(elements).map(el => {{
                    const rect = el.getBoundingClientRect();
                    return {{
                        tagName: el.tagName,
                        className: el.className,
                        id: el.id,
                        text: el.innerText || el.textContent || '',
                        href: el.href || null,
                        rect: rect ? {{x: rect.x, y: rect.y, width: rect.width, height: rect.height}} : null,
                        attributes: Array.from(el.attributes).reduce((acc, attr) => {{
                            acc[attr.name] = attr.value;
                            return acc;
                        }}, {{}})
                    }};
                }});
            }}
        """)

    def query_selector(self, selector: str):
        """通过 JS 查询第一个匹配元素"""
        elements = self.query_selector_all(selector)
        return elements[0] if elements else None

    async def eval_js_async(self, expression: str, await_promise: bool = False, timeout: Optional[float] = None) -> Any:
        """异步版本的 eval_js（兼容异步调用）"""
        return self.eval_js(expression, await_promise, timeout)


def _match_params(params: dict, match: Optional[dict]) -> bool:
    if not match:
        return True
    return all(params.get(k) == v for k, v in match.items())


def method_hint(data: dict) -> str:
    return data.get("method") or "cdp"


def connect_tab(target: dict, timeout: float = 15.0, host: str = "127.0.0.1", port: int = 9222) -> CDPSession:
    ws_url = target.get("webSocketDebuggerUrl")
    if not ws_url:
        raise CDPError(f"target 没有 webSocketDebuggerUrl: {target}")
    # Chrome DevTools Protocol requires Origin header for WebSocket connections
    origin = f"http://{host}:{port}"
    return CDPSession(ws_url, timeout=timeout, origin=origin)


def find_tab(
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    tab_id: Optional[str] = None,
    url_contains: Optional[str] = None,
    title_contains: Optional[str] = None,
) -> dict:
    """按 id / url关键字 / 标题关键字 找到一个 tab；都不给则返回第一个。"""
    tabs = list_tabs(host, port)
    if not tabs:
        raise CDPError("没有找到任何可连接的 tab，请确认浏览器已用调试端口启动")
    if tab_id:
        for t in tabs:
            if t.get("id") == tab_id:
                return t
        raise CDPError(f"未找到 tab id={tab_id}")
    if url_contains:
        for t in tabs:
            if url_contains in (t.get("url") or ""):
                return t
    if title_contains:
        for t in tabs:
            if title_contains in (t.get("title") or ""):
                return t
    if url_contains or title_contains:
        raise CDPError("未找到匹配的 tab")
    return tabs[0]
