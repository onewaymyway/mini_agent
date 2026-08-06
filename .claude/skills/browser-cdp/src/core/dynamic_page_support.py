"""
dynamic_page_support.py - 动态页面支持模块

整合以下核心能力：
1. 元素加载等待（SmartWait）
2. 滚动触发懒加载（EnhancedDynamicLoader）
3. SPA 路由变化监听（SPADetector）
4. 懒加载图片等待（EnhancedDynamicLoader）
5. DOM 变化监听（DOMObserver）

用法：
    from src.core.dynamic_page_support import DynamicPageSupport
    
    support = DynamicPageSupport(session)
    
    # 等待元素出现
    await support.wait_for_element("#result", timeout=10)
    
    # 滚动加载内容
    await support.scroll_to_load(".item", max_items=100)
    
    # 等待 SPA 路由稳定
    await support.wait_for_spa_route(timeout=15)
    
    # 等待懒加载图片
    loaded = await support.wait_for_lazy_images(timeout=10)
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any, Callable

from src.core.smart_wait import SmartWait, WaitConfig
from src.core.enhanced_dynamic_loader import EnhancedDynamicLoader, ScrollConfig, ScrollResult
from src.core.spa_detector import SPADetector, SPAInfo
from src.core.dom_observer import DOMObserver

logger = logging.getLogger(__name__)


@dataclass
class DynamicPageResult:
    """动态页面操作结果"""
    success: bool
    operation: str
    data: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    elapsed: float = 0.0

    def to_dict(self) -> dict:
        result = {
            "success": self.success,
            "operation": self.operation,
            "elapsed": round(self.elapsed, 2),
        }
        if self.error:
            result["error"] = self.error
        if self.data:
            result["data"] = self.data
        return result


class DynamicPageSupport:
    """
    动态页面支持类
    
    整合智能等待、滚动加载、SPA路由监听、懒加载等待等能力
    """
    
    def __init__(self, session):
        self.session = session
        self._smart_wait = SmartWait(session)
        self._dynamic_loader = EnhancedDynamicLoader(session)
        self._spa_detector = SPADetector(session)
        self._dom_observer = DOMObserver(session)
    
    # =========================================================================
    # 元素加载等待
    # =========================================================================
    
    async def wait_for_element(
        self,
        selector: str,
        timeout: float = 10.0,
        visible: bool = True,
        strategy: str = "adaptive",
    ) -> bool:
        """
        等待元素出现
        
        Args:
            selector: CSS 选择器
            timeout: 超时时间（秒）
            visible: 是否等待可见
            strategy: 等待策略（adaptive/selector/networkidle/stable）
        
        Returns:
            bool: 是否成功等待
        """
        logger.info(f"等待元素出现: {selector}，策略: {strategy}")
        start = time.time()
        
        if strategy == "adaptive":
            # 自适应等待：先等网络空闲，再等元素
            await self._smart_wait.wait_for("networkidle", timeout=timeout * 0.5)
            result = await self._smart_wait.wait_for_selector(selector, timeout=timeout * 0.5, visible=visible)
        else:
            result = await self._smart_wait.wait_for(strategy, timeout=timeout, selector=selector, visible=visible)
        
        elapsed = time.time() - start
        logger.info(f"元素等待完成: {selector}，耗时 {elapsed:.2f}s，成功: {result.success}")
        return result.success
    
    async def wait_for_elements(
        self,
        selectors: List[str],
        timeout: float = 15.0,
        all_required: bool = True,
    ) -> Dict[str, bool]:
        """
        等待多个元素出现
        
        Args:
            selectors: CSS 选择器列表
            timeout: 超时时间
            all_required: 是否所有元素都必须出现
        
        Returns:
            Dict[str, bool]: 每个选择器的等待结果
        """
        logger.info(f"等待多个元素: {selectors}")
        results = {}
        
        for selector in selectors:
            results[selector] = await self.wait_for_element(selector, timeout=timeout)
            if not all_required and not results[selector]:
                logger.warning(f"元素未出现（非必需）: {selector}")
                continue
            if all_required and not results[selector]:
                logger.error(f"必需元素未出现: {selector}")
                break
        
        return results
    
    # =========================================================================
    # 滚动触发懒加载
    # =========================================================================
    
    async def scroll_to_load(
        self,
        item_selector: str = "",
        max_pages: int = 10,
        max_items: int = 100,
        scroll_distance: int = 800,
        scroll_delay: float = 0.8,
        stop_condition: Callable = None,
    ) -> ScrollResult:
        """
        滚动加载内容
        
        Args:
            item_selector: 列表项选择器
            max_pages: 最大滚动页数
            max_items: 最大收集项数
            scroll_distance: 每次滚动距离
            scroll_delay: 滚动间隔（秒）
            stop_condition: 停止条件函数
        
        Returns:
            ScrollResult: 滚动结果
        """
        logger.info(f"开始滚动加载，最大页数: {max_pages}，最大项数: {max_items}")
        
        config = ScrollConfig(
            item_selector=item_selector,
            max_pages=max_pages,
            scroll_distance=scroll_distance,
            scroll_delay=scroll_delay,
        )
        loader = EnhancedDynamicLoader(self.session, config)
        
        result = await loader.smart_scroll(
            max_pages=max_pages,
            stop_condition=stop_condition,
        )
        
        logger.info(f"滚动加载完成: {result.pages_loaded} 页，{result.items_found} 项")
        return result
    
    async def load_virtual_list(
        self,
        item_selector: str,
        max_items: int = 100,
        scroll_distance: int = 500,
    ) -> List[Dict[str, Any]]:
        """
        加载虚拟列表数据
        
        Args:
            item_selector: 列表项选择器
            max_items: 最大收集项数
            scroll_distance: 每次滚动距离
        
        Returns:
            List[Dict]: 收集的数据列表
        """
        logger.info(f"开始加载虚拟列表，最大项数: {max_items}")
        
        config = ScrollConfig(item_selector=item_selector)
        loader = EnhancedDynamicLoader(self.session, config)
        
        items = await loader.load_virtual_list(
            item_selector=item_selector,
            max_items=max_items,
            scroll_distance=scroll_distance,
        )
        
        logger.info(f"虚拟列表加载完成: {len(items)} 项")
        return items
    
    # =========================================================================
    # SPA 路由变化监听
    # =========================================================================
    
    async def wait_for_spa_route(
        self,
        timeout: float = 15.0,
        expected_url: str = None,
    ) -> bool:
        """
        等待 SPA 路由稳定
        
        Args:
            timeout: 超时时间
            expected_url: 期望的 URL 模式
        
        Returns:
            bool: 是否成功等待
        """
        logger.info(f"等待 SPA 路由稳定，超时: {timeout}s")
        
        # 先检测 SPA 框架
        spa_info = await self._spa_detector.detect()
        logger.info(f"检测到 SPA 框架: {spa_info.framework.value}")
        
        # 等待路由稳定
        result = await self._smart_wait.wait_for(
            "route",
            timeout=timeout,
            expected_url=expected_url,
        )
        
        logger.info(f"SPA 路由等待完成: {result.success}")
        return result.success
    
    async def detect_spa(self) -> SPAInfo:
        """
        检测 SPA 框架
        
        Returns:
            SPAInfo: SPA 框架信息
        """
        logger.info("检测 SPA 框架")
        info = await self._spa_detector.detect()
        logger.info(f"SPA 框架检测完成: {info.framework.value} v{info.version}")
        return info
    
    # =========================================================================
    # 懒加载图片等待
    # =========================================================================
    
    async def wait_for_lazy_images(
        self,
        selector: str = "img[loading='lazy'], [data-src], [data-lazy]",
        timeout: float = 10.0,
    ) -> int:
        """
        等待懒加载图片完成
        
        Args:
            selector: 懒加载图片选择器
            timeout: 超时时间
        
        Returns:
            int: 已加载的图片数量
        """
        logger.info(f"等待懒加载图片完成，选择器: {selector}")
        
        config = ScrollConfig()
        loader = EnhancedDynamicLoader(self.session, config)
        
        loaded = await loader.wait_for_lazy_images(selector=selector, timeout=timeout)
        
        logger.info(f"懒加载图片等待完成: {loaded} 张已加载")
        return loaded
    
    # =========================================================================
    # DOM 变化监听
    # =========================================================================
    
    async def wait_for_dom_stable(
        self,
        check_interval: float = 0.5,
        stable_count: int = 3,
        timeout: float = 30.0,
    ) -> bool:
        """
        等待 DOM 稳定
        
        Args:
            check_interval: 检查间隔
            stable_count: 连续稳定次数
            timeout: 超时时间
        
        Returns:
            bool: 是否稳定
        """
        logger.info(f"等待 DOM 稳定，超时: {timeout}s")
        
        await self._dom_observer.observe()
        try:
            result = await self._dom_observer.wait_for_stable(
                check_interval=check_interval,
                stable_count=stable_count,
                timeout=timeout,
            )
            logger.info(f"DOM 稳定检测完成: {result}")
            return result
        finally:
            await self._dom_observer.stop()
    
    async def wait_for_content_change(
        self,
        selector: str = "body",
        min_changes: int = 1,
        timeout: float = 15.0,
    ) -> bool:
        """
        等待内容变化
        
        Args:
            selector: 监听的元素
            min_changes: 最小变化次数
            timeout: 超时时间
        
        Returns:
            bool: 是否发生变化
        """
        logger.info(f"等待内容变化: {selector}")
        
        await self._dom_observer.observe(selector=selector)
        try:
            result = await self._dom_observer.wait_for_content_change(
                selector=selector,
                min_changes=min_changes,
                timeout=timeout,
            )
            logger.info(f"内容变化检测完成: {result}")
            return result
        finally:
            await self._dom_observer.stop()
    
    # =========================================================================
    # 组合操作
    # =========================================================================
    
    async def wait_for_page_ready(
        self,
        selector: str = None,
        wait_network_idle: bool = True,
        wait_content_stable: bool = True,
        timeout: float = 30.0,
    ) -> bool:
        """
        等待页面完全就绪（组合操作）
        
        按顺序执行：
        1. 等待网络空闲
        2. 等待指定选择器出现
        3. 等待内容稳定
        
        Args:
            selector: 关键元素选择器
            wait_network_idle: 是否等待网络空闲
            wait_content_stable: 是否等待内容稳定
            timeout: 总超时时间
        
        Returns:
            bool: 是否就绪
        """
        logger.info("等待页面完全就绪")
        start = time.time()
        
        # 1. 等待网络空闲
        if wait_network_idle:
            network_result = await self._smart_wait.wait_for(
                "networkidle",
                timeout=timeout * 0.4,
            )
            logger.info(f"网络空闲等待: {network_result.success}")
            if not network_result.success:
                logger.warning("网络空闲等待超时，继续后续检查")
        
        # 2. 等待选择器
        if selector:
            selector_result = await self._smart_wait.wait_for_selector(
                selector,
                timeout=timeout * 0.3,
            )
            logger.info(f"选择器等待: {selector_result.success}")
            if not selector_result.success:
                logger.warning(f"选择器 {selector} 未出现")
        
        # 3. 等待内容稳定
        if wait_content_stable:
            stable_result = await self._smart_wait.wait_for(
                "stable",
                timeout=timeout * 0.3,
            )
            logger.info(f"内容稳定等待: {stable_result.success}")
        
        elapsed = time.time() - start
        all_success = (not wait_network_idle or network_result.success) and \
                      (not selector or selector_result.success) and \
                      (not wait_content_stable or stable_result.success)
        
        logger.info(f"页面就绪检查完成，耗时 {elapsed:.2f}s，成功: {all_success}")
        return all_success
    
    async def scroll_and_collect(
        self,
        item_selector: str,
        max_items: int = 100,
        max_pages: int = 10,
        wait_after_scroll: float = 0.5,
    ) -> List[Dict[str, Any]]:
        """
        滚动并收集内容（组合操作）
        
        Args:
            item_selector: 列表项选择器
            max_items: 最大收集项数
            max_pages: 最大滚动页数
            wait_after_scroll: 滚动后等待时间
        
        Returns:
            List[Dict]: 收集的数据列表
        """
        logger.info(f"开始滚动收集，最大项数: {max_items}")
        
        all_items = []
        seen_keys = set()
        
        config = ScrollConfig(item_selector=item_selector)
        loader = EnhancedDynamicLoader(self.session, config)
        
        # 智能滚动
        scroll_result = await loader.smart_scroll(
            max_pages=max_pages,
            callback=lambda pages, items: logger.info(f"已滚动 {pages} 页，收集 {items} 项"),
        )
        
        # 等待懒加载图片
        await self.wait_for_lazy_images(timeout=5.0)
        
        # 收集最终内容
        items = await loader._collect_visible_items(item_selector)
        new_items = loader._deduplicate_items(items, seen_keys)
        all_items.extend(new_items)
        
        logger.info(f"滚动收集完成: {len(all_items)} 项")
        return all_items


# ============================================================================
# 便捷函数
# ============================================================================

async def wait_for_element(
    session,
    selector: str,
    timeout: float = 10.0,
    visible: bool = True,
) -> bool:
    """等待元素出现的便捷函数"""
    support = DynamicPageSupport(session)
    return await support.wait_for_element(selector, timeout=timeout, visible=visible)


async def scroll_to_load(
    session,
    item_selector: str = "",
    max_pages: int = 10,
    max_items: int = 100,
) -> ScrollResult:
    """滚动加载内容的便捷函数"""
    support = DynamicPageSupport(session)
    return await support.scroll_to_load(
        item_selector=item_selector,
        max_pages=max_pages,
        max_items=max_items,
    )


async def wait_for_spa_route(
    session,
    timeout: float = 15.0,
    expected_url: str = None,
) -> bool:
    """等待 SPA 路由稳定的便捷函数"""
    support = DynamicPageSupport(session)
    return await support.wait_for_spa_route(timeout=timeout, expected_url=expected_url)


async def wait_for_lazy_images(
    session,
    selector: str = "img[loading='lazy'], [data-src], [data-lazy]",
    timeout: float = 10.0,
) -> int:
    """等待懒加载图片完成的便捷函数"""
    support = DynamicPageSupport(session)
    return await support.wait_for_lazy_images(selector=selector, timeout=timeout)


async def wait_for_page_ready(
    session,
    selector: str = None,
    timeout: float = 30.0,
) -> bool:
    """等待页面完全就绪的便捷函数"""
    support = DynamicPageSupport(session)
    return await support.wait_for_page_ready(selector=selector, timeout=timeout)


# ============================================================================
# 新增动态场景处理方法
# ============================================================================

async def wait_for_ajax_complete(
    session,
    timeout: float = 10.0,
) -> bool:
    """
    等待所有 AJAX 请求完成

    Args:
        timeout: 超时时间（秒）

    Returns:
        bool: 是否所有 AJAX 请求已完成
    """
    from src.core.smart_wait import SmartWait
    smart_wait = SmartWait(session)
    result = await smart_wait.wait_for("ajaxidle", timeout=timeout)
    logger.info(f"AJAX 等待完成: {result.success}")
    return result.success


async def wait_for_animation_complete(
    session,
    selector: str = "*",
    timeout: float = 5.0,
) -> bool:
    """
    等待 CSS 动画/过渡完成

    Args:
        selector: 元素选择器
        timeout: 超时时间（秒）

    Returns:
        bool: 动画是否已完成
    """
    logger.info(f"等待动画完成: {selector}")
    js_code = f'''
    (function() {{
        const elements = document.querySelectorAll('{selector}');
        let hasAnimation = false;
        elements.forEach(el => {{
            const style = window.getComputedStyle(el);
            if (style.animationName !== 'none' && style.animationPlayState === 'running') {{
                hasAnimation = true;
            }}
            if (style.transitionProperty !== 'none' && style.transitionDuration !== '0s') {{
                hasAnimation = true;
            }}
        }});
        return !hasAnimation;
    }})();
    '''
    result = await session.evaluate(js_code, timeout=timeout)
    logger.info(f"动画等待完成: {result}")
    return bool(result)


async def wait_for_iframe_loaded(
    session,
    selector: str = "iframe",
    timeout: float = 15.0,
) -> bool:
    """
    等待 iframe 内容加载完成

    Args:
        selector: iframe 选择器
        timeout: 超时时间（秒）

    Returns:
        bool: iframe 是否已加载完成
    """
    logger.info(f"等待 iframe 加载: {selector}")
    js_code = f'''
    (function() {{
        const iframes = document.querySelectorAll('{selector}');
        if (iframes.length === 0) return true;
        let allLoaded = true;
        iframes.forEach(iframe => {{
            if (iframe.contentDocument && iframe.contentDocument.readyState === 'complete') {{
                return;
            }}
            allLoaded = false;
        }});
        return allLoaded;
    }})();
    '''
    from src.core.smart_wait import SmartWait
    smart_wait = SmartWait(session)
    result = await smart_wait.wait_for_js(js_code, timeout=timeout)
    logger.info(f"iframe 等待完成: {result}")
    return result


async def wait_for_websocket_ready(
    session,
    timeout: float = 10.0,
) -> bool:
    """
    等待 WebSocket 连接就绪

    Args:
        timeout: 超时时间（秒）

    Returns:
        bool: WebSocket 是否已就绪
    """
    logger.info("等待 WebSocket 连接就绪")
    js_code = '''
    (function() {
        // 检查是否有活跃的 WebSocket 连接
        const ws = window.__browser_cdp_ws;
        if (!ws) return true;
        return ws.readyState === WebSocket.OPEN;
    })();
    '''
    from src.core.smart_wait import SmartWait
    smart_wait = SmartWait(session)
    result = await smart_wait.wait_for_js(js_code, timeout=timeout)
    logger.info(f"WebSocket 等待完成: {result}")
    return result


async def wait_for_shadow_dom(
    session,
    selector: str,
    timeout: float = 10.0,
) -> bool:
    """
    等待 Shadow DOM 附加完成

    Args:
        selector: 宿主元素选择器
        timeout: 超时时间（秒）

    Returns:
        bool: Shadow DOM 是否已附加
    """
    logger.info(f"等待 Shadow DOM: {selector}")
    js_code = f'''
    (function() {{
        const el = document.querySelector('{selector}');
        if (!el) return false;
        return !!el.shadowRoot;
    }})();
    '''
    from src.core.smart_wait import SmartWait
    smart_wait = SmartWait(session)
    result = await smart_wait.wait_for_js(js_code, timeout=timeout)
    logger.info(f"Shadow DOM 等待完成: {result}")
    return result


async def wait_for_cookie_consent(
    session,
    timeout: float = 10.0,
    auto_accept: bool = True,
) -> bool:
    """
    等待并处理 Cookie 同意弹窗

    Args:
        timeout: 超时时间（秒）
        auto_accept: 是否自动接受

    Returns:
        bool: Cookie 弹窗是否已处理
    """
    logger.info("等待 Cookie 同意弹窗")
    js_code = '''
    (function() {
        // 常见 Cookie 弹窗选择器
        const selectors = [
            '#cookie-banner', '.cookie-banner', '.cookie-consent',
            '.cookie-popup', '#cookie-popup', '.cc-banner',
            '.gdpr-banner', '.consent-banner', '[role="dialog"]'
        ];
        for (const sel of selectors) {
            const el = document.querySelector(sel);
            if (el && el.offsetParent !== null) {
                return { found: true, text: el.textContent.substring(0, 100) };
            }
        }
        return { found: false };
    })();
    '''
    from src.core.smart_wait import SmartWait
    smart_wait = SmartWait(session)
    result = await smart_wait.wait_for_js(js_code, timeout=timeout)

    if result and result.get("found") and auto_accept:
        # 尝试点击接受按钮
        accept_selectors = [
            '#cookie-accept', '.cookie-accept', '.accept-cookies',
            '#accept-cookies', '.cc-accept', '[data-testid="accept-cookies"]',
            'button:has-text("接受")', 'button:has-text("Accept")',
            'button:has-text("同意")'
        ]
        for sel in accept_selectors:
            try:
                await session.click(sel, timeout=2.0)
                logger.info(f"已点击 Cookie 接受按钮: {sel}")
                return True
            except Exception:
                continue
        return True  # 弹窗存在但无法自动接受

    logger.info("Cookie 弹窗等待完成")
    return True


async def wait_for_loading_spinner(
    session,
    timeout: float = 10.0,
) -> bool:
    """
    等待加载转圈消失

    Args:
        timeout: 超时时间（秒）

    Returns:
        bool: 加载转圈是否已消失
    """
    logger.info("等待加载转圈消失")
    js_code = '''
    (function() {
        const selectors = [
            '.spinner', '.loader', '.loading', '.skeleton',
            '[class*="spinner"]', '[class*="loading"]',
            '[class*="skeleton"]', '.ajax-loader'
        ];
        for (const sel of selectors) {
            const el = document.querySelector(sel);
            if (el && el.offsetParent !== null) {
                return false;
            }
        }
        return true;
    })();
    '''
    from src.core.smart_wait import SmartWait
    smart_wait = SmartWait(session)
    result = await smart_wait.wait_for_js(js_code, timeout=timeout)
    logger.info(f"加载转圈等待完成: {result}")
    return result


async def wait_for_skeleton_screen(
    session,
    timeout: float = 10.0,
) -> bool:
    """
    等待骨架屏渲染完成

    Args:
        timeout: 超时时间（秒）

    Returns:
        bool: 骨架屏是否已消失
    """
    logger.info("等待骨架屏消失")
    js_code = '''
    (function() {
        const selectors = [
            '.skeleton', '.skeleton-screen', '[class*="skeleton"]',
            '.placeholder', '[class*="placeholder"]'
        ];
        for (const sel of selectors) {
            const el = document.querySelector(sel);
            if (el && el.offsetParent !== null) {
                return false;
            }
        }
        return true;
    })();
    '''
    from src.core.smart_wait import SmartWait
    smart_wait = SmartWait(session)
    result = await smart_wait.wait_for_js(js_code, timeout=timeout)
    logger.info(f"骨架屏等待完成: {result}")
    return result


async def wait_for_font_loaded(
    session,
    timeout: float = 10.0,
) -> bool:
    """
    等待 Web 字体加载完成

    Args:
        timeout: 超时时间（秒）

    Returns:
        bool: 字体是否已加载完成
    """
    logger.info("等待 Web 字体加载")
    js_code = '''
    (function() {
        if (!document.fonts || document.fonts.ready === undefined) return true;
        return document.fonts.ready.then(() => true).catch(() => true);
    })();
    '''
    from src.core.smart_wait import SmartWait
    smart_wait = SmartWait(session)
    result = await smart_wait.wait_for_js(js_code, timeout=timeout)
    logger.info(f"字体等待完成: {result}")
    return result


async def wait_for_popup_closed(
    session,
    timeout: float = 10.0,
) -> bool:
    """
    等待弹窗/模态框关闭

    Args:
        timeout: 超时时间（秒）

    Returns:
        bool: 弹窗是否已关闭
    """
    logger.info("等待弹窗关闭")
    js_code = '''
    (function() {
        const selectors = [
            '.modal', '.popup', '.dialog', '.overlay',
            '[role="dialog"]', '[role="alertdialog"]',
            '.modal-backdrop', '.popup-overlay'
        ];
        for (const sel of selectors) {
            const els = document.querySelectorAll(sel);
            for (const el of els) {
                if (el.offsetParent !== null && !el.classList.contains('d-none')) {
                    return false;
                }
            }
        }
        return true;
    })();
    '''
    from src.core.smart_wait import SmartWait
    smart_wait = SmartWait(session)
    result = await smart_wait.wait_for_js(js_code, timeout=timeout)
    logger.info(f"弹窗等待完成: {result}")
    return result


async def wait_for_video_loaded(
    session,
    selector: str = "video",
    timeout: float = 15.0,
) -> bool:
    """
    等待视频加载完成

    Args:
        selector: 视频元素选择器
        timeout: 超时时间（秒）

    Returns:
        bool: 视频是否已加载完成
    """
    logger.info(f"等待视频加载: {selector}")
    js_code = f'''
    (function() {{
        const videos = document.querySelectorAll('{selector}');
        if (videos.length === 0) return true;
        let allLoaded = true;
        videos.forEach(video => {{
            if (video.readyState < 3) {{  // HAVE_CURRENT_DATA = 3
                allLoaded = false;
            }}
        }});
        return allLoaded;
    }})();
    '''
    from src.core.smart_wait import SmartWait
    smart_wait = SmartWait(session)
    result = await smart_wait.wait_for_js(js_code, timeout=timeout)
    logger.info(f"视频等待完成: {result}")
    return result


async def wait_for_canvas_rendered(
    session,
    selector: str = "canvas",
    timeout: float = 10.0,
) -> bool:
    """
    等待 Canvas 渲染完成

    Args:
        selector: Canvas 选择器
        timeout: 超时时间（秒）

    Returns:
        bool: Canvas 是否已渲染
    """
    logger.info(f"等待 Canvas 渲染: {selector}")
    js_code = f'''
    (function() {{
        const canvases = document.querySelectorAll('{selector}');
        if (canvases.length === 0) return true;
        // 检查 canvas 是否有内容（非空白）
        let hasContent = false;
        canvases.forEach(canvas => {{
            try {{
                const ctx = canvas.getContext('2d');
                const imageData = ctx.getImageData(0, 0, 1, 1);
                // 检查像素是否有内容
                for (let i = 0; i < imageData.data.length; i += 4) {{
                    if (imageData.data[i] > 0 || imageData.data[i+1] > 0 || imageData.data[i+2] > 0) {{
                        hasContent = true;
                        return;
                    }}
                }}
            }} catch(e) {{
                hasContent = true; // 跨域 canvas 视为已渲染
            }}
        }});
        return hasContent;
    }})();
    '''
    from src.core.smart_wait import SmartWait
    smart_wait = SmartWait(session)
    result = await smart_wait.wait_for_js(js_code, timeout=timeout)
    logger.info(f"Canvas 等待完成: {result}")
    return result


async def wait_for_webgl_ready(
    session,
    timeout: float = 10.0,
) -> bool:
    """
    等待 WebGL 上下文就绪

    Args:
        timeout: 超时时间（秒）

    Returns:
        bool: WebGL 是否已就绪
    """
    logger.info("等待 WebGL 就绪")
    js_code = '''
    (function() {
        const canvas = document.createElement('canvas');
        const gl = canvas.getContext('webgl') || canvas.getContext('experimental-webgl');
        return !!gl;
    })();
    '''
    from src.core.smart_wait import SmartWait
    smart_wait = SmartWait(session)
    result = await smart_wait.wait_for_js(js_code, timeout=timeout)
    logger.info(f"WebGL 等待完成: {result}")
    return result


async def wait_for_intersection(
    session,
    selector: str,
    threshold: float = 0.1,
    timeout: float = 10.0,
) -> bool:
    """
    等待元素进入视口（Intersection Observer）

    Args:
        selector: 元素选择器
        threshold: 可见阈值
        timeout: 超时时间（秒）

    Returns:
        bool: 元素是否已进入视口
    """
    logger.info(f"等待元素进入视口: {selector}")
    js_code = f'''
    (function() {{
        return new Promise((resolve) => {{
            const el = document.querySelector('{selector}');
            if (!el) {{ resolve(false); return; }}
            const observer = new IntersectionObserver((entries) => {{
                entries.forEach(entry => {{
                    if (entry.isIntersecting) {{
                        observer.disconnect();
                        resolve(true);
                    }}
                }});
            }}, {{ threshold: {threshold} }});
            observer.observe(el);
            setTimeout(() => {{
                observer.disconnect();
                resolve(false);
            }}, {int(timeout * 1000)});
        }});
    }})();
    '''
    from src.core.smart_wait import SmartWait
    smart_wait = SmartWait(session)
    result = await smart_wait.wait_for_js(js_code, timeout=timeout)
    logger.info(f"Intersection 等待完成: {result}")
    return result


async def wait_for_resize_stable(
    session,
    timeout: float = 5.0,
) -> bool:
    """
    等待窗口 resize 稳定

    Args:
        timeout: 超时时间（秒）

    Returns:
        bool: resize 是否已稳定
    """
    logger.info("等待 resize 稳定")
    js_code = '''
    (function() {
        const width = window.innerWidth;
        const height = window.innerHeight;
        return { width: width, height: height };
    })();
    '''
    from src.core.smart_wait import SmartWait
    smart_wait = SmartWait(session)
    # 等待一段时间让 resize 事件稳定
    await asyncio.sleep(0.5)
    result = await smart_wait.wait_for_js(js_code, timeout=timeout)
    logger.info(f"Resize 等待完成: {result}")
    return True


async def wait_for_service_worker(
    session,
    timeout: float = 10.0,
) -> bool:
    """
    等待 Service Worker 激活

    Args:
        timeout: 超时时间（秒）

    Returns:
        bool: Service Worker 是否已激活
    """
    logger.info("等待 Service Worker 激活")
    js_code = '''
    (function() {
        if (!navigator.serviceWorker) return true;
        return navigator.serviceWorker.ready.then(() => true).catch(() => true);
    })();
    '''
    from src.core.smart_wait import SmartWait
    smart_wait = SmartWait(session)
    result = await smart_wait.wait_for_js(js_code, timeout=timeout)
    logger.info(f"Service Worker 等待完成: {result}")
    return result


async def wait_for_pwa_install(
    session,
    timeout: float = 15.0,
    auto_install: bool = False,
) -> bool:
    """
    等待 PWA 安装提示

    Args:
        timeout: 超时时间（秒）
        auto_install: 是否自动安装

    Returns:
        bool: PWA 安装提示是否已处理
    """
    logger.info("等待 PWA 安装提示")
    js_code = '''
    (function() {
        return window.__pwaPromptEvent !== undefined;
    })();
    '''
    from src.core.smart_wait import SmartWait
    smart_wait = SmartWait(session)
    result = await smart_wait.wait_for_js(js_code, timeout=timeout)
    if result and auto_install:
        # 触发 PWA 安装
        await session.evaluate('window.__pwaPromptEvent.prompt()')
        await session.evaluate('window.__pwaPromptEvent.userChoice')
        logger.info("PWA 已自动安装")
    logger.info(f"PWA 等待完成: {result}")
    return result


async def wait_for_js_error_settled(
    session,
    timeout: float = 5.0,
) -> bool:
    """
    等待 JS 错误收敛（无新错误产生）

    Args:
        timeout: 超时时间（秒）

    Returns:
        bool: JS 错误是否已收敛
    """
    logger.info("等待 JS 错误收敛")
    js_code = '''
    (function() {
        return window.__jsErrorCount || 0;
    })();
    '''
    from src.core.smart_wait import SmartWait
    smart_wait = SmartWait(session)
    # 记录当前错误数
    initial_count = await smart_wait.wait_for_js(js_code, timeout=2.0)
    await asyncio.sleep(timeout)
    # 检查错误数是否不再增长
    final_count = await smart_wait.wait_for_js(js_code, timeout=2.0)
    settled = (final_count - initial_count) <= 0
    logger.info(f"JS 错误收敛检查: 初始={initial_count}, 最终={final_count}, 收敛={settled}")
    return settled


async def wait_for_performance_stable(
    session,
    timeout: float = 5.0,
) -> bool:
    """
    等待页面性能指标稳定

    Args:
        timeout: 超时时间（秒）

    Returns:
        bool: 性能指标是否已稳定
    """
    logger.info("等待性能指标稳定")
    js_code = '''
    (function() {
        const perf = performance.getEntriesByType('navigation')[0];
        if (!perf) return true;
        const duration = perf.duration;
        return duration > 0 && duration < 30000; // 30秒内视为稳定
    })();
    '''
    from src.core.smart_wait import SmartWait
    smart_wait = SmartWait(session)
    result = await smart_wait.wait_for_js(js_code, timeout=timeout)
    logger.info(f"性能等待完成: {result}")
    return result
