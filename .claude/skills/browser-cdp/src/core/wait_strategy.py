"""
wait_strategy.py - 动态页面渲染等待策略

提供JavaScript渲染完成检测、滚动加载识别和弹窗拦截功能，
用于SPA网站的可靠抓取。
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


class RenderStatus(Enum):
    LOADING = "loading"
    IDLE = "idle"
    COMPLETE = "complete"
    TIMEOUT = "timeout"
    ERROR = "error"


class ScrollState(Enum):
    STABLE = "stable"
    LOADING = "loading"
    END_REACHED = "end_reached"
    ERROR = "error"


class PopupType(Enum):
    COOKIE_BANNER = "cookie_banner"
    SUBSCRIPTION = "subscription"
    CAPTCHA = "captcha"
    MODAL = "modal"
    ADVERTISING = "advertising"
    ALERT = "alert"
    UNKNOWN = "unknown"


@dataclass
class WaitForResult:
    status: RenderStatus
    wait_time: float = 0.0
    items_count: int = 0
    url: str = ""
    error: Optional[str] = None
    details: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        return {"status": self.status.value, "wait_time": self.wait_time, "items_count": self.items_count, "url": self.url, "error": self.error, "details": self.details}


@dataclass
class ScrollResult:
    state: ScrollState
    pages_scrolled: int = 0
    total_items: int = 0
    new_items_per_page: List[int] = field(default_factory=list)
    error: Optional[str] = None


class JSRenderDetector:
    """JavaScript渲染完成检测器"""
    
    DETECTION_SCRIPTS = {
        "ready_state": "document.readyState",
        "ajax_count": "window._pendingAjax || 0",
        "dom_changes": "window._mutationCount || 0",
    }
    
    def __init__(self, session: Any):
        self._session = session
        self._mutation_observer_installed = False
    
    async def wait_for_ready_state(self, target_state: str = "complete", timeout: float = 30.0, check_interval: float = 0.5) -> WaitForResult:
        start_time = time.time()
        while time.time() - start_time < timeout:
            try:
                state = await self._session.eval_js(self.DETECTION_SCRIPTS["ready_state"])
                if state == target_state:
                    return WaitForResult(status=RenderStatus.COMPLETE, wait_time=time.time() - start_time, url=await self._get_current_url(), details={"readyState": state})
            except Exception as e:
                logger.debug(f"等待readyState时出错: {e}")
            await asyncio.sleep(check_interval)
        return WaitForResult(status=RenderStatus.TIMEOUT, wait_time=time.time() - start_time, error=f"等待{target_state}状态超时({timeout}s)")
    
    async def wait_for_selector(self, selector: str, timeout: float = 30.0, check_interval: float = 0.5, visible: bool = True) -> WaitForResult:
        start_time = time.time()
        while time.time() - start_time < timeout:
            try:
                if visible:
                    exists = await self._session.eval_js(f"document.querySelector('{selector}') !== null && getComputedStyle(document.querySelector('{selector}')).display !== 'none'")
                else:
                    exists = await self._session.eval_js(f"document.querySelector('{selector}') !== null")
                if exists:
                    count = await self._session.eval_js(f"document.querySelectorAll('{selector}').length")
                    return WaitForResult(status=RenderStatus.COMPLETE, wait_time=time.time() - start_time, items_count=count, details={"selector": selector, "count": count})
            except Exception as e:
                logger.debug(f"等待选择器 '{selector}' 时出错: {e}")
            await asyncio.sleep(check_interval)
        return WaitForResult(status=RenderStatus.TIMEOUT, wait_time=time.time() - start_time, error=f"等待选择器 '{selector}' 超时")
    
    async def wait_for_network_idle(self, timeout: float = 10.0, idle_threshold: int = 5, check_interval: float = 0.2) -> WaitForResult:
        start_time = time.time()
        idle_count = 0
        while time.time() - start_time < timeout:
            try:
                network_status = await self._session.eval_js("(() => { const resources = performance.getEntriesByType('resource'); const recent = resources.filter(r => (performance.now() - r.startTime) < 1000); return { active: recent.length, total: resources.length }; })()")
                active_requests = network_status.get('active', 0)
                if active_requests == 0:
                    idle_count += 1
                    if idle_count >= idle_threshold:
                        return WaitForResult(status=RenderStatus.IDLE, wait_time=time.time() - start_time, details={"active_requests": 0, "idle_checks": idle_count})
                else:
                    idle_count = 0
            except Exception as e:
                logger.debug(f"检查网络状态时出错: {e}")
            await asyncio.sleep(check_interval)
        return WaitForResult(status=RenderStatus.TIMEOUT, wait_time=time.time() - start_time, error="网络空闲检测超时")
    
    async def _get_current_url(self) -> str:
        try:
            return await self._session.eval_js("window.location.href")
        except:
            return ""
    
    def install_mutation_observer(self) -> bool:
        if self._mutation_observer_installed:
            return True
        script = "(() => { if (window._mutationObserver) return true; window._mutationCount = 0; window._mutationObserver = new MutationObserver((mutations) => { window._mutationCount += mutations.length; }); window._mutationObserver.observe(document.body, { childList: true, subtree: true, attributes: true, characterData: true }); return true; })()"
        try:
            result = self._session.eval_js(script)
            self._mutation_observer_installed = bool(result)
            return self._mutation_observer_installed
        except Exception as e:
            logger.error(f"安装MutationObserver失败: {e}")
            return False
    
    def get_mutation_count(self) -> int:
        if not self._mutation_observer_installed:
            return 0
        try:
            return self._session.eval_js("window._mutationCount || 0")
        except:
            return 0


class ScrollLoadMonitor:
    """滚动加载监控器"""
    
    def __init__(self, session: Any):
        self._session = session
        self._scroll_callbacks: List[Callable] = []
    
    def on_content_loaded(self, callback: Callable[[int, int], None]) -> None:
        self._scroll_callbacks.append(callback)
    
    async def wait_for_scroll_load(self, max_pages: int = 10, scroll_amount: int = 800, stability_threshold: int = 3, item_selector: Optional[str] = None, timeout: float = 60.0) -> ScrollResult:
        start_time = time.time()
        pages_scrolled = 0
        stable_count = 0
        prev_item_count = 0
        new_items_per_page = []
        
        while pages_scrolled < max_pages and (time.time() - start_time) < timeout:
            if item_selector:
                current_count = await self._count_items(item_selector)
            else:
                current_count = await self._get_page_height()
            
            new_items = current_count - prev_item_count
            new_items_per_page.append(new_items)
            
            for callback in self._scroll_callbacks:
                try:
                    callback(pages_scrolled + 1, current_count)
                except Exception as e:
                    logger.warning(f"滚动回调执行失败: {e}")
            
            if new_items <= 0:
                stable_count += 1
                if stable_count >= stability_threshold:
                    return ScrollResult(state=ScrollState.END_REACHED, pages_scrolled=pages_scrolled, total_items=current_count, new_items_per_page=new_items_per_page)
            else:
                stable_count = 0
                prev_item_count = current_count
            
            await self._scroll_by(scroll_amount)
            pages_scrolled += 1
            await asyncio.sleep(0.5)
        
        if pages_scrolled >= max_pages:
            state = ScrollState.LOADING
            error = f"达到最大滚动页数限制({max_pages})"
        else:
            state = ScrollState.STABLE
            error = None
        
        return ScrollResult(state=state, pages_scrolled=pages_scrolled, total_items=prev_item_count, new_items_per_page=new_items_per_page, error=error)
    
    async def _scroll_by(self, amount: int) -> None:
        await self._session.eval_js(f"window.scrollBy(0, {amount})")
    
    async def _get_page_height(self) -> int:
        return await self._session.eval_js("document.documentElement.scrollHeight")
    
    async def _count_items(self, selector: str) -> int:
        try:
            return await self._session.eval_js(f"document.querySelectorAll('{selector}').length")
        except:
            return 0


class PopupInterceptor:
    """弹窗拦截器"""
    
    POPUP_SELECTORS = {
        PopupType.COOKIE_BANNER: ['[class*="cookie"]', '[class*="consent"]', '.cookie-banner'],
        PopupType.SUBSCRIPTION: ['[class*="subscribe"]', '.subscription-popup'],
        PopupType.CAPTCHA: ['.g-recaptcha', '.hcaptcha', '[class*="captcha"]'],
        PopupType.MODAL: ['.modal', '[role="dialog"]', '[class*="popup"]'],
        PopupType.ADVERTISING: ['[class*="ad"]', '.ad-banner'],
    }
    
    CLOSE_SELECTORS = ['[class*="close"]', '[class*="dismiss"]', '.close', '.dismiss']
    
    def __init__(self, session: Any):
        self._session = session
        self._intercepted_popups: List[Dict] = []
    
    async def detect_popups(self) -> List[Dict[str, Any]]:
        popups = []
        for popup_type, selectors in self.POPUP_SELECTORS.items():
            for selector in selectors:
                try:
                    count = await self._session.eval_js(f"document.querySelectorAll('{selector}').length")
                    if count > 0:
                        popups.append({"type": popup_type.value, "selector": selector, "count": count})
                        break
                except Exception:
                    pass
        return popups
    
    async def dismiss_popup(self, popup_type: Optional[PopupType] = None, selector: Optional[str] = None, timeout: float = 5.0) -> bool:
        start_time = time.time()
        targets = []
        if selector:
            targets = [(selector, "custom")]
        elif popup_type:
            targets = [(s, popup_type.value) for s in self.POPUP_SELECTORS.get(popup_type, [])]
        else:
            for ptype, selectors in self.POPUP_SELECTORS.items():
                for s in selectors:
                    targets.append((s, ptype.value))
        
        for target_selector, ptype in targets:
            if time.time() - start_time > timeout:
                break
            try:
                for close_selector in self.CLOSE_SELECTORS:
                    close_btn = await self._session.eval_js(f"(() => {{ const els = document.querySelectorAll('{target_selector} {close_selector}'); return els.length > 0 ? els[0].tagName + '.' + els[0].className : null; }})()")
                    if close_btn:
                        await self._session.eval_js(f"(() => {{ const els = document.querySelectorAll('{target_selector} {close_selector}'); if (els.length > 0) {{ els[0].click(); return true; }} return false; }})()")
                        await asyncio.sleep(0.5)
                        remaining = await self._session.eval_js(f"document.querySelectorAll('{target_selector}').length")
                        if remaining == 0:
                            self._intercepted_popups.append({"type": ptype, "closed_at": time.time()})
                            return True
            except Exception as e:
                logger.warning(f"关闭弹窗失败 ({target_selector}): {e}")
        return False
    
    async def dismiss_all_popups(self, max_attempts: int = 5, timeout_per_popup: float = 5.0) -> List[Dict]:
        closed_popups = []
        for _ in range(max_attempts):
            popups = await self.detect_popups()
            if not popups:
                break
            for popup in popups:
                success = await self.dismiss_popup(selector=popup["selector"], timeout=timeout_per_popup)
                if success:
                    closed_popups.append(popup)
                    break
        return closed_popups
    
    def get_intercepted_history(self) -> List[Dict]:
        return list(self._intercepted_popups)
    
    def clear_history(self):
        self._intercepted_popups.clear()


class SPAWaitStrategy:
    """SPA网站等待策略管理器"""
    
    def __init__(self, session: Any):
        self._session = session
        self._js_detector = JSRenderDetector(session)
        self._scroll_monitor = ScrollLoadMonitor(session)
        self._popup_interceptor = PopupInterceptor(session)
    
    @property
    def js_detector(self) -> JSRenderDetector:
        return self._js_detector
    
    @property
    def scroll_monitor(self) -> ScrollLoadMonitor:
        return self._scroll_monitor
    
    @property
    def popup_interceptor(self) -> PopupInterceptor:
        return self._popup_interceptor
    
    async def wait_for_page_ready(self, timeout: float = 30.0, strategies: Optional[List[str]] = None) -> WaitForResult:
        if strategies is None:
            strategies = ["ready_state", "network"]
        start_time = time.time()
        tasks = []
        if "ready_state" in strategies:
            tasks.append(self._js_detector.wait_for_ready_state(timeout=timeout))
        if "network" in strategies:
            tasks.append(self._js_detector.wait_for_network_idle(timeout=timeout))
        
        results = []
        if tasks:
            completed, _ = await asyncio.wait(tasks, timeout=timeout)
            for task in completed:
                try:
                    results.append(task.result())
                except Exception as e:
                    logger.warning(f"等待策略执行失败: {e}")
        
        if not results:
            return WaitForResult(status=RenderStatus.ERROR, wait_time=time.time() - start_time, error="所有等待策略均未执行")
        
        success_results = [r for r in results if r.status in (RenderStatus.COMPLETE, RenderStatus.IDLE)]
        if success_results:
            best_result = min(success_results, key=lambda r: r.wait_time)
            best_result.status = RenderStatus.COMPLETE
            best_result.wait_time = time.time() - start_time
            best_result.details["strategies_used"] = strategies
            return best_result
        
        timeout_result = min(results, key=lambda r: r.wait_time)
        timeout_result.status = RenderStatus.TIMEOUT
        timeout_result.wait_time = time.time() - start_time
        timeout_result.details["strategies_used"] = strategies
        return timeout_result
    
    async def wait_for_spa_navigation(self, url_contains: Optional[str] = None, selector: Optional[str] = None, timeout: float = 30.0) -> WaitForResult:
        start_time = time.time()
        if url_contains:
            while time.time() - start_time < timeout:
                current_url = await self._session.eval_js("window.location.href")
                if url_contains in current_url:
                    if selector:
                        result = await self._js_detector.wait_for_selector(selector, timeout=5.0)
                        result.wait_time = time.time() - start_time
                        result.url = current_url
                        return result
                    return WaitForResult(status=RenderStatus.COMPLETE, wait_time=time.time() - start_time, url=current_url)
                await asyncio.sleep(0.3)
        if selector:
            result = await self._js_detector.wait_for_selector(selector, timeout=timeout)
            result.wait_time = time.time() - start_time
            result.url = await self._session.eval_js("window.location.href")
            return result
        result = await self._js_detector.wait_for_network_idle(timeout=timeout)
        result.wait_time = time.time() - start_time
        result.url = await self._session.eval_js("window.location.href")
        return result
    
    async def load_with_scroll(self, max_pages: int = 10, item_selector: Optional[str] = None, timeout: float = 60.0) -> ScrollResult:
        return await self._scroll_monitor.wait_for_scroll_load(max_pages=max_pages, item_selector=item_selector, timeout=timeout)
    
    async def handle_popups(self, auto_dismiss: bool = True, max_attempts: int = 5) -> List[Dict]:
        if not auto_dismiss:
            return await self._popup_interceptor.detect_popups()
        return await self._popup_interceptor.dismiss_all_popups(max_attempts=max_attempts)
    
    async def smart_wait(self, url: str, timeout: float = 30.0, item_selector: Optional[str] = None, auto_handle_popups: bool = True) -> WaitForResult:
        """智能等待策略"""
        start_time = time.time()
        await self._session.goto(url)
        if auto_handle_popups:
            await self._popup_interceptor.dismiss_all_popups(max_attempts=3)
        page_result = await self.wait_for_page_ready(timeout=timeout)
        if page_result.status != RenderStatus.COMPLETE and page_result.status != RenderStatus.IDLE:
            if item_selector:
                selector_result = await self._js_detector.wait_for_selector(item_selector, timeout=10.0)
                if selector_result.status == RenderStatus.COMPLETE:
                    page_result = selector_result
        if item_selector:
            scroll_result = await self.load_with_scroll(max_pages=3, item_selector=item_selector, timeout=15.0)
            page_result.details["scroll_loaded"] = scroll_result.total_items
        page_result.wait_time = time.time() - start_time
        page_result.url = url
        return page_result
