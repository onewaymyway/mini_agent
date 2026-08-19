"""
route_change_detector.py - SPA 路由变化检测器

基于 CDP Page.navigateStarted / Page.frameNavigated 事件，
配合 JS 注入检测 history API 调用，实现精确的 SPA 路由变化等待。

与 smart_wait.py 的 "route" 策略无缝集成。

用法示例：
    from src.core.route_change_detector import RouteChangeDetector
    detector = RouteChangeDetector(session)
    await detector.start_tracking()
    await session.click("a[href='/new-page']")
    result = await detector.wait_for_change(timeout=10)
    await detector.stop_tracking()
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set

logger = logging.getLogger(__name__)


class RouteChangeEvent(Enum):
    """路由变化事件类型"""
    NAVIGATE_STARTED = "navigate_started"      # CDP: Page.navigateStarted
    FRAME_NAVIGATED = "frame_navigated"        # CDP: Page.frameNavigated
    HISTORY_CHANGED = "history_changed"        # JS: history.pushState/replaceState
    URL_CHANGED = "url_changed"                # JS: window.location 变化
    SPA_ROUTE = "spa_route"                    # SPA 框架路由变化（vue-router/react-router）


@dataclass
class RouteChangeRecord:
    """单次路由变化记录"""
    event_type: RouteChangeEvent
    old_url: str = ""
    new_url: str = ""
    timestamp: float = field(default_factory=time.time)
    frame_id: Optional[str] = None
    request_id: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_type": self.event_type.value,
            "old_url": self.old_url,
            "new_url": self.new_url,
            "timestamp": self.timestamp,
            "frame_id": self.frame_id,
            "request_id": self.request_id,
            "extra": self.extra,
        }


class RouteChangeDetector:
    """
    SPA 路由变化检测器

    监听 CDP 事件 + JS 注入，检测以下路由变化：
    1. CDP Page.navigateStarted — 导航开始
    2. CDP Page.frameNavigated — frame 级导航完成
    3. JS history API 调用（pushState/replaceState）
    4. JS window.location 变化
    5. SPA 框架路由对象变化（vue-router/react-router/angular）
    """

    # 检测 SPA 框架路由变化的 JS
    SPA_ROUTER_CHECK_JS = """
    (() => {
        const results = {};
        // Vue Router
        if (window.__VUE_ROUTER__) {
            results.vue = {
                current: window.__VUE_ROUTER__.currentRoute?.value?.path || window.__VUE_ROUTER__.currentRoute?.path,
                version: window.__VUE_ROUTER__._version
            };
        }
        // React Router v6+
        if (window.__reactRouterVersion) {
            results.react = { version: window.__reactRouterVersion };
        }
        // React Router hooks（通过 DOM 检测）
        const reactRoot = document.querySelector('[data-reactroot]');
        if (reactRoot) {
            results.react_dom = { has_root: true };
        }
        // Angular Router
        if (window.angular) {
            try {
                const injector = angular.element(document).injector();
                if (injector) {
                    const route = injector.get('$route');
                    if (route) {
                        results.angular = { current: route.current?.path };
                    }
                }
            } catch(e) {}
        }
        // Next.js
        if (window.__NEXT_DATA__) {
            results.nextjs = {
                pathname: window.__NEXT_DATA__.props?.pageProps?.pathname || 'unknown'
            };
        }
        // Nuxt
        if (window.__NUXT__) {
            results.nuxt = { path: window.__NUXT__.state?.path || 'unknown' };
        }
        return results;
    })()
    """

    def __init__(
        self,
        session: Any,
        check_spa_router: bool = True,
        check_history_api: bool = True,
        max_events: int = 50,
    ):
        self.session = session
        self.check_spa_router = check_spa_router
        self.check_history_api = check_history_api
        self.max_events = max_events

        self._events: List[RouteChangeRecord] = []
        self._tracking = False
        self._last_url: str = ""
        self._last_route_hash: str = ""
        self._event_callbacks: List[Callable] = []
        self._history_hook_installed = False
        self._stop_event: Optional[asyncio.Event] = None

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    async def start_tracking(self) -> None:
        """启动路由变化监听"""
        if self._tracking:
            return
        self._tracking = True
        self._events.clear()
        self._last_url = await self._get_current_url()
        self._last_route_hash = await self._get_route_hash()
        self._stop_event = asyncio.Event()

        # 注册 CDP 事件
        await self._register_cdp_events()

        # 注入 history API 监听（仅首次）
        if self.check_history_api and not self._history_hook_installed:
            await self._inject_history_hook()

        logger.debug("RouteChangeDetector: 开始监听路由变化")

    async def stop_tracking(self) -> None:
        """停止路由变化监听"""
        if not self._tracking:
            return
        self._tracking = False
        if self._stop_event:
            self._stop_event.set()
        logger.debug("RouteChangeDetector: 停止监听")

    # ------------------------------------------------------------------
    # 等待路由变化
    # ------------------------------------------------------------------

    async def wait_for_change(
        self,
        timeout: float = 10.0,
        min_events: int = 1,
        url_contains: Optional[str] = None,
        check_spa: bool = True,
    ) -> List[RouteChangeRecord]:
        """
        等待路由变化发生

        Args:
            timeout: 超时时间（秒）
            min_events: 最少需要检测到的事件数
            url_contains: 可选，新 URL 需包含此字符串
            check_spa: 是否额外检查 SPA 框架路由变化

        Returns:
            检测到的路由变化事件列表
        """
        initial_count = len(self._events)
        start_time = time.time()

        while time.time() - start_time < timeout:
            # 检查是否有新事件
            new_events = self._events[initial_count:]
            if len(new_events) >= min_events:
                # 过滤 URL 条件
                if url_contains:
                    filtered = [e for e in new_events if url_contains in e.new_url]
                    if filtered:
                        return filtered
                return new_events

            # 检查 SPA 路由变化
            if check_spa and self.check_spa_router:
                current_hash = await self._get_route_hash()
                if current_hash != self._last_route_hash:
                    record = RouteChangeRecord(
                        event_type=RouteChangeEvent.SPA_ROUTE,
                        old_url=self._last_url,
                        new_url=await self._get_current_url(),
                        extra={"route_hash": current_hash},
                    )
                    self._events.append(record)
                    self._last_route_hash = current_hash
                    self._fire_callbacks(record)
                    return [record]

            await asyncio.sleep(0.1)

        logger.warning(f"RouteChangeDetector: 等待路由变化超时 ({timeout}s)")
        return self._events[initial_count:]

    async def wait_for_navigate(
        self,
        target_url: str,
        timeout: float = 10.0,
    ) -> List[RouteChangeRecord]:
        """
        等待导航到指定 URL

        Args:
            target_url: 目标 URL（支持部分匹配）
            timeout: 超时时间
        """
        return await self.wait_for_change(
            timeout=timeout,
            url_contains=target_url,
            min_events=1,
        )

    # ------------------------------------------------------------------
    # 查询 API
    # ------------------------------------------------------------------

    def get_events(self) -> List[RouteChangeRecord]:
        """返回所有已记录的路由变化事件"""
        return list(self._events)

    def get_last_event(self) -> Optional[RouteChangeRecord]:
        """返回最新的路由变化事件"""
        return self._events[-1] if self._events else None

    def get_event_count(self) -> int:
        """返回已记录的事件数量"""
        return len(self._events)

    def get_current_url(self) -> str:
        """返回当前记录的 URL"""
        return self._last_url

    def on_event(self, callback: Callable[[RouteChangeRecord], None]) -> None:
        """注册事件回调"""
        self._event_callbacks.append(callback)

    def _fire_callbacks(self, record: RouteChangeRecord) -> None:
        """触发所有回调"""
        for cb in self._event_callbacks:
            try:
                cb(record)
            except Exception as e:
                logger.warning(f"RouteChangeDetector: 回调执行失败: {e}")

    # ------------------------------------------------------------------
    # CDP 事件注册
    # ------------------------------------------------------------------

    async def _register_cdp_events(self) -> None:
        """注册 CDP 事件监听"""
        session = self.session

        # Page.navigateStarted
        async def on_navigate_started(params: Dict) -> None:
            if not self._tracking:
                return
            request_id = params.get("requestId", "")
            url = params.get("url", "")
            record = RouteChangeRecord(
                event_type=RouteChangeEvent.NAVIGATE_STARTED,
                old_url=self._last_url,
                new_url=url,
                request_id=request_id,
            )
            self._append_event(record)
            self._last_url = url
            self._fire_callbacks(record)

        # Page.frameNavigated
        async def on_frame_navigated(params: Dict) -> None:
            if not self._tracking:
                return
            frame_id = params.get("frame", {}).get("id", "")
            url = params.get("url", "")
            record = RouteChangeRecord(
                event_type=RouteChangeEvent.FRAME_NAVIGATED,
                old_url=self._last_url,
                new_url=url,
                frame_id=frame_id,
            )
            self._append_event(record)
            self._last_url = url
            self._fire_callbacks(record)

        # 注册事件监听（通过 CDP Runtime.evaluate 或 session 的 on_event 方法）
        try:
            # 尝试使用 session 的事件注册 API
            if hasattr(session, "on_cdp_event"):
                session.on_cdp_event("Page.navigateStarted", on_navigate_started)
                session.on_cdp_event("Page.frameNavigated", on_frame_navigated)
            else:
                # 降级：通过 CDP send 注册
                await session.send("Page.enable")
                logger.debug("RouteChangeDetector: CDP Page.enable 已发送")
        except Exception as e:
            logger.warning(f"RouteChangeDetector: 注册 CDP 事件失败: {e}")

    # ------------------------------------------------------------------
    # History API 钩子
    # ------------------------------------------------------------------

    async def _inject_history_hook(self) -> None:
        """注入 history API 监听脚本"""
        hook_js = """
        (() => {
            if (window.__browser_cdp_history_hook__) return;
            window.__browser_cdp_history_hook__ = true;
            const originalPush = history.pushState.bind(history);
            const originalReplace = history.replaceState.bind(history);
            history.pushState = function(...args) {
                originalPush(...args);
                window.__browser_cdp_history_event__ = {
                    type: 'pushState',
                    url: window.location.href,
                    timestamp: Date.now()
                };
            };
            history.replaceState = function(...args) {
                originalReplace(...args);
                window.__browser_cdp_history_event__ = {
                    type: 'replaceState',
                    url: window.location.href,
                    timestamp: Date.now()
                };
            };
        })()
        """
        try:
            await self.session.eval_js(hook_js)
            self._history_hook_installed = True
            logger.debug("RouteChangeDetector: History API 钩子已注入")
        except Exception as e:
            logger.warning(f"RouteChangeDetector: 注入 History API 钩子失败: {e}")

    async def check_history_event(self) -> Optional[Dict]:
        """检查是否有 history API 触发的事件"""
        if not self.check_history_api:
            return None
        try:
            result = await self.session.eval_js(
                "window.__browser_cdp_history_event__ || null"
            )
            if result:
                record = RouteChangeRecord(
                    event_type=RouteChangeEvent.HISTORY_CHANGED,
                    old_url=self._last_url,
                    new_url=result.get("url", ""),
                    extra={"type": result.get("type"), "timestamp": result.get("timestamp")},
                )
                self._append_event(record)
                self._last_url = result.get("url", "")
                self._fire_callbacks(record)
                # 清除事件标记
                await self.session.eval_js("delete window.__browser_cdp_history_event__")
            return result
        except Exception as e:
            logger.warning(f"RouteChangeDetector: 检查 history 事件失败: {e}")
            return None

    # ------------------------------------------------------------------
    # 辅助方法
    # ------------------------------------------------------------------

    async def _get_current_url(self) -> str:
        """获取当前页面 URL"""
        try:
            return await self.session.eval_js("window.location.href")
        except Exception:
            return self._last_url

    async def _get_route_hash(self) -> str:
        """获取当前路由哈希（用于 SPA 框架检测）"""
        try:
            result = await self.session.eval_js(self.SPA_ROUTER_CHECK_JS)
            return str(result) if result else ""
        except Exception:
            return ""

    def _append_event(self, record: RouteChangeRecord) -> None:
        """添加事件记录（带上限）"""
        self._events.append(record)
        if len(self._events) > self.max_events:
            self._events = self._events[-self.max_events:]

    async def reset(self) -> None:
        """重置检测器状态"""
        await self.stop_tracking()
        self._events.clear()
        self._last_url = ""
        self._last_route_hash = ""
        self._history_hook_installed = False
        logger.debug("RouteChangeDetector: 已重置")


# =====================================================================
# 便捷函数
# =====================================================================

async def wait_for_spa_route(
    session: Any,
    target_url: Optional[str] = None,
    timeout: float = 10.0,
) -> List[Dict[str, Any]]:
    """
    便捷函数：等待 SPA 路由变化

    Args:
        session: CDP session 对象
        target_url: 可选，目标 URL（部分匹配）
        timeout: 超时时间

    Returns:
        路由变化事件列表（字典格式）
    """
    detector = RouteChangeDetector(session)
    await detector.start_tracking()
    try:
        if target_url:
            events = await detector.wait_for_navigate(target_url, timeout=timeout)
        else:
            events = await detector.wait_for_change(timeout=timeout)
        return [e.to_dict() for e in events]
    finally:
        await detector.stop_tracking()
