"""
perception/behavior/collectors/cdp_browser.py — 通过 CDP 采集专用调试浏览器的页面访问事件

只使用 CDP 的 Target 域：
  Target.setDiscoverTargets            → 订阅所有 target 的创建/变化/销毁
  Target.targetCreated / targetInfoChanged / targetDestroyed

只关心 type == "page" 的 target，只取它的 url + title，不做：
  - Page.captureScreenshot（截图）
  - Network.*（请求/响应内容、cookie）
  - Runtime.evaluate（读取 DOM/页面文本）
  - 任何输入内容记录

事件语义与浏览器插件方案保持一致：source="cdp_browser"，event_type="page_visit"，
duration_sec 为该 url 在被替换/关闭前的停留时长；redact_url_path 开关同样生效。

依赖 `websockets` 库（extras: mini-agent[behavior-cdp]），未安装时 is_available()
返回 False，manager 会拒绝启动并给出安装提示，不会静默失败。
"""

from __future__ import annotations

import json
import threading
import time
from typing import Optional
from urllib.parse import urlparse

from ..events import ActivityEvent
from .base import BaseCollector
from .browser_launcher import DebugBrowserProcess, find_browser_executable, default_user_data_dir


def _redact(url: str, redact_path: bool) -> tuple[Optional[str], Optional[str]]:
    """返回 (domain, path)；redact_path=True 时 path 为 None。"""
    try:
        parsed = urlparse(url)
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.perception.behavior.collectors.cdp_browser._redact')
        return None, None
    domain = parsed.netloc or None
    path = None if redact_path else (parsed.path or "")
    return domain, path


class CDPBrowserCollector(BaseCollector):
    """事件驱动型采集器：不走 BaseCollector 的轮询循环，而是维持一条 CDP 长连接。"""

    name = "cdp_browser"

    def __init__(
        self,
        store,
        port: int = 9333,
        browser_path: str = "",
        user_data_dir: str = "",
        redact_url_path: bool = True,
        redact_title: bool = True,
        headless: bool = False,
    ) -> None:
        super().__init__(store, interval_sec=0)  # 不使用轮询间隔
        self._port = port
        self._browser_path = browser_path
        self._user_data_dir = user_data_dir
        self._redact_url_path = redact_url_path
        self._redact_title = redact_title
        self._headless = headless

        self._proc: Optional[DebugBrowserProcess] = None
        self._ws = None
        self._thread: Optional[threading.Thread] = None
        self._stop_flag = threading.Event()

        # targetId -> {"url": str, "title": str, "since": float}
        self._pages: dict[str, dict] = {}

    @staticmethod
    def is_available() -> bool:
        try:
            import websockets  # noqa: F401
            return True
        except ImportError:
            return False

    def launch_and_connect(self) -> None:
        """启动专用浏览器实例并建立 CDP 连接。供 `/behavior browser start` 调用。"""
        if not self.is_available():
            raise RuntimeError(
                "缺少 websockets 依赖，请先安装：pip install 'mini-agent[behavior-cdp]' "
                "或 pip install websockets"
            )

        executable = find_browser_executable(self._browser_path)
        if not executable:
            raise RuntimeError(
                "未找到可用的浏览器可执行文件（Chrome/Edge/Chromium）。"
                "可通过 cdp_browser_path 配置项手动指定路径。"
            )

        user_data_dir = None
        if self._user_data_dir:
            from pathlib import Path
            user_data_dir = Path(self._user_data_dir)

        self._proc = DebugBrowserProcess(
            executable=executable,
            port=self._port,
            user_data_dir=user_data_dir,
            headless=self._headless,
        )
        self._proc.start()
        self.start()

    def is_browser_running(self) -> bool:
        return bool(self._proc and self._proc.is_running)

    # ── CDP 连接线程 ──────────────────────────────────────────────────────

    def _run_ws_loop(self) -> None:
        from websockets.sync.client import connect
        from websockets.exceptions import ConnectionClosed

        try:
            ws_url = self._proc.browser_ws_url() if self._proc else None
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.perception.behavior.collectors.cdp_browser.CDPBrowserCollector._run_ws_loop')
            ws_url = None
        if not ws_url:
            return

        try:
            with connect(ws_url, open_timeout=5) as ws:
                self._ws = ws
                ws.send(json.dumps({"id": 1, "method": "Target.setDiscoverTargets", "params": {"discover": True}}))
                while not self._stop_flag.is_set():
                    try:
                        raw = ws.recv(timeout=1.0)
                    except TimeoutError:
                        continue
                    except ConnectionClosed:
                        break
                    self._handle_message(raw)
        except Exception as _mini_agent_exc:
            # 浏览器被手动关闭 / 网络异常等，静默退出线程，不刷屏重试。
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.perception.behavior.collectors.cdp_browser.CDPBrowserCollector._run_ws_loop')
            pass
        finally:
            self._flush_all_pages()
            self._ws = None

    def _handle_message(self, raw: str) -> None:
        try:
            msg = json.loads(raw)
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.perception.behavior.collectors.cdp_browser.CDPBrowserCollector._handle_message')
            return
        method = msg.get("method")
        if method not in (
            "Target.targetCreated", "Target.targetInfoChanged", "Target.targetDestroyed",
        ):
            return

        params = msg.get("params", {})
        if method == "Target.targetDestroyed":
            target_id = params.get("targetId")
            self._emit_final(target_id)
            return

        info = params.get("targetInfo", {})
        if info.get("type") != "page":
            return

        target_id = info.get("targetId")
        url = info.get("url", "")
        title = info.get("title", "")
        now = time.time()

        prev = self._pages.get(target_id)
        if prev is None:
            self._pages[target_id] = {"url": url, "title": title, "since": now}
            return
        if prev["url"] == url:
            # 只是标题变化（如页面内 SPA 更新），不产出新事件，避免刷屏
            prev["title"] = title
            return

        # URL 变了：给上一个 URL 收尾一条事件
        self._emit_visit(prev["url"], prev["title"], prev["since"], now)
        self._pages[target_id] = {"url": url, "title": title, "since": now}

    def _emit_visit(self, url: str, title: str, since: float, until: float) -> None:
        if not url:
            return
        domain, path = _redact(url, self._redact_url_path)
        if not domain:
            return
        event = ActivityEvent(
            timestamp=since,
            source="cdp_browser",
            event_type="page_visit",
            window_title=None if self._redact_title else title,
            domain=domain,
            url_path=path,
            duration_sec=round(until - since, 1),
        )
        self._store.append(event)

    def _emit_final(self, target_id: Optional[str]) -> None:
        if not target_id:
            return
        prev = self._pages.pop(target_id, None)
        if prev:
            self._emit_visit(prev["url"], prev["title"], prev["since"], time.time())

    def _flush_all_pages(self) -> None:
        now = time.time()
        for tid in list(self._pages.keys()):
            prev = self._pages.pop(tid)
            self._emit_visit(prev["url"], prev["title"], prev["since"], now)

    # ── 覆盖 BaseCollector 的 start/stop（事件驱动，不用轮询）──────────────

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_flag.clear()
        self._thread = threading.Thread(target=self._run_ws_loop, name="behavior-cdp_browser", daemon=True)
        self._thread.start()

    def stop(self, kill_browser: bool = False) -> None:
        self._stop_flag.set()
        if self._thread:
            self._thread.join(timeout=3.0)
        self._thread = None
        if kill_browser and self._proc:
            self._proc.stop()

    @property
    def is_running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def poll(self):
        # 事件驱动型采集器不使用轮询接口；实现该方法只是满足 BaseCollector 契约。
        return None

    def status(self) -> dict:
        return {
            "browser_running": self.is_browser_running(),
            "ws_connected": self.is_running,
            "port": self._port,
            "open_pages": len(self._pages),
        }
