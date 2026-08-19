"""
wait_strategy.py - 鍔ㄦ€侀〉闈㈡覆鏌撶瓑寰呯瓥鐣?

鎻愪緵JavaScript娓叉煋瀹屾垚妫€娴嬨€佹粴鍔ㄥ姞杞借瘑鍒拰寮圭獥鎷︽埅鍔熻兘锛?
鐢ㄤ簬SPA缃戠珯鐨勫彲闈犳姄鍙栥€?
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class RenderStatus(Enum):
    """娓叉煋鐘舵€佹灇涓?""
    LOADING = "loading"
    IDLE = "idle"
    COMPLETE = "complete"
    TIMEOUT = "timeout"
    ERROR = "error"


class ScrollState(Enum):
    """婊氬姩鐘舵€佹灇涓?""
    STABLE = "stable"
    LOADING = "loading"
    END_REACHED = "end_reached"
    ERROR = "error"


class PopupType(Enum):
    """寮圭獥绫诲瀷鏋氫妇"""
    COOKIE_BANNER = "cookie_banner"
    SUBSCRIPTION = "subscription"
    CAPTCHA = "captcha"
    MODAL = "modal"
    ADVERTISING = "advertising"
    ALERT = "alert"
    UNKNOWN = "unknown"


@dataclass
class WaitForResult:
    """绛夊緟缁撴灉"""
    status: RenderStatus
    wait_time: float = 0.0
    items_count: int = 0
    url: str = ""
    error: Optional[str] = None
    details: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status.value,
            "wait_time": self.wait_time,
            "items_count": self.items_count,
            "url": self.url,
            "error": self.error,
            "details": self.details,
        }


@dataclass
class ScrollResult:
    """婊氬姩鍔犺浇缁撴灉"""
    state: ScrollState
    pages_scrolled: int = 0
    total_items: int = 0
    new_items_per_page: List[int] = field(default_factory=list)
    error: Optional[str] = None


class JSRenderDetector:
    """JavaScript娓叉煋瀹屾垚妫€娴嬪櫒"""
    
    DETECTION_SCRIPTS = {
        "ready_state": "document.readyState",
        "ajax_count": "window._pendingAjax || 0",
        "dom_changes": "window._mutationCount || 0",
        "fonts_loaded": "document.fonts?.ready?.then(() => true).catch(() => false)",
        "resources_complete": """
            (() => {
                const perf = performance.getEntriesByType('resource');
                const images = document.querySelectorAll('img');
                const loaded = Array.from(images).filter(img => img.complete).length;
                return { total: images.length, loaded: loaded };
            })()
        """,
    }
    
    def __init__(self, session: Any):
        self._session = session
        self._mutation_observer_installed = False
        self._mutation_count = 0
        self._ajax_pending = 0
    
    async def wait_for_ready_state(self, target_state: str = "complete", timeout: float = 30.0, check_interval: float = 0.5) -> WaitForResult:
        start_time = time.time()
        while time.time() - start_time < timeout:
            try:
                state = await self._session.eval_js(self.DETECTION_SCRIPTS["ready_state"])
                if state == target_state:
                    return WaitForResult(status=RenderStatus.COMPLETE, wait_time=time.time() - start_time, url=await self._get_current_url(), details={"readyState": state})
            except Exception as e:
                logger.debug(f"绛夊緟readyState鏃跺嚭閿? {e}")
            await asyncio.sleep(check_interval)
        return WaitForResult(status=RenderStatus.TIMEOUT, wait_time=time.time() - start_time, error=f"绛夊緟{target_state}鐘舵€佽秴鏃?{timeout}s)")
    
    async def wait_for_ajax_idle(self, timeout: float = 10.0, idle_threshold: int = 3, check_interval: float = 0.3) -> WaitForResult:
        start_time = time.time()
        idle_count = 0
        while time.time() - start_time < timeout:
            try:
                pending = await self._session.eval_js("window._ajaxPending || 0")
                if pending == 0:
                    idle_count += 1
                    if idle_count >= idle_threshold:
                        return WaitForResult(status=RenderStatus.IDLE, wait_time=time.time() - start_time, details={"ajax_pending": 0, "idle_checks": idle_count})
                else:
                    idle_count = 0
            except Exception:
                idle_count = 0
            await asyncio.sleep(check_interval)
        return WaitForResult(status=RenderStatus.TIMEOUT, wait_time=time.time() - start_time, error="AJAX璇锋眰瓒呮椂")
    
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
                logger.debug(f"绛夊緟閫夋嫨鍣?'{selector}' 鏃跺嚭閿? {e}")
            await asyncio.sleep(check_interval)
        return WaitForResult(status=RenderStatus.TIMEOUT, wait_time=time.time() - start_time, error=f"绛夊緟閫夋嫨鍣?'{selector}' 瓒呮椂")
    
    async def wait_for_network_idle(self, timeout: float = 10.0, idle_threshold: int = 5, check_interval: float = 0.2) -> WaitForResult:
        start_time = time.time()
        idle_count = 0
        while time.time() - start_time < timeout:
            try:
                network_status = await self._session.eval_js("""(() => { const resources = performance.getEntriesByType('resource'); const recent = resources.filter(r => (performance.now() - r.startTime) < 1000); return { active: recent.length, total: resources.length }; })()""")
                active_requests = network_status.get('active', 0)
                if active_requests == 0:
                    idle_count += 1
                    if idle_count >= idle_threshold:
                        return WaitForResult(status=RenderStatus.IDLE, wait_time=time.time() - start_time, details={"active_requests": 0, "idle_checks": idle_count})
                else:
                    idle_count = 0
            except Exception as e:
                logger.debug(f"妫€鏌ョ綉缁滅姸鎬佹椂鍑洪敊: {e}")
            await asyncio.sleep(check_interval)
        return WaitForResult(status=RenderStatus.TIMEOUT, wait_time=time.time() - start_time, error="缃戠粶绌洪棽妫€娴嬭秴鏃?)
    
    async def wait_for_images_loaded(self, timeout: float = 30.0, check_interval: float = 0.5, min_load_percentage: float = 0.9) -> WaitForResult:
        start_time = time.time()
        while time.time() - start_time < timeout:
            try:
                result = await self._session.eval_js("""(() => { const images = document.querySelectorAll('img'); if (images.length === 0) return { loaded: 1, total: 1, percentage: 1 }; let loaded = 0; images.forEach(img => { if (img.complete && img.naturalWidth !== 0) loaded++; }); return { loaded: loaded, total: images.length, percentage: loaded / images.length }; })()""")
                percentage = result.get('percentage', 0)
                if percentage >= min_load_percentage:
                    return WaitForResult(status=RenderStatus.COMPLETE, wait_time=time.time() - start_time, details={"images_loaded": result.get('loaded', 0), "load_percentage": percentage})
            except Exception as e:
                logger.debug(f"妫€鏌ュ浘鐗囧姞杞界姸鎬佹椂鍑洪敊: {e}")
            await asyncio.sleep(check_interval)
        return WaitForResult(status=RenderStatus.TIMEOUT, wait_time=time.time() - start_time, error="鍥剧墖鍔犺浇瓒呮椂")
    
    async def _get_current_url(self) -> str:
        try:
            return await self._session.eval_js("window.location.href")
        except:
            return ""
    
    def install_mutation_observer(self) -> bool:
        if self._mutation_observer_installed:
            return True
        script = """(() => { if (window._mutationObserver) return true; window._mutationCount = 0; window._mutationObserver = new MutationObserver((mutations) => { window._mutationCount += mutations.length; }); window._mutationObserver.observe(document.body, { childList: true, subtree: true, attributes: true, characterData: true }); return true; })()"""
        try:
            result = self._session.eval_js(script)
            self._mutation_observer_installed = bool(result)
            return self._mutation_observer_installed
        except Exception as e:
            logger.error(f"瀹夎MutationObserver澶辫触: {e}")
            return False
    
    def uninstall_mutation_observer(self) -> bool:
        if not self._mutation_observer_installed:
            return True
        try:
            self._session.eval_js("(() => { if (window._mutationObserver) { window._mutationObserver.disconnect(); window._mutationObserver = null; } return true; })()")
            self._mutation_observer_installed = False
            return True
        except Exception as e:
            logger.error(f"鍗歌浇MutationObserver澶辫触: {e}")
            return False
    
    def get_mutation_count(self) -> int:
        try:
            return self._session.eval_js("window._mutationCount || 0")
        except:
            return 0


class ScrollLoadMonitor:
    """婊氬姩鍔犺浇鐩戞帶鍣?- 妫€娴嬮〉闈㈡粴鍔ㄦ椂鐨勫姩鎬佸唴瀹瑰姞杞?""
    
    def __init__(self, session: Any):
        self._session = session
        self._scroll_callbacks: List[Callable] = []
        self._content_hashes: List[str] = []
        self._load_count = 0
    
    def on_content_loaded(self, callback: Callable[[int, int], None]) -> None:
        """娉ㄥ唽鍐呭鍔犺浇鍥炶皟 (page_num, total_items)"""
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
                    logger.warning(f"婊氬姩鍥炶皟鎵ц澶辫触: {e}")
            
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
            error = f"杈惧埌鏈€澶ф粴鍔ㄩ〉鏁伴檺鍒?{max_pages})"
        else:
            state = ScrollState.STABLE
            error = None
        
        return ScrollResult(state=state, pages_scrolled=pages_scrolled, total_items=prev_item_count, new_items_per_page=new_items_per_page, error=error)
    
    async def detect_infinite_scroll(self, container_selector: Optional[str] = None, item_selector: Optional[str] = None, max_loads: int = 5) -> Dict[str, Any]:
        initial_height = await self._get_page_height()
        initial_items = await self._count_items(item_selector) if item_selector else 0
        
        for i in range(max_loads):
            await self._scroll_by(500)
            await asyncio.sleep(0.5)
            current_height = await self._get_page_height()
            current_items = await self._count_items(item_selector) if item_selector else 0
            height_change = current_height - initial_height
            items_change = current_items - initial_items
            
            if height_change < 50 and items_change < 1:
                return {"is_infinite_scroll": False, "loads_detected": i, "height_change": height_change, "items_change": items_change}
        
        return {"is_infinite_scroll": True, "loads_detected": max_loads, "initial_height": initial_height, "initial_items": initial_items}
    
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
    """寮圭獥鎷︽埅鍣?- 妫€娴嬪苟澶勭悊甯歌寮圭獥"""
    
    POPUP_SELECTORS = {
        PopupType.COOKIE_BANNER: ['[class*="cookie"]', '[class*="consent"]', '[id*="cookie"]', '.cookie-banner', '.cookie-popup', '#onetrust-accept-btn-handler'],
        PopupType.SUBSCRIPTION: ['[class*="subscribe"]', '[class*="newsletter"]', '.subscription-popup'],
        PopupType.CAPTCHA: ['.g-recaptcha', '.hcaptcha', '[class*="captcha"]', '.geetest'],
        PopupType.MODAL: ['.modal', '[role="dialog"]', '[class*="popup"]', '[class*="overlay"]'],
        PopupType.ADVERTISING: ['[class*="ad"]', '.ad-banner', '.advert', '.popunder'],
    }
    
    CLOSE_SELECTORS = ['[class*="close"]', '[class*="dismiss"]', '[class*="cancel"]', '[class*="accept"]', '[class*="agree"]', '.close', '.dismiss']
    
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
                logger.warning(f"鍏抽棴寮圭獥澶辫触 ({target_selector}): {e}")
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
