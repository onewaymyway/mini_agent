"""
browser-core/impl/cdp_client.py — 极简 Chrome DevTools Protocol 客户端。

阶段十四新增。这是 browser-core 契约（见 ../SKILL.md）的第一份真实实现的
最底层依赖，风格与 `.claude/skills/browser-cdp/src/core/cdp_client.py`
一致（同步阻塞、不依赖 Playwright/Selenium，只用 HTTP /json/* 做 tab 发现、
WebSocket 发 CDP 命令），但做了大幅精简：只保留 browser-core 7 个原语真正
用得到的那部分能力（tab 发现/连接、Page.navigate、Runtime.evaluate、
Page.captureScreenshot），去掉了 browser-cdp 里那些面向"几十个网站定制抓取"
的重试分类/事件订阅/cookie管理等重型基础设施。

刻意不直接 `import` browser-cdp 的模块：generative-capability 的 member/
explorer 脚本运行时是按文件路径动态 `importlib` 加载的独立文件（见
`capability_engine.py::execute()` 头部注释），不是这个仓库的一部分（可以被
单独复制到别的项目里 `.claude/skills/browser-core/` 目录使用），因此不应该
依赖同一仓库内另一个 skill 目录的内部实现细节——那样会让 browser-core 变得
没法脱离 browser-cdp 独立存在，违反两者本应是"通用原语 vs 网站定制脚本"的
清晰边界（见 SKILL.md"为什么现在只做契约"一节的分析）。

连接的可以是：
- 本模块自己用 `browser_launch.py` 拉起的一个新 Chrome 进程（headless 或
  headed 均可，取决于调用方传入的 mode）；
- 用户自己手动启动、且带 `--remote-debugging-port` 的**任意已在运行的
  浏览器**（包括用户桌面上正常使用、已登录过账号的普通 Chrome）——这正是
  本次改动要支持的"有时候登录需要用户进行操作"场景：用户先手动登录好，
  再把这个已登录的浏览器实例交给 browser-core "attach"，不需要 browser-core
  自己去处理任何登录/验证码逻辑。
"""
from __future__ import annotations

import itertools
import json
import threading
import time
from typing import Any, Optional

try:
    import requests
except ImportError as e:  # pragma: no cover - 环境缺依赖时给出明确指引
    raise ImportError(
        "browser-core 需要 `requests` 库（项目 requirements.txt 已包含）。"
    ) from e

try:
    import websocket  # websocket-client
except ImportError as e:  # pragma: no cover
    raise ImportError(
        "browser-core 需要 `websocket-client` 库，请先 `pip install "
        "websocket-client`（browser-cdp skill 的 pyproject.toml 里已声明同一"
        "依赖，browser-core 作为独立静态 skill 需要单独安装一次，这是刻意的"
        "——两个 skill 不共享 Python 依赖环境假设）。"
    ) from e


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 9222


class CDPError(RuntimeError):
    pass


def _http_json(host: str, port: int, path: str, timeout: float = 5.0) -> Any:
    resp = requests.get(f"http://{host}:{port}{path}", timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def is_debug_port_alive(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT, timeout: float = 1.0) -> bool:
    try:
        _http_json(host, port, "/json/version", timeout=timeout)
        return True
    except Exception:
        return False


def list_tabs(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> list[dict]:
    targets = _http_json(host, port, "/json/list")
    return [t for t in targets if t.get("type") == "page"]


def new_tab(url: str = "about:blank", host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> dict:
    import urllib.parse

    resp = requests.post(
        f"http://{host}:{port}/json/new",
        data=urllib.parse.quote(url),
        timeout=5.0,
    )
    resp.raise_for_status()
    return resp.json()


class CDPSession:
    """对单个 tab（page target）建立的 WebSocket 会话，同步阻塞风格。"""

    def __init__(self, ws_url: str, host: str, port: int, timeout: float = 15.0):
        self.timeout = timeout
        self._id_counter = itertools.count(1)
        self._lock = threading.Lock()
        self._pending: dict[int, dict] = {}
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

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def send(self, method: str, params: Optional[dict] = None, timeout: Optional[float] = None) -> dict:
        """发送一条 CDP 命令并阻塞等待其对应的 result。"""
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
            # 忽略其余事件（Network.*/Page.loadEventFired 等），本模块不需要订阅它们
        raise CDPError(f"等待 CDP 响应超时 (method={method}, timeout={timeout or self.timeout}s)")

    def eval_js(self, expression: str, await_promise: bool = False, timeout: Optional[float] = None) -> Any:
        result = self.send(
            "Runtime.evaluate",
            {"expression": expression, "returnByValue": True, "awaitPromise": await_promise},
            timeout=timeout,
        )
        if result.get("exceptionDetails"):
            raise CDPError(f"JS 执行异常: {result['exceptionDetails']}")
        return result.get("result", {}).get("value")

    def navigate(self, url: str, timeout: float = 20.0) -> dict:
        self.send("Page.enable", {})
        result = self.send("Page.navigate", {"url": url}, timeout=timeout)
        if result.get("errorText"):
            raise CDPError(result["errorText"])
        # 简单轮询 document.readyState，避免依赖事件订阅的时序问题
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                state = self.eval_js("document.readyState")
            except CDPError:
                state = None
            if state in ("interactive", "complete"):
                break
            time.sleep(0.2)
        return result

    def capture_screenshot(self, full_page: bool = False) -> str:
        params: dict = {"format": "png"}
        if full_page:
            params["captureBeyondViewport"] = True
        result = self.send("Page.captureScreenshot", params, timeout=20.0)
        data = result.get("data")
        if not data:
            raise CDPError("Page.captureScreenshot 未返回图像数据")
        return data  # base64


def connect_tab(target: dict, host: str, port: int, timeout: float = 15.0) -> CDPSession:
    ws_url = target.get("webSocketDebuggerUrl")
    if not ws_url:
        raise CDPError(f"target 没有 webSocketDebuggerUrl: {target}")
    return CDPSession(ws_url, host=host, port=port, timeout=timeout)
