"""
元素可见性检测器 - 增强版

提供结构化的元素可见性、可交互性检测能力：
- 基于几何位置的可见性检测
- 动态加载状态感知
- 登录验证后的元素可达性检查
- 反检测场景适配
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any, Callable

logger = logging.getLogger(__name__)


@dataclass
class VisibilityResult:
    """可见性检测结果"""
    visible: bool
    interactive: bool
    exists: bool
    reason: str = ""
    details: Dict[str, Any] = field(default_factory=dict)
    
    def __bool__(self):
        return self.visible and self.exists


class ElementVisibilityDetector:
    """
    元素可见性检测器
    
    核心能力：
    1. 几何可见性检测（getBoundingClientRect）
    2. DOM可见性检测（offsetParent, computed style）
    3. 交互可达性检测（tab index, pointer events）
    4. 动态加载状态检测（loading/spinner识别）
    5. 登录状态感知检测
    """
    
    # 常见加载状态选择器
    LOADING_SELECTORS = [
        '.loading', '.spinner', '.skeleton', '[class*="loading"]',
        '[class*="spinner"]', '.is-loading', '.ant-spin', '.el-loading'
    ]
    
    # 常见遮挡层选择器
    OVERLAY_SELECTORS = [
        '.modal', '.overlay', '.mask', '.backdrop', '[class*="modal"]',
        '[class*="popup"]', '.ant-modal', '.el-dialog'
    ]
    
    # 可见性检测JS模板
    VISIBILITY_JS_TEMPLATE = """
    (function() {{
        var selector = arguments[0];
        var el = null;
        
        try {{
            el = document.querySelector(selector);
        }} catch(e) {{
            return {{ visible: false, exists: false, error: e.message }};
        }}
        
        if (!el) {{
            return {{ visible: false, exists: false }};
        }}
        
        // 几何可见性
        var rect = el.getBoundingClientRect();
        var isVisible = rect.width > 0 && rect.height > 0 &&
                       el.offsetParent !== null &&
                       getComputedStyle(el).visibility !== 'hidden' &&
                       getComputedStyle(el).display !== 'none';
        
        // 交互可达性
        var isInteractive = isVisible && (
            el.tabIndex >= 0 || 
            el.hasAttribute('onclick') ||
            el.tagName === 'A' ||
            el.tagName === 'BUTTON' ||
            el.tagName === 'INPUT' ||
            getComputedStyle(el).pointerEvents !== 'none'
        );
        
        // 是否在视口内
        var inViewport = isVisible && rect.top < window.innerHeight && 
                        rect.bottom > 0 && rect.left < window.innerWidth &&
                        rect.right > 0;
        
        return {{
            visible: isVisible,
            interactive: isInteractive,
            exists: true,
            in_viewport: inViewport,
            rect: {{
                x: Math.round(rect.x),
                y: Math.round(rect.y),
                w: Math.round(rect.width),
                h: Math.round(rect.height)
            }}
        }};
    }})()
    """
    
    # 加载状态检测JS
    LOADING_STATE_JS = """
    (function() {{
        var loadingSelectors = arguments[0];
        var targetSelector = arguments[1];
        var loadingEls = document.querySelectorAll(loadingSelectors.join(','));
        var targetEl = document.querySelector(targetSelector);
        
        var isPageLoading = loadingEls.length > 0 && targetEl !== null;
        
        // 检查是否有全屏loading遮罩
        var hasOverlay = false;
        try {{
            var overlays = document.querySelectorAll(arguments[2]);
            hasOverlay = overlays.length > 0;
        }} catch(e) {{}}
        
        return {{
            page_loading: isPageLoading,
            has_overlay: hasOverlay,
            loading_count: loadingEls.length
        }};
    }})()
    """
    
    def __init__(self, session, config: Dict[str, Any] = None):
        self.session = session
        self.config = config or {}
        self._history: List[Dict] = []
    
    async def check_visibility(
        self,
        selector: str,
        timeout: float = 5.0,
        poll_interval: float = 0.3,
    ) -> VisibilityResult:
        """
        检查元素可见性
        
        Args:
            selector: CSS选择器
            timeout: 超时时间（秒）
            poll_interval: 轮询间隔（秒）
        
        Returns:
            VisibilityResult
        """
        start_time = time.time()
        last_result = VisibilityResult(visible=False, interactive=False, exists=False, reason="timeout")
        
        while time.time() - start_time < timeout:
            try:
                result_data = await self.session.eval_js(
                    self.VISIBILITY_JS_TEMPLATE,
                    [selector]
                )
                
                if result_data.get('exists', False):
                    result = VisibilityResult(
                        visible=result_data.get('visible', False),
                        interactive=result_data.get('interactive', False),
                        exists=True,
                        reason='',
                        details=result_data
                    )
                    self._record(selector, result)
                    
                    if result.visible:
                        logger.debug(f"元素可见: {selector} ({time.time()-start_time:.2f}s)")
                        return result
                    
                    last_result = result
                else:
                    last_result = VisibilityResult(
                        visible=False,
                        interactive=False,
                        exists=False,
                        reason='element_not_found'
                    )
            except Exception as e:
                logger.debug(f"可见性检查异常: {e}")
                last_result = VisibilityResult(
                    visible=False,
                    interactive=False,
                    exists=False,
                    reason=str(e)
                )
            
            await asyncio.sleep(poll_interval)
        
        # 超时
        elapsed = time.time() - start_time
        last_result.reason = f"timeout({elapsed:.1f}s)"
        self._record(selector, last_result)
        return last_result
    
    async def check_interactive(
        self,
        selector: str,
        timeout: float = 5.0,
    ) -> VisibilityResult:
        """
        检查元素是否可交互
        """
        result = await self.check_visibility(selector, timeout)
        if result.exists and result.visible:
            # 额外检查交互性
            try:
                interactive_js = """
                (function() {
                    var el = document.querySelector(arguments[0]);
                    if (!el) return {clickable: false, focusable: false};
                    var rect = el.getBoundingClientRect();
                    var clickable = rect.width > 0 && rect.height > 0 &&
                                   getComputedStyle(el).pointerEvents !== 'none';
                    var focusable = el.tabIndex >= 0 || el.hasAttribute('tabindex');
                    return {clickable: clickable, focusable: focusable};
                })()
                """
                extra = await self.session.eval_js(interactive_js, [selector])
                result.interactive = extra.get('clickable', False) or extra.get('focusable', False)
            except Exception:
                pass
        return result
    
    async def wait_for_visible(
        self,
        selector: str,
        timeout: float = 10.0,
        poll_interval: float = 0.5,
    ) -> VisibilityResult:
        """
        等待元素可见（轮询版本）
        """
        return await self.check_visibility(selector, timeout, poll_interval)
    
    async def check_loading_state(
        self,
        target_selector: str = None,
    ) -> Dict[str, Any]:
        """
        检查页面加载状态
        """
        try:
            result = await self.session.eval_js(
                self.LOADING_STATE_JS,
                [self.LOADING_SELECTORS, target_selector or '', self.OVERLAY_SELECTORS]
            )
            return result
        except Exception as e:
            logger.debug(f"加载状态检查失败: {e}")
            return {'page_loading': False, 'has_overlay': False, 'error': str(e)}
    
    async def is_element_blocked(
        self,
        selector: str,
    ) -> bool:
        """
        检查元素是否被遮挡（登录弹窗等）
        """
        try:
            blocked_js = """
            (function() {
                var targetSelector = arguments[0];
                var overlaySelectors = arguments[1];
                var targetEl = document.querySelector(targetSelector);
                
                if (!targetEl) return {blocked: false, reason: 'not_found'};
                
                var rect = targetEl.getBoundingClientRect();
                
                // 检查是否有覆盖层遮挡
                try {
                    var overlays = document.querySelectorAll(overlaySelectors.join(','));
                    for (var i = 0; i < overlays.length; i++) {
                        var ovRect = overlays[i].getBoundingClientRect();
                        // 简单判断：如果覆盖层覆盖了目标元素的大部分区域
                        if (ovRect.width > 0 && ovRect.height > 0 &&
                            rect.top >= ovRect.top && rect.left >= ovRect.left) {
                            return {blocked: true, reason: 'overlay'};
                        }
                    }
                } catch(e) {}
                
                // 检查滚动位置
                var scrollTop = window.pageYOffset || document.documentElement.scrollTop;
                var inViewPort = rect.top >= 0 && rect.bottom <= window.innerHeight;
                
                return {blocked: false, reason: 'clear', in_viewport: inViewPort};
            })()
            """
            result = await self.session.eval_js(blocked_js, [selector, self.OVERLAY_SELECTORS])
            return result.get('blocked', False)
        except Exception:
            return False
    
    def _record(self, selector: str, result: VisibilityResult):
        """记录检测历史"""
        self._history.append({
            'selector': selector,
            'visible': result.visible,
            'interactive': result.interactive,
            'exists': result.exists,
            'timestamp': time.time(),
        })
    
    def get_stats(self) -> Dict[str, Any]:
        """获取统计信息"""
        if not self._history:
            return {'total': 0}
        
        visibles = [h for h in self._history if h['visible']]
        exists = [h for h in self._history if h['exists']]
        
        return {
            'total': len(self._history),
            'visible_count': len(visibles),
            'exists_count': len(exists),
            'visible_rate': f"{len(visibles)}/{len(self._history)}",
        }


async def check_visibility(
    session,
    selector: str,
    timeout: float = 5.0,
    poll_interval: float = 0.3,
) -> VisibilityResult:
    """
    模块级便捷函数：检查元素可见性

    Args:
        session: browser-cdp session 对象
        selector: CSS选择器
        timeout: 超时时间（秒）
        poll_interval: 轮询间隔（秒）

    Returns:
        VisibilityResult
    """
    detector = ElementVisibilityDetector(session)
    return await detector.check_visibility(selector, timeout=timeout, poll_interval=poll_interval)
