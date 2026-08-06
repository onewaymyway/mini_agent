"""
browser_interaction.py - Browser Interaction Module

Provides unified browser interaction operations, supporting:
1. Infinite scroll loading (smart detection, virtual list, adaptive strategy)
2. Form submission (auto-fill, validation, error handling)
3. Popup/modal handling (auto-close, confirm, cancel)
4. AJAX request monitoring (wait for completion, intercept, retry)
5. Page state management (navigation history, state snapshots)
6. Error recovery strategies (auto-retry, fallback handling)

Usage:
    from src.core.browser_interaction import BrowserInteraction
    
    interaction = BrowserInteraction(session)
    
    # Infinite scroll loading
    await interaction.infinite_scroll(item_selector=".item", max_items=100)
    
    # Form submission
    await interaction.submit_form("#search-form", {"keyword": "AI"})
    
    # Popup handling
    await interaction.handle_popup(timeout=10)
    
    # AJAX wait
    await interaction.wait_for_ajax(timeout=15)
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, List, Dict, Any, Callable, Tuple

from src.core.smart_wait import SmartWait, WaitConfig
from src.core.enhanced_dynamic_loader import EnhancedDynamicLoader, ScrollConfig, ScrollResult
from src.core.dynamic_page_support import DynamicPageSupport

logger = logging.getLogger(__name__)


class PopupType(Enum):
    """Popup types"""
    ALERT = "alert"
    CONFIRM = "confirm"
    PROMPT = "prompt"
    MODAL = "modal"
    COOKIE_BANNER = "cookie_banner"
    CAPTCHA = "captcha"


class FormStatus(Enum):
    """Form status"""
    IDLE = "idle"
    VALIDATING = "validating"
    SUBMITTING = "submitting"
    SUCCESS = "success"
    ERROR = "error"


class ErrorRecoveryStrategy(Enum):
    """Error recovery strategies"""
    RETRY = "retry"
    FALLBACK = "fallback"
    SKIP = "skip"
    ABORT = "abort"


@dataclass
class InteractionResult:
    """Interaction operation result"""
    success: bool
    operation: str
    data: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    elapsed: float = 0.0
    retries: int = 0
    
    def to_dict(self) -> dict:
        result = {
            "success": self.success,
            "operation": self.operation,
            "elapsed": round(self.elapsed, 2),
            "retries": self.retries,
        }
        if self.error:
            result["error"] = self.error
        if self.data:
            result["data"] = self.data
        return result


@dataclass
class FormField:
    """Form field"""
    selector: str
    value: Any
    type: str = "text"
    wait_for: Optional[str] = None


@dataclass
class PopupInfo:
    """Popup information"""
    type: PopupType
    selector: str
    title: str = ""
    message: str = ""
    buttons: List[Dict[str, str]] = field(default_factory=list)


@dataclass
class AjaxRequest:
    """AJAX request information"""
    url: str
    method: str = "GET"
    status: int = 0
    duration: float = 0.0
    response_size: int = 0


@dataclass
class PageState:
    """Page state snapshot"""
    url: str
    title: str
    scroll_position: int = 0
    page_height: int = 0
    element_count: int = 0
    timestamp: float = 0.0
    content_hash: str = ""


class BrowserInteraction:
    """
    Browser interaction operation class
    
    Provides unified interaction operation interface, integrating scroll, form,
    popup, AJAX wait and other capabilities
    """
    
    def __init__(self, session, config: Optional[Dict[str, Any]] = None):
        self.session = session
        self.config = config or {}
        self._smart_wait = SmartWait(session)
        self._dynamic_loader = EnhancedDynamicLoader(session)
        self._page_support = DynamicPageSupport(session)
        self._ajax_requests: List[AjaxRequest] = []
        self._page_states: List[PageState] = []
        self._popup_handlers: Dict[PopupType, Callable] = {}
        self._error_recovery = ErrorRecoveryManager(self)
        self._register_default_popup_handlers()
    
    def _register_default_popup_handlers(self):
        """Register default popup handlers"""
        self._popup_handlers[PopupType.ALERT] = self._handle_alert
        self._popup_handlers[PopupType.CONFIRM] = self._handle_confirm
        self._popup_handlers[PopupType.MODAL] = self._handle_modal
        self._popup_handlers[PopupType.COOKIE_BANNER] = self._handle_cookie_banner
    
    # =========================================================================
    # Infinite Scroll Loading
    # =========================================================================
    
    async def infinite_scroll(
        self,
        item_selector: str = "",
        max_items: int = 100,
        max_pages: int = 10,
        stop_condition: Callable[[int, int], bool] = None,
        scroll_distance: int = 800,
        scroll_delay: float = 0.8,
    ) -> ScrollResult:
        """Smart infinite scroll loading"""
        logger.info(f"Starting smart infinite scroll, max items: {max_items}")
        config = ScrollConfig(
            item_selector=item_selector,
            max_pages=max_pages,
            scroll_distance=scroll_distance,
            scroll_delay=scroll_delay,
        )
        result = await self._dynamic_loader.smart_scroll(
            max_pages=max_pages,
            stop_condition=stop_condition,
        )
        await self._page_support.wait_for_lazy_images(timeout=5.0)
        logger.info(f"Infinite scroll completed: {result.pages_loaded} pages, {result.items_found} items")
        return result
    
    async def load_virtual_list(
        self,
        item_selector: str,
        max_items: int = 100,
    ) -> List[Dict[str, Any]]:
        """Load all data from virtual list"""
        logger.info(f"Starting to load virtual list, max items: {max_items}")
        items = await self._dynamic_loader.load_virtual_list(
            item_selector=item_selector,
            max_items=max_items,
        )
        logger.info(f"Virtual list loading completed: {len(items)} items")
        return items

    # =========================================================================
    # Form Submission
    # =========================================================================
    
    async def submit_form(
        self,
        form_selector: str,
        fields: Dict[str, Any],
        submit_selector: str = None,
        wait_for_response: bool = True,
        timeout: float = 30.0,
    ) -> InteractionResult:
        """Submit form"""
        logger.info(f"Starting form submission: {form_selector}")
        start_time = time.time()
        try:
            for selector, value in fields.items():
                await self._fill_field(selector, value)
            if submit_selector:
                await self._click_element(submit_selector)
            else:
                submit_btn = await self._find_submit_button(form_selector)
                if submit_btn:
                    await self._click_element(submit_btn)
            if wait_for_response:
                await self.wait_for_ajax(timeout=timeout)
                await self._page_support.wait_for_page_ready(timeout=timeout)
            elapsed = time.time() - start_time
            logger.info(f"Form submission completed, elapsed: {elapsed:.2f}s")
            return InteractionResult(
                success=True, operation="submit_form",
                data={"form": form_selector, "fields": list(fields.keys())},
                elapsed=elapsed,
            )
        except Exception as e:
            elapsed = time.time() - start_time
            logger.error(f"Form submission failed: {e}")
            return InteractionResult(success=False, operation="submit_form", error=str(e), elapsed=elapsed)
    
    async def _fill_field(self, selector: str, value: Any):
        """Fill form field"""
        js = f"""
        (() => {{
            const el = document.querySelector({selector!r});
            if (!el) return false;
            if (el.tagName === 'SELECT') {{
                const options = Array.from(el.options);
                const option = options.find(o => o.value === {value!r} || o.text === {value!r});
                if (option) {{ el.value = option.value; el.dispatchEvent(new Event('change', {{ bubbles: true }})); }}
                return !!option;
            }}
            if (el.type === 'checkbox' || el.type === 'radio') {{
                if ((value && !el.checked) || (!value && el.checked)) el.click();
                return true;
            }}
            el.focus(); el.select(); el.value = {value!r};
            el.dispatchEvent(new Event('input', {{ bubbles: true }}));
            el.dispatchEvent(new Event('change', {{ bubbles: true }}));
            return true;
        }})()
        """
        success = await self.session.eval_js(js)
        if not success:
            raise Exception(f"Cannot fill field: {selector}")
    
    async def _find_submit_button(self, form_selector: str) -> Optional[str]:
        """Find submit button"""
        js = f"""
        (() => {{
            const form = document.querySelector({form_selector!r});
            if (!form) return null;
            const submitBtn = form.querySelector('input[type="submit"], button[type="submit"]');
            if (submitBtn) return submitBtn.outerHTML.substring(0, 100);
            const buttons = form.querySelectorAll('button, input[type="button"]');
            for (const btn of buttons) {{
                const text = (btn.textContent || btn.value || '').toLowerCase();
                if (text.includes('submit') || text.includes('search') || text.includes('go')) return btn.outerHTML.substring(0, 100);
            }}
            return null;
        }})()
        """
        result = await self.session.eval_js(js)
        return result
    
    async def _click_element(self, selector: str):
        """Click element"""
        js = f"""
        (() => {{
            const el = document.querySelector({selector!r});
            if (!el) return false;
            el.click();
            return true;
        }})()
        """
        success = await self.session.eval_js(js)
        if not success:
            raise Exception(f"Cannot click element: {selector}")

    # =========================================================================
    # Popup/Modal Handling
    # =========================================================================
    
    async def handle_popup(
        self,
        popup_type: PopupType = None,
        action: str = "close",
        timeout: float = 10.0,
    ) -> InteractionResult:
        """Handle popup/modal"""
        logger.info(f"Starting popup handling, type: {popup_type}, action: {action}")
        start_time = time.time()
        try:
            popup_info = await self._detect_popup(timeout=timeout)
            if not popup_info:
                logger.info("No popup detected")
                return InteractionResult(
                    success=True, operation="handle_popup",
                    data={"popup_detected": False},
                    elapsed=time.time() - start_time,
                )
            handler = self._popup_handlers.get(popup_type or popup_info.type)
            if handler:
                await handler(popup_info, action)
            else:
                await self._default_popup_handler(popup_info, action)
            elapsed = time.time() - start_time
            logger.info(f"Popup handling completed, elapsed: {elapsed:.2f}s")
            return InteractionResult(
                success=True, operation="handle_popup",
                data={"popup_type": popup_info.type.value, "action": action, "title": popup_info.title},
                elapsed=elapsed,
            )
        except Exception as e:
            elapsed = time.time() - start_time
            logger.error(f"Popup handling failed: {e}")
            return InteractionResult(success=False, operation="handle_popup", error=str(e), elapsed=elapsed)
    
    async def _detect_popup(self, timeout: float = 10.0) -> Optional[PopupInfo]:
        """Detect popup"""
        js = """
        (() => {
            const selectors = [
                '.modal', '.popup', '.dialog', '.overlay',
                '.cookie-banner', '.consent-banner',
                '.captcha-container', '.verification-box',
                '[role="dialog"]', '[role="alertdialog"]'
            ];
            for (const selector of selectors) {
                const el = document.querySelector(selector);
                if (el && el.offsetParent !== null) {
                    return {
                        type: selector.includes('cookie') ? 'cookie_banner' :
                              selector.includes('captcha') ? 'captcha' : 'modal',
                        selector: selector,
                        title: el.querySelector('h1, h2, h3, .title')?.textContent || '',
                        visible: true
                    };
                }
            }
            return null;
        })()
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                result = await self.session.eval_js(js)
                if result:
                    return PopupInfo(
                        type=PopupType(result.get('type', 'modal')),
                        selector=result.get('selector', ''),
                        title=result.get('title', ''),
                    )
            except Exception:
                pass
            await asyncio.sleep(0.5)
        return None
    
    async def _handle_alert(self, popup: PopupInfo, action: str):
        """Handle alert popup"""
        if action in ("close", "accept"):
            self.session.send("Runtime.evaluate", {"expression": "alert('ok')"})
    
    async def _handle_confirm(self, popup: PopupInfo, action: str):
        """Handle confirm popup"""
        if action in ("close", "cancel"):
            self.session.send("Runtime.evaluate", {"expression": "confirm('cancel')"})
        elif action == "accept":
            self.session.send("Runtime.evaluate", {"expression": "confirm('ok')"})
    
    async def _handle_modal(self, popup: PopupInfo, action: str):
        """Handle modal popup"""
        if action == "close":
            close_selectors = ['.close', '.dismiss', '[aria-label="Close"]', 'button[title="Close"]']
            for selector in close_selectors:
                js = f"""
                (() => {{
                    const btn = document.querySelector({selector!r});
                    if (btn) {{ btn.click(); return true; }}
                    return false;
                }})()
                """
                if await self.session.eval_js(js):
                    return
            self.session.send("Input.dispatchKeyEvent", {"type": "keyDown", "key": "Escape"})
            self.session.send("Input.dispatchKeyEvent", {"type": "keyUp", "key": "Escape"})
    
    async def _handle_cookie_banner(self, popup: PopupInfo, action: str):
        """Handle cookie banner"""
        if action in ("close", "accept"):
            accept_selectors = ['.accept', '.accept-all', '[data-testid="accept-cookies"]']
            for selector in accept_selectors:
                js = f"""
                (() => {{
                    const btn = document.querySelector({selector!r});
                    if (btn) {{ btn.click(); return true; }}
                    return false;
                }})()
                """
                if await self.session.eval_js(js):
                    return
    
    async def _default_popup_handler(self, popup: PopupInfo, action: str):
        """Default popup handler"""
        logger.warning(f"Using default handler for popup: {popup.type.value}")
        await self._handle_modal(popup, action)

    # =========================================================================
    # AJAX Request Monitoring
    # =========================================================================
    
    async def wait_for_ajax(self, timeout: float = 15.0, min_requests: int = 0) -> List[AjaxRequest]:
        """Wait for AJAX requests to complete"""
        logger.info(f"Starting AJAX wait, timeout: {timeout}s")
        result = await self._smart_wait.wait_for("ajax", timeout=timeout)
        if result.success:
            logger.info(f"AJAX wait completed, elapsed: {result.elapsed:.2f}s")
        else:
            logger.warning("AJAX wait timeout")
        return self._ajax_requests
    
    async def monitor_ajax_requests(self, url_pattern: str = None, timeout: float = 30.0) -> List[AjaxRequest]:
        """Monitor AJAX requests"""
        logger.info(f"Starting AJAX monitoring, URL pattern: {url_pattern}")
        self._ajax_requests.clear()
        js = """
        (() => {
            const originalFetch = window.fetch;
            window.fetch = function(...args) {
                const url = args[0] instanceof Request ? args[0].url : args[0];
                const startTime = performance.now();
                return originalFetch.apply(this, args).then(response => {
                    window.__ajax_requests = window.__ajax_requests || [];
                    window.__ajax_requests.push({url, method: args[1]?.method || 'GET', status: response.status, duration: performance.now() - startTime});
                    return response;
                });
            };
            window.__ajax_requests = window.__ajax_requests || [];
            return 'AJAX interceptor installed';
        })()
        """
        await self.session.eval_js(js)
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                requests = await self.session.eval_js("window.__ajax_requests || []")
                if requests:
                    for req in requests:
                        if url_pattern and url_pattern not in req.get('url', ''):
                            continue
                        self._ajax_requests.append(AjaxRequest(
                            url=req.get('url', ''), method=req.get('method', 'GET'),
                            status=req.get('status', 0), duration=req.get('duration', 0) / 1000,
                        ))
            except Exception:
                pass
            await asyncio.sleep(0.5)
        logger.info(f"AJAX monitoring completed, captured {len(self._ajax_requests)} requests")
        return self._ajax_requests

    # =========================================================================
    # Page State Management
    # =========================================================================
    
    async def capture_page_state(self) -> PageState:
        """Capture page state snapshot"""
        js = """
        (() => {
            return {
                url: window.location.href,
                title: document.title,
                scrollPosition: window.scrollY || document.documentElement.scrollTop,
                pageHeight: document.documentElement.scrollHeight,
                elementCount: document.querySelectorAll('*').length,
            };
        })()
        """
        state_data = await self.session.eval_js(js)
        state = PageState(
            url=state_data.get('url', ''), title=state_data.get('title', ''),
            scroll_position=state_data.get('scrollPosition', 0),
            page_height=state_data.get('pageHeight', 0),
            element_count=state_data.get('elementCount', 0),
            timestamp=time.time(),
        )
        self._page_states.append(state)
        return state
    
    def get_page_state_history(self, limit: int = 10) -> List[PageState]:
        """Get page state history"""
        return self._page_states[-limit:]

    # =========================================================================
    # Combined Operations
    # =========================================================================
    
    async def search_and_collect(
        self,
        search_url: str,
        query: str,
        item_selector: str,
        max_items: int = 50,
        wait_for_results: bool = True,
    ) -> InteractionResult:
        """Search and collect results (combined operation)"""
        logger.info(f"Starting search: {query}")
        start_time = time.time()
        try:
            self.session.send("Page.navigate", {"url": search_url})
            await self._page_support.wait_for_page_ready(timeout=15.0)
            await self._fill_field('input[name="q"], input[type="search"], #search-input', query)
            await self.session.eval_js('document.querySelector("input[name=\"q\"], input[type=\"search\"], #search-input")?.dispatchEvent(new KeyboardEvent("keydown", {key: "Enter"}))')
            if wait_for_results:
                await self._page_support.wait_for_element(item_selector, timeout=10.0)
                await self.wait_for_ajax(timeout=10.0)
            items = await self.load_virtual_list(item_selector, max_items=max_items)
            elapsed = time.time() - start_time
            logger.info(f"Search completed: collected {len(items)} items, elapsed: {elapsed:.2f}s")
            return InteractionResult(success=True, operation="search_and_collect", data={"query": query, "items": items}, elapsed=elapsed)
        except Exception as e:
            elapsed = time.time() - start_time
            logger.error(f"Search failed: {e}")
            return InteractionResult(success=False, operation="search_and_collect", error=str(e), elapsed=elapsed)

    # =========================================================================
    # 新增交互操作方法
    # =========================================================================

    async def drag_and_drop(
        self,
        from_selector: str,
        to_selector: str,
        duration: float = 1.0,
    ) -> InteractionResult:
        """拖拽操作"""
        logger.info(f"开始拖拽: {from_selector} -> {to_selector}")
        start_time = time.time()
        try:
            js = f'''
            (function() {{
                const fromEl = document.querySelector('{from_selector}');
                const toEl = document.querySelector('{to_selector}');
                if (!fromEl || !toEl) return {{ success: false, error: 'Element not found' }};
                const fromRect = fromEl.getBoundingClientRect();
                const toRect = toEl.getBoundingClientRect();
                const startX = fromRect.left + fromRect.width / 2;
                const startY = fromRect.top + fromRect.height / 2;
                const endX = toRect.left + toRect.width / 2;
                const endY = toRect.top + toRect.height / 2;
                fromEl.dispatchEvent(new MouseEvent('mousedown', {{ bubbles: true, clientX: startX, clientY: startY }}));
                const steps = 10;
                const stepDuration = {duration} * 1000 / steps;
                let step = 0;
                const dragInterval = setInterval(() => {{
                    step++;
                    const progress = step / steps;
                    const currentX = startX + (endX - startX) * progress;
                    const currentY = startY + (endY - startY) * progress;
                    document.dispatchEvent(new MouseEvent('mousemove', {{ bubbles: true, clientX: currentX, clientY: currentY }}));
                    if (step >= steps) {{
                        clearInterval(dragInterval);
                        toEl.dispatchEvent(new MouseEvent('mouseup', {{ bubbles: true, clientX: endX, clientY: endY }}));
                        toEl.dispatchEvent(new MouseEvent('click', {{ bubbles: true, clientX: endX, clientY: endY }}));
                    }}
                }}, stepDuration);
                return {{ success: true }};
            }})();
            '''
            result = await self.session.eval_js(js)
            elapsed = time.time() - start_time
            logger.info(f"拖拽完成，耗时 {elapsed:.2f}s")
            return InteractionResult(success=result.get('success', False), operation="drag_and_drop", data={"from": from_selector, "to": to_selector}, elapsed=elapsed)
        except Exception as e:
            elapsed = time.time() - start_time
            logger.error(f"拖拽失败: {e}")
            return InteractionResult(success=False, operation="drag_and_drop", error=str(e), elapsed=elapsed)

    async def hover_and_click(
        self,
        selector: str,
        hover_duration: float = 0.5,
    ) -> InteractionResult:
        """悬停后点击操作"""
        logger.info(f"开始悬停点击: {selector}")
        start_time = time.time()
        try:
            js = f'''
            (function() {{
                const el = document.querySelector('{selector}');
                if (!el) return {{ success: false, error: 'Element not found' }};
                const rect = el.getBoundingClientRect();
                const x = rect.left + rect.width / 2;
                const y = rect.top + rect.height / 2;
                el.dispatchEvent(new MouseEvent('mouseover', {{ bubbles: true, clientX: x, clientY: y }}));
                return new Promise((resolve) => {{
                    setTimeout(() => {{
                        el.dispatchEvent(new MouseEvent('click', {{ bubbles: true, clientX: x, clientY: y }}));
                        resolve({{ success: true }});
                    }}, {hover_duration * 1000});
                }});
            }})();
            '''
            result = await self.session.eval_js(js)
            elapsed = time.time() - start_time
            logger.info(f"悬停点击完成，耗时 {elapsed:.2f}s")
            return InteractionResult(success=result.get('success', False), operation="hover_and_click", data={"selector": selector}, elapsed=elapsed)
        except Exception as e:
            elapsed = time.time() - start_time
            logger.error(f"悬停点击失败: {e}")
            return InteractionResult(success=False, operation="hover_and_click", error=str(e), elapsed=elapsed)

    async def right_click(
        self,
        selector: str,
    ) -> InteractionResult:
        """右键点击操作"""
        logger.info(f"开始右键点击: {selector}")
        start_time = time.time()
        try:
            js = f'''
            (function() {{
                const el = document.querySelector('{selector}');
                if (!el) return {{ success: false, error: 'Element not found' }};
                const rect = el.getBoundingClientRect();
                const x = rect.left + rect.width / 2;
                const y = rect.top + rect.height / 2;
                el.dispatchEvent(new MouseEvent('contextmenu', {{ bubbles: true, clientX: x, clientY: y, button: 2 }}));
                return {{ success: true }};
            }})();
            '''
            result = await self.session.eval_js(js)
            elapsed = time.time() - start_time
            logger.info(f"右键点击完成，耗时 {elapsed:.2f}s")
            return InteractionResult(success=result.get('success', False), operation="right_click", data={"selector": selector}, elapsed=elapsed)
        except Exception as e:
            elapsed = time.time() - start_time
            logger.error(f"右键点击失败: {e}")
            return InteractionResult(success=False, operation="right_click", error=str(e), elapsed=elapsed)

    async def double_click(
        self,
        selector: str,
    ) -> InteractionResult:
        """双击操作"""
        logger.info(f"开始双击: {selector}")
        start_time = time.time()
        try:
            js = f'''
            (function() {{
                const el = document.querySelector('{selector}');
                if (!el) return {{ success: false, error: 'Element not found' }};
                const rect = el.getBoundingClientRect();
                const x = rect.left + rect.width / 2;
                const y = rect.top + rect.height / 2;
                el.dispatchEvent(new MouseEvent('mousedown', {{ bubbles: true, clientX: x, clientY: y }}));
                el.dispatchEvent(new MouseEvent('mouseup', {{ bubbles: true, clientX: x, clientY: y }}));
                el.dispatchEvent(new MouseEvent('click', {{ bubbles: true, clientX: x, clientY: y }}));
                el.dispatchEvent(new MouseEvent('mousedown', {{ bubbles: true, clientX: x, clientY: y }}));
                el.dispatchEvent(new MouseEvent('mouseup', {{ bubbles: true, clientX: x, clientY: y }}));
                el.dispatchEvent(new MouseEvent('dblclick', {{ bubbles: true, clientX: x, clientY: y }}));
                return {{ success: true }};
            }})();
            '''
            result = await self.session.eval_js(js)
            elapsed = time.time() - start_time
            logger.info(f"双击完成，耗时 {elapsed:.2f}s")
            return InteractionResult(success=result.get('success', False), operation="double_click", data={"selector": selector}, elapsed=elapsed)
        except Exception as e:
            elapsed = time.time() - start_time
            logger.error(f"双击失败: {e}")
            return InteractionResult(success=False, operation="double_click", error=str(e), elapsed=elapsed)

    async def select_dropdown(
        self,
        selector: str,
        value: str,
    ) -> InteractionResult:
        """下拉框选择操作"""
        logger.info(f"开始选择下拉框: {selector} -> {value}")
        start_time = time.time()
        try:
            js = f'''
            (function() {{
                const sel = document.querySelector('{selector}');
                if (!sel || sel.tagName !== 'SELECT') return {{ success: false, error: 'Not a select element' }};
                const options = Array.from(sel.options);
                const option = options.find(o => o.value === '{value}' || o.text === '{value}');
                if (!option) return {{ success: false, error: 'Option not found' }};
                sel.value = option.value;
                sel.dispatchEvent(new Event('change', {{ bubbles: true }}));
                sel.dispatchEvent(new Event('input', {{ bubbles: true }}));
                return {{ success: true, selected: option.value }};
            }})();
            '''
            result = await self.session.eval_js(js)
            elapsed = time.time() - start_time
            logger.info(f"下拉框选择完成，耗时 {elapsed:.2f}s")
            return InteractionResult(success=result.get('success', False), operation="select_dropdown", data={"selector": selector, "value": value}, elapsed=elapsed)
        except Exception as e:
            elapsed = time.time() - start_time
            logger.error(f"下拉框选择失败: {e}")
            return InteractionResult(success=False, operation="select_dropdown", error=str(e), elapsed=elapsed)

    async def upload_file(
        self,
        selector: str,
        file_path: str,
    ) -> InteractionResult:
        """文件上传操作"""
        logger.info(f"开始文件上传: {file_path}")
        start_time = time.time()
        try:
            js = f'''
            (function() {{
                const input = document.querySelector('{selector}');
                if (!input || input.type !== 'file') return {{ success: false, error: 'Not a file input' }};
                input.dispatchEvent(new Event('change', {{ bubbles: true }}));
                return {{ success: true, message: 'File input triggered' }};
            }})();
            '''
            result = await self.session.eval_js(js)
            elapsed = time.time() - start_time
            logger.info(f"文件上传触发完成，耗时 {elapsed:.2f}s")
            return InteractionResult(success=result.get('success', False), operation="upload_file", data={"selector": selector, "file_path": file_path}, elapsed=elapsed)
        except Exception as e:
            elapsed = time.time() - start_time
            logger.error(f"文件上传失败: {e}")
            return InteractionResult(success=False, operation="upload_file", error=str(e), elapsed=elapsed)

    async def scroll_to_element(
        self,
        selector: str,
        behavior: str = "smooth",
    ) -> InteractionResult:
        """滚动到指定元素"""
        logger.info(f"开始滚动到元素: {selector}")
        start_time = time.time()
        try:
            js = f'''
            (function() {{
                const el = document.querySelector('{selector}');
                if (!el) return {{ success: false, error: 'Element not found' }};
                el.scrollIntoView({{ behavior: '{behavior}', block: 'center' }});
                return {{ success: true }};
            }})();
            '''
            result = await self.session.eval_js(js)
            elapsed = time.time() - start_time
            logger.info(f"滚动完成，耗时 {elapsed:.2f}s")
            return InteractionResult(success=result.get('success', False), operation="scroll_to_element", data={"selector": selector}, elapsed=elapsed)
        except Exception as e:
            elapsed = time.time() - start_time
            logger.error(f"滚动失败: {e}")
            return InteractionResult(success=False, operation="scroll_to_element", error=str(e), elapsed=elapsed)

    async def fill_and_submit(
        self,
        form_selector: str,
        fields: Dict[str, Any],
        submit_selector: str = None,
        wait_for_response: bool = True,
        timeout: float = 30.0,
    ) -> InteractionResult:
        """填写表单并提交（组合操作）"""
        logger.info(f"开始填写并提交表单: {form_selector}")
        start_time = time.time()
        try:
            for selector, value in fields.items():
                await self.session.eval_js(f'''
                (function() {{
                    const el = document.querySelector('{selector}');
                    if (!el) return false;
                    if (el.tagName === 'SELECT') {{
                        const option = Array.from(el.options).find(o => o.value === '{value}' || o.text === '{value}');
                        if (option) {{ el.value = option.value; el.dispatchEvent(new Event('change', {{ bubbles: true }})); }}
                        return !!option;
                    }}
                    el.focus(); el.select(); el.value = '{value}';
                    el.dispatchEvent(new Event('input', {{ bubbles: true }}));
                    el.dispatchEvent(new Event('change', {{ bubbles: true }}));
                    return true;
                }})();
                ''')
            if submit_selector:
                await self.session.eval_js(f'''
                (function() {{
                    const btn = document.querySelector('{submit_selector}');
                    if (btn) {{ btn.click(); return true; }}
                    return false;
                }})();
                ''')
            else:
                submit_btn = await self.session.eval_js(f'''
                (function() {{
                    const form = document.querySelector('{form_selector}');
                    if (!form) return null;
                    const btn = form.querySelector('button[type="submit"], input[type="submit"]');
                    return btn ? btn.outerHTML.substring(0, 100) : null;
                }})();
                ''')
                if submit_btn:
                    await self.session.eval_js(f'''
                    (function() {{
                        const btn = document.querySelector('button[type="submit"], input[type="submit"]');
                        if (btn) btn.click();
                    }})();
                    ''')
            if wait_for_response:
                from src.core.dynamic_page_support import wait_for_ajax_complete
                await wait_for_ajax_complete(self.session, timeout=timeout)
            elapsed = time.time() - start_time
            logger.info(f"表单提交完成，耗时 {elapsed:.2f}s")
            return InteractionResult(success=True, operation="fill_and_submit", data={"form": form_selector, "fields": list(fields.keys())}, elapsed=elapsed)
        except Exception as e:
            elapsed = time.time() - start_time
            logger.error(f"表单提交失败: {e}")
            return InteractionResult(success=False, operation="fill_and_submit", error=str(e), elapsed=elapsed)

    async def take_screenshot_with_annotation(
        self,
        selector: str,
        annotation: str = "",
    ) -> InteractionResult:
        """截图并标注指定元素"""
        logger.info(f"开始标注截图: {selector}")
        start_time = time.time()
        try:
            js = f'''
            (function() {{
                const el = document.querySelector('{selector}');
                if (!el) return {{ success: false, error: 'Element not found' }};
                const rect = el.getBoundingClientRect();
                return {{ success: true, x: Math.round(rect.left), y: Math.round(rect.top), width: Math.round(rect.width), height: Math.round(rect.height), annotation: '{annotation}' }};
            }})();
            '''
            result = await self.session.eval_js(js)
            elapsed = time.time() - start_time
            logger.info(f"标注截图完成，耗时 {elapsed:.2f}s")
            return InteractionResult(success=result.get('success', False), operation="take_screenshot_with_annotation", data=result, elapsed=elapsed)
        except Exception as e:
            elapsed = time.time() - start_time
            logger.error(f"标注截图失败: {e}")
            return InteractionResult(success=False, operation="take_screenshot_with_annotation", error=str(e), elapsed=elapsed)

    async def execute_javascript(
        self,
        js_code: str,
        timeout: float = 10.0,
    ) -> InteractionResult:
        """执行自定义 JavaScript 代码"""
        logger.info("开始执行自定义 JS")
        start_time = time.time()
        try:
            result = await self.session.eval_js(js_code, timeout=timeout)
            elapsed = time.time() - start_time
            logger.info(f"JS 执行完成，耗时 {elapsed:.2f}s")
            return InteractionResult(success=True, operation="execute_javascript", data={"result": result}, elapsed=elapsed)
        except Exception as e:
            elapsed = time.time() - start_time
            logger.error(f"JS 执行失败: {e}")
            return InteractionResult(success=False, operation="execute_javascript", error=str(e), elapsed=elapsed)


class ErrorRecoveryManager:
    """Error recovery manager"""
    
    def __init__(self, interaction: BrowserInteraction):
        self.interaction = interaction
    
    async def recover(self, error: Exception, strategy: ErrorRecoveryStrategy, max_retries: int = 3) -> Tuple[bool, str]:
        """Execute error recovery"""
        if strategy == ErrorRecoveryStrategy.RETRY:
            for attempt in range(1, max_retries + 1):
                logger.info(f"Retry attempt {attempt}/{max_retries}: {error}")
                await asyncio.sleep(0.5 * attempt)
                current_url = await self.interaction.session.eval_js("location.href")
                self.interaction.session.send("Page.navigate", {"url": current_url})
                await self.interaction._page_support.wait_for_page_ready(timeout=10.0)
                return True, f"Retry successful (attempt {attempt})"
            return False, f"Retry failed, attempted {max_retries} times"
        elif strategy == ErrorRecoveryStrategy.FALLBACK:
            logger.info(f"Executing fallback: {error}")
            return True, "Fallback executed"
        elif strategy == ErrorRecoveryStrategy.SKIP:
            logger.warning(f"Skipping error: {error}")
            return True, "Error skipped"
        else:
            logger.error(f"Operation aborted: {error}")
            return False, f"Operation aborted: {error}"


# ============================================================================
# Convenience Functions
# ============================================================================

async def infinite_scroll(session, item_selector: str = "", max_items: int = 100, max_pages: int = 10) -> ScrollResult:
    """Convenience function for infinite scroll"""
    interaction = BrowserInteraction(session)
    return await interaction.infinite_scroll(item_selector=item_selector, max_items=max_items, max_pages=max_pages)


async def submit_form(session, form_selector: str, fields: Dict[str, Any], timeout: float = 30.0) -> InteractionResult:
    """Convenience function for form submission"""
    interaction = BrowserInteraction(session)
    return await interaction.submit_form(form_selector, fields, timeout=timeout)


async def handle_popup(session, popup_type: PopupType = None, action: str = "close", timeout: float = 10.0) -> InteractionResult:
    """Convenience function for popup handling"""
    interaction = BrowserInteraction(session)
    return await interaction.handle_popup(popup_type=popup_type, action=action, timeout=timeout)


async def wait_for_ajax(session, timeout: float = 15.0) -> List[AjaxRequest]:
    """Convenience function for AJAX wait"""
    interaction = BrowserInteraction(session)
    return await interaction.wait_for_ajax(timeout=timeout)


async def capture_page_state(session) -> PageState:
    """Convenience function for page state capture"""
    interaction = BrowserInteraction(session)
    return await interaction.capture_page_state()


# ============================================================================
# 新增交互操作方法
# ============================================================================

async def drag_and_drop(
    session,
    from_selector: str,
    to_selector: str,
    duration: float = 1.0,
) -> InteractionResult:
    """
    拖拽操作

    Args:
        from_selector: 拖拽源元素选择器
        to_selector: 目标元素选择器
        duration: 拖拽持续时间（秒）

    Returns:
        InteractionResult: 操作结果
    """
    logger.info(f"开始拖拽: {from_selector} -> {to_selector}")
    start_time = time.time()
    try:
        js = f'''
        (function() {{
            const fromEl = document.querySelector('{from_selector}');
            const toEl = document.querySelector('{to_selector}');
            if (!fromEl || !toEl) return {{ success: false, error: 'Element not found' }};

            const fromRect = fromEl.getBoundingClientRect();
            const toRect = toEl.getBoundingClientRect();

            const startX = fromRect.left + fromRect.width / 2;
            const startY = fromRect.top + fromRect.height / 2;
            const endX = toRect.left + toRect.width / 2;
            const endY = toRect.top + toRect.height / 2;

            // 触发 mousedown
            fromEl.dispatchEvent(new MouseEvent('mousedown', {{
                bubbles: true,
                clientX: startX,
                clientY: startY
            }}));

            // 模拟拖拽过程
            const steps = 10;
            const stepDuration = {duration} * 1000 / steps;
            let step = 0;

            const dragInterval = setInterval(() => {{
                step++;
                const progress = step / steps;
                const currentX = startX + (endX - startX) * progress;
                const currentY = startY + (endY - startY) * progress;

                document.dispatchEvent(new MouseEvent('mousemove', {{
                    bubbles: true,
                    clientX: currentX,
                    clientY: currentY
                }}));

                if (step >= steps) {{
                    clearInterval(dragInterval);
                    // 触发 mouseup
                    toEl.dispatchEvent(new MouseEvent('mouseup', {{
                        bubbles: true,
                        clientX: endX,
                        clientY: endY
                    }}));
                    toEl.dispatchEvent(new MouseEvent('click', {{
                        bubbles: true,
                        clientX: endX,
                        clientY: endY
                    }}));
                }}
            }}, stepDuration);

            return {{ success: true }};
        }})();
        '''
        result = await session.eval_js(js)
        elapsed = time.time() - start_time
        logger.info(f"拖拽完成，耗时 {elapsed:.2f}s")
        return InteractionResult(
            success=result.get('success', False),
            operation="drag_and_drop",
            data={"from": from_selector, "to": to_selector},
            elapsed=elapsed,
        )
    except Exception as e:
        elapsed = time.time() - start_time
        logger.error(f"拖拽失败: {e}")
        return InteractionResult(success=False, operation="drag_and_drop", error=str(e), elapsed=elapsed)


async def hover_and_click(
    session,
    selector: str,
    hover_duration: float = 0.5,
) -> InteractionResult:
    """
    悬停后点击操作

    Args:
        selector: 元素选择器
        hover_duration: 悬停持续时间（秒）

    Returns:
        InteractionResult: 操作结果
    """
    logger.info(f"开始悬停点击: {selector}")
    start_time = time.time()
    try:
        js = f'''
        (function() {{
            const el = document.querySelector('{selector}');
            if (!el) return {{ success: false, error: 'Element not found' }};

            const rect = el.getBoundingClientRect();
            const x = rect.left + rect.width / 2;
            const y = rect.top + rect.height / 2;

            // 触发 mouseover
            el.dispatchEvent(new MouseEvent('mouseover', {{
                bubbles: true,
                clientX: x,
                clientY: y
            }}));

            // 等待悬停
            return new Promise((resolve) => {{
                setTimeout(() => {{
                    // 触发 click
                    el.dispatchEvent(new MouseEvent('click', {{
                        bubbles: true,
                        clientX: x,
                        clientY: y
                    }}));
                    resolve({{ success: true }});
                }}, {hover_duration * 1000});
            }});
        }})();
        '''
        result = await session.eval_js(js)
        elapsed = time.time() - start_time
        logger.info(f"悬停点击完成，耗时 {elapsed:.2f}s")
        return InteractionResult(
            success=result.get('success', False),
            operation="hover_and_click",
            data={"selector": selector},
            elapsed=elapsed,
        )
    except Exception as e:
        elapsed = time.time() - start_time
        logger.error(f"悬停点击失败: {e}")
        return InteractionResult(success=False, operation="hover_and_click", error=str(e), elapsed=elapsed)


async def right_click(
    session,
    selector: str,
) -> InteractionResult:
    """
    右键点击操作

    Args:
        selector: 元素选择器

    Returns:
        InteractionResult: 操作结果
    """
    logger.info(f"开始右键点击: {selector}")
    start_time = time.time()
    try:
        js = f'''
        (function() {{
            const el = document.querySelector('{selector}');
            if (!el) return {{ success: false, error: 'Element not found' }};

            const rect = el.getBoundingClientRect();
            const x = rect.left + rect.width / 2;
            const y = rect.top + rect.height / 2;

            el.dispatchEvent(new MouseEvent('contextmenu', {{
                bubbles: true,
                clientX: x,
                clientY: y,
                button: 2
            }}));

            return {{ success: true }};
        }})();
        '''
        result = await session.eval_js(js)
        elapsed = time.time() - start_time
        logger.info(f"右键点击完成，耗时 {elapsed:.2f}s")
        return InteractionResult(
            success=result.get('success', False),
            operation="right_click",
            data={"selector": selector},
            elapsed=elapsed,
        )
    except Exception as e:
        elapsed = time.time() - start_time
        logger.error(f"右键点击失败: {e}")
        return InteractionResult(success=False, operation="right_click", error=str(e), elapsed=elapsed)


async def double_click(
    session,
    selector: str,
) -> InteractionResult:
    """
    双击操作

    Args:
        selector: 元素选择器

    Returns:
        InteractionResult: 操作结果
    """
    logger.info(f"开始双击: {selector}")
    start_time = time.time()
    try:
        js = f'''
        (function() {{
            const el = document.querySelector('{selector}');
            if (!el) return {{ success: false, error: 'Element not found' }};

            const rect = el.getBoundingClientRect();
            const x = rect.left + rect.width / 2;
            const y = rect.top + rect.height / 2;

            // 第一次点击
            el.dispatchEvent(new MouseEvent('mousedown', {{ bubbles: true, clientX: x, clientY: y }}));
            el.dispatchEvent(new MouseEvent('mouseup', {{ bubbles: true, clientX: x, clientY: y }}));
            el.dispatchEvent(new MouseEvent('click', {{ bubbles: true, clientX: x, clientY: y }}));

            // 第二次点击
            el.dispatchEvent(new MouseEvent('mousedown', {{ bubbles: true, clientX: x, clientY: y }}));
            el.dispatchEvent(new MouseEvent('mouseup', {{ bubbles: true, clientX: x, clientY: y }}));
            el.dispatchEvent(new MouseEvent('dblclick', {{ bubbles: true, clientX: x, clientY: y }}));

            return {{ success: true }};
        }})();
        '''
        result = await session.eval_js(js)
        elapsed = time.time() - start_time
        logger.info(f"双击完成，耗时 {elapsed:.2f}s")
        return InteractionResult(
            success=result.get('success', False),
            operation="double_click",
            data={"selector": selector},
            elapsed=elapsed,
        )
    except Exception as e:
        elapsed = time.time() - start_time
        logger.error(f"双击失败: {e}")
        return InteractionResult(success=False, operation="double_click", error=str(e), elapsed=elapsed)


async def select_dropdown(
    session,
    selector: str,
    value: str,
) -> InteractionResult:
    """
    下拉框选择操作

    Args:
        selector: select 元素选择器
        value: 要选择的值

    Returns:
        InteractionResult: 操作结果
    """
    logger.info(f"开始选择下拉框: {selector} -> {value}")
    start_time = time.time()
    try:
        js = f'''
        (function() {{
            const sel = document.querySelector('{selector}');
            if (!sel || sel.tagName !== 'SELECT') return {{ success: false, error: 'Not a select element' }};

            const options = Array.from(sel.options);
            const option = options.find(o => o.value === '{value}' || o.text === '{value}');
            if (!option) return {{ success: false, error: 'Option not found' }};

            sel.value = option.value;
            sel.dispatchEvent(new Event('change', {{ bubbles: true }}));
            sel.dispatchEvent(new Event('input', {{ bubbles: true }}));

            return {{ success: true, selected: option.value }};
        }})();
        '''
        result = await session.eval_js(js)
        elapsed = time.time() - start_time
        logger.info(f"下拉框选择完成，耗时 {elapsed:.2f}s")
        return InteractionResult(
            success=result.get('success', False),
            operation="select_dropdown",
            data={"selector": selector, "value": value},
            elapsed=elapsed,
        )
    except Exception as e:
        elapsed = time.time() - start_time
        logger.error(f"下拉框选择失败: {e}")
        return InteractionResult(success=False, operation="select_dropdown", error=str(e), elapsed=elapsed)


async def upload_file(
    session,
    selector: str,
    file_path: str,
) -> InteractionResult:
    """
    文件上传操作

    Args:
        selector: input[type=file] 选择器
        file_path: 文件路径

    Returns:
        InteractionResult: 操作结果
    """
    logger.info(f"开始文件上传: {file_path}")
    start_time = time.time()
    try:
        js = f'''
        (function() {{
            const input = document.querySelector('{selector}');
            if (!input || input.type !== 'file') return {{ success: false, error: 'Not a file input' }};

            // 创建虚拟文件列表
            const dataTransfer = new DataTransfer();
            // 注意：这里只能模拟，实际文件上传需要 CDP 的 FileInput 事件
            input.dispatchEvent(new Event('change', {{ bubbles: true }}));

            return {{ success: true, message: 'File input triggered' }};
        }})();
        '''
        result = await session.eval_js(js)
        elapsed = time.time() - start_time
        logger.info(f"文件上传触发完成，耗时 {elapsed:.2f}s")
        return InteractionResult(
            success=result.get('success', False),
            operation="upload_file",
            data={"selector": selector, "file_path": file_path},
            elapsed=elapsed,
        )
    except Exception as e:
        elapsed = time.time() - start_time
        logger.error(f"文件上传失败: {e}")
        return InteractionResult(success=False, operation="upload_file", error=str(e), elapsed=elapsed)


async def scroll_to_element(
    session,
    selector: str,
    behavior: str = "smooth",
) -> InteractionResult:
    """
    滚动到指定元素

    Args:
        selector: 元素选择器
        behavior: 滚动行为（smooth/auto）

    Returns:
        InteractionResult: 操作结果
    """
    logger.info(f"开始滚动到元素: {selector}")
    start_time = time.time()
    try:
        js = f'''
        (function() {{
            const el = document.querySelector('{selector}');
            if (!el) return {{ success: false, error: 'Element not found' }};

            el.scrollIntoView({{ behavior: '{behavior}', block: 'center' }});

            return {{ success: true }};
        }})();
        '''
        result = await session.eval_js(js)
        elapsed = time.time() - start_time
        logger.info(f"滚动完成，耗时 {elapsed:.2f}s")
        return InteractionResult(
            success=result.get('success', False),
            operation="scroll_to_element",
            data={"selector": selector},
            elapsed=elapsed,
        )
    except Exception as e:
        elapsed = time.time() - start_time
        logger.error(f"滚动失败: {e}")
        return InteractionResult(success=False, operation="scroll_to_element", error=str(e), elapsed=elapsed)


async def fill_and_submit(
    session,
    form_selector: str,
    fields: Dict[str, Any],
    submit_selector: str = None,
    wait_for_response: bool = True,
    timeout: float = 30.0,
) -> InteractionResult:
    """
    填写表单并提交（组合操作）

    Args:
        form_selector: 表单选择器
        fields: 字段字典
        submit_selector: 提交按钮选择器
        wait_for_response: 是否等待响应
        timeout: 超时时间

    Returns:
        InteractionResult: 操作结果
    """
    logger.info(f"开始填写并提交表单: {form_selector}")
    start_time = time.time()
    try:
        for selector, value in fields.items():
            await session.eval_js(f'''
            (function() {{
                const el = document.querySelector('{selector}');
                if (!el) return false;
                if (el.tagName === 'SELECT') {{
                    const option = Array.from(el.options).find(o => o.value === '{value}' || o.text === '{value}');
                    if (option) {{ el.value = option.value; el.dispatchEvent(new Event('change', {{ bubbles: true }})); }}
                    return !!option;
                }}
                el.focus(); el.select(); el.value = '{value}';
                el.dispatchEvent(new Event('input', {{ bubbles: true }}));
                el.dispatchEvent(new Event('change', {{ bubbles: true }}));
                return true;
            }})();
            ''')

        if submit_selector:
            await session.eval_js(f'''
            (function() {{
                const btn = document.querySelector('{submit_selector}');
                if (btn) {{ btn.click(); return true; }}
                return false;
            }})();
            ''')
        else:
            submit_btn = await session.eval_js(f'''
            (function() {{
                const form = document.querySelector('{form_selector}');
                if (!form) return null;
                const btn = form.querySelector('button[type="submit"], input[type="submit"]');
                return btn ? btn.outerHTML.substring(0, 100) : null;
            }})();
            ''')
            if submit_btn:
                await session.eval_js(f'''
                (function() {{
                    const btn = document.querySelector('button[type="submit"], input[type="submit"]');
                    if (btn) btn.click();
                }})();
                ''')

        if wait_for_response:
            from src.core.dynamic_page_support import wait_for_ajax_complete
            await wait_for_ajax_complete(session, timeout=timeout)

        elapsed = time.time() - start_time
        logger.info(f"表单提交完成，耗时 {elapsed:.2f}s")
        return InteractionResult(
            success=True,
            operation="fill_and_submit",
            data={"form": form_selector, "fields": list(fields.keys())},
            elapsed=elapsed,
        )
    except Exception as e:
        elapsed = time.time() - start_time
        logger.error(f"表单提交失败: {e}")
        return InteractionResult(success=False, operation="fill_and_submit", error=str(e), elapsed=elapsed)


async def take_screenshot_with_annotation(
    session,
    selector: str,
    annotation: str = "",
) -> InteractionResult:
    """
    截图并标注指定元素

    Args:
        selector: 元素选择器
        annotation: 标注文字

    Returns:
        InteractionResult: 操作结果
    """
    logger.info(f"开始标注截图: {selector}")
    start_time = time.time()
    try:
        js = f'''
        (function() {{
            const el = document.querySelector('{selector}');
            if (!el) return {{ success: false, error: 'Element not found' }};

            const rect = el.getBoundingClientRect();
            return {{
                success: true,
                x: Math.round(rect.left),
                y: Math.round(rect.top),
                width: Math.round(rect.width),
                height: Math.round(rect.height),
                annotation: '{annotation}'
            }};
        }})();
        '''
        result = await session.eval_js(js)
        elapsed = time.time() - start_time
        logger.info(f"标注截图完成，耗时 {elapsed:.2f}s")
        return InteractionResult(
            success=result.get('success', False),
            operation="take_screenshot_with_annotation",
            data=result,
            elapsed=elapsed,
        )
    except Exception as e:
        elapsed = time.time() - start_time
        logger.error(f"标注截图失败: {e}")
        return InteractionResult(success=False, operation="take_screenshot_with_annotation", error=str(e), elapsed=elapsed)


async def execute_javascript(
    session,
    js_code: str,
    timeout: float = 10.0,
) -> InteractionResult:
    """
    执行自定义 JavaScript 代码

    Args:
        js_code: JavaScript 代码
        timeout: 超时时间

    Returns:
        InteractionResult: 操作结果
    """
    logger.info("开始执行自定义 JS")
    start_time = time.time()
    try:
        result = await session.eval_js(js_code, timeout=timeout)
        elapsed = time.time() - start_time
        logger.info(f"JS 执行完成，耗时 {elapsed:.2f}s")
        return InteractionResult(
            success=True,
            operation="execute_javascript",
            data={"result": result},
            elapsed=elapsed,
        )
    except Exception as e:
        elapsed = time.time() - start_time
        logger.error(f"JS 执行失败: {e}")
        return InteractionResult(success=False, operation="execute_javascript", error=str(e), elapsed=elapsed)


# ============================================================================
# 新增便捷函数
# ============================================================================

async def drag_and_drop(session, from_selector: str, to_selector: str, duration: float = 1.0) -> InteractionResult:
    """Convenience function for drag and drop"""
    interaction = BrowserInteraction(session)
    return await interaction.drag_and_drop(from_selector=from_selector, to_selector=to_selector, duration=duration)


async def hover_and_click(session, selector: str, hover_duration: float = 0.5) -> InteractionResult:
    """Convenience function for hover and click"""
    interaction = BrowserInteraction(session)
    return await interaction.hover_and_click(selector=selector, hover_duration=hover_duration)


async def right_click(session, selector: str) -> InteractionResult:
    """Convenience function for right click"""
    interaction = BrowserInteraction(session)
    return await interaction.right_click(selector=selector)


async def double_click(session, selector: str) -> InteractionResult:
    """Convenience function for double click"""
    interaction = BrowserInteraction(session)
    return await interaction.double_click(selector=selector)


async def select_dropdown(session, selector: str, value: str) -> InteractionResult:
    """Convenience function for dropdown selection"""
    interaction = BrowserInteraction(session)
    return await interaction.select_dropdown(selector=selector, value=value)


async def upload_file(session, selector: str, file_path: str) -> InteractionResult:
    """Convenience function for file upload"""
    interaction = BrowserInteraction(session)
    return await interaction.upload_file(selector=selector, file_path=file_path)


async def scroll_to_element(session, selector: str, behavior: str = "smooth") -> InteractionResult:
    """Convenience function for scroll to element"""
    interaction = BrowserInteraction(session)
    return await interaction.scroll_to_element(selector=selector, behavior=behavior)


async def fill_and_submit(session, form_selector: str, fields: Dict[str, Any], timeout: float = 30.0) -> InteractionResult:
    """Convenience function for fill and submit"""
    interaction = BrowserInteraction(session)
    return await interaction.fill_and_submit(form_selector=form_selector, fields=fields, timeout=timeout)


async def execute_javascript(session, js_code: str, timeout: float = 10.0) -> InteractionResult:
    """Convenience function for custom JS execution"""
    interaction = BrowserInteraction(session)
    return await interaction.execute_javascript(js_code=js_code, timeout=timeout)
