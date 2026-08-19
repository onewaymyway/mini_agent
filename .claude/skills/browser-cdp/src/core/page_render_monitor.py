"""
page_render_monitor.py - 页面渲染监控与动态加载支持

提供全面的页面渲染状态检测和动态内容加载能力：
1. 显式等待 - 基于条件函数的等待
2. 网络空闲检测 - CDP Network 事件驱动的等待
3. 页面渲染完成判断 - 多维度评估页面就绪状态
4. DOM 变化监听 - MutationObserver 增强检测
5. 资源加载追踪 - 完整资源加载状态
6. 混合等待策略 - 组合多种策略的最优等待

适用于：
- 单页应用（SPA）路由切换
- 无限滚动加载
- AJAX 动态内容
- 懒加载图片和视频
- WebSocket 实时数据
- 验证码和登录验证页面
"""
from __future__ import annotations

import asyncio
import time
import logging
from typing import Optional, Callable, Any, List, Dict, Set, Tuple
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)


class RenderStatus(Enum):
    """页面渲染状态枚举"""
    NOT_STARTED = "not_started"
    DOM_LOADING = "dom_loading"  # DOM 正在构建
    NETWORK_BUSY = "network_busy"  # 网络请求进行中
    CONTENT_FILLING = "content_filling"  # 内容正在填充
    STABLE = "stable"  # 页面稳定
    ERROR = "error"  # 加载失败


@dataclass
class RenderMetrics:
    """页面渲染指标"""
    # 时间指标
    dom_content_loaded: float = 0.0  # DOMContentLoaded 时间
    load_event_fired: float = 0.0  # load 事件时间
    first_contentful_paint: float = 0.0  # 首次内容绘制
    
    # 计数指标
    total_requests: int = 0
    active_requests: int = 0
    xhr_fetch_count: int = 0
    active_xhr_fetch: int = 0
    pending_images: int = 0
    loaded_images: int = 0
    
    # DOM 指标
    dom_nodes_count: int = 0
    dom_changes_last_check: int = 0
    
    # 网络指标
    last_request_time: float = 0.0
    idle_since: float = 0.0
    
    # 状态
    render_status: RenderStatus = RenderStatus.NOT_STARTED
    is_interactive: bool = False
    is_fully_loaded: bool = False
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "render_status": self.render_status.value,
            "dom_content_loaded": self.dom_content_loaded,
            "load_event_fired": self.load_event_fired,
            "first_contentful_paint": self.first_contentful_paint,
            "total_requests": self.total_requests,
            "active_requests": self.active_requests,
            "xhr_fetch_count": self.xhr_fetch_count,
            "active_xhr_fetch": self.active_xhr_fetch,
            "pending_images": self.pending_images,
            "loaded_images": self.loaded_images,
            "dom_nodes_count": self.dom_nodes_count,
            "dom_changes_last_check": self.dom_changes_last_check,
            "last_request_time": self.last_request_time,
            "idle_since": self.idle_since,
            "is_interactive": self.is_interactive,
            "is_fully_loaded": self.is_fully_loaded,
        }


@dataclass
class WaitForOptions:
    """等待选项配置"""
    timeout: float = 30.0  # 总超时
    check_interval: float = 0.3  # 检查间隔
    idle_timeout: float = 0.5  # 网络空闲阈值
    stable_count: int = 3  # 内容稳定次数
    wait_network_idle: bool = True  # 等待网络空闲
    wait_dom_stable: bool = True  # 等待 DOM 稳定
    wait_images: bool = True  # 等待图片加载
    wait_fonts: bool = True  # 等待字体加载
    wait_ajax: bool = True  # 等待 AJAX 请求
    wait_selector: Optional[str] = None  # 等待特定元素
    wait_condition: Optional[Callable] = None  # 自定义条件
    critical_only: bool = True  # 只等待关键请求


class PageRenderMonitor:
    """
    页面渲染监控器
    
    提供多层次的页面就绪检测，集成 CDP Network 事件监听和 JS 评估。
    """
    
    # 关键请求类型（需要等待的）
    CRITICAL_REQUEST_TYPES = {'xhr', 'fetch', 'websocket'}
    
    # 静态资源模式（可忽略的）
    STATIC_RESOURCE_PATTERNS = {
        '.png', '.jpg', '.jpeg', '.gif', '.svg', '.webp', '.ico',
        '.css', '.js', '.woff', '.woff2', '.ttf', '.eot', '.otf',
        '.mp4', '.mp3', '.webm', '.avi', '.mpg', '.mpeg',
    }
    
    # 静态资源 MIME 类型
    STATIC_MIME_TYPES = {
        'image/', 'text/css', 'application/javascript', 'font/',
        'image/svg+xml', 'application/font', 'text/html',
        'video/', 'audio/',
    }
    
    def __init__(self, session, options: WaitForOptions = None):
        self.session = session
        self.options = options or WaitForOptions()
        
        # 状态跟踪
        self._metrics = RenderMetrics()
        self._network_enabled = False
        self._request_details: Dict[str, Dict] = {}
        self._pending_requests = 0
        self._active_xhr_fetch = 0
        self._critical_pending = 0
        self._idle_since = 0.0
        self._dom_hash_history: List[str] = []
        self._load_events: List[Dict] = []
        
        # 回调
        self._callbacks: List[Callable] = []
    
    # =========================================================================
    # 公共 API
    # =========================================================================
    
    async def wait_for_page_ready(
        self,
        selector: Optional[str] = None,
        timeout: Optional[float] = None,
        wait_all_resources: bool = False,
    ) -> Dict[str, Any]:
        """
        等待页面完全就绪
        
        Args:
            selector: 可选的选择器，等待特定元素出现
            timeout: 超时时间（秒）
            wait_all_resources: 是否等待所有资源（包括图片、字体等）
        
        Returns:
            渲染指标字典
        """
        timeout = timeout or self.options.timeout
        start_time = time.time()
        
        logger.info(f"开始等待页面就绪，超时: {timeout}s")
        
        try:
            # 启用网络监控
            self._enable_network_monitoring()
            
            # 阶段1：等待 DOM 基本就绪
            await self._wait_dom_ready(timeout=timeout * 0.3)
            
            # 阶段2：等待网络空闲（关键请求）
            if self.options.wait_network_idle:
                await self._wait_network_idle(
                    timeout=timeout * 0.4,
                    idle_timeout=self.options.idle_timeout,
                    critical_only=self.options.critical_only,
                )
            
            # 阶段3：等待内容稳定
            if self.options.wait_dom_stable:
                await self._wait_content_stable(
                    timeout=timeout * 0.2,
                    stable_count=self.options.stable_count,
                )
            
            # 阶段4：等待指定选择器（如果有）
            if selector:
                await self._wait_selector(selector, timeout=timeout * 0.1)
            
            # 阶段5：等待资源加载（可选）
            if wait_all_resources and self.options.wait_images:
                await self._wait_resources_loaded(timeout=timeout * 0.1)
            
            # 更新指标
            self._update_metrics()
            elapsed = time.time() - start_time
            
            result = {
                "success": True,
                "elapsed": elapsed,
                "metrics": self._metrics.to_dict(),
                "strategy_used": self._detect_strategy_used(),
            }
            
            logger.info(f"页面就绪检测完成，耗时: {elapsed:.2f}s")
            return result
            
        except Exception as e:
            logger.error(f"页面就绪检测失败: {e}")
            return {
                "success": False,
                "elapsed": time.time() - start_time,
                "error": str(e),
                "metrics": self._metrics.to_dict(),
            }
        finally:
            self._disable_network_monitoring()
    
    async def wait_for_network_idle(
        self,
        timeout: Optional[float] = None,
        idle_timeout: Optional[float] = None,
        critical_only: bool = True,
    ) -> bool:
        """
        等待网络空闲
        
        Args:
            timeout: 总超时
            idle_timeout: 空闲阈值
            critical_only: 只等待关键请求
        
        Returns:
            是否成功
        """
        timeout = timeout or self.options.timeout
        idle_timeout = idle_timeout or self.options.idle_timeout
        
        self._enable_network_monitoring()
        
        deadline = time.time() + timeout
        stable_since = 0.0
        
        while time.time() < deadline:
            current_pending = self._critical_pending if critical_only else self._pending_requests
            
            if current_pending == 0:
                if stable_since == 0:
                    stable_since = time.time()
                
                if time.time() - stable_since >= idle_timeout:
                    logger.debug(f"网络空闲检测通过 (关键请求={self._critical_pending}, 总请求={self._pending_requests})")
                    return True
            else:
                stable_since = 0.0
                logger.debug(f"当前请求数: 关键={self._critical_pending}, 总={self._pending_requests}")
            
            await asyncio.sleep(self.options.check_interval)
        
        return False
    
    async def wait_for_selector(
        self,
        selector: str,
        timeout: Optional[float] = None,
        visible: bool = True,
    ) -> bool:
        """
        等待 CSS 选择器出现
        
        Args:
            selector: CSS 选择器
            timeout: 超时时间
            visible: 是否等待可见
        
        Returns:
            是否成功
        """
        timeout = timeout or self.options.timeout
        deadline = time.time() + timeout
        
        if visible:
            js = f"""
            (() => {{
                const el = document.querySelector({selector!r});
                if (!el) return false;
                const rect = el.getBoundingClientRect();
                return rect.width > 0 && rect.height > 0 &&
                       rect.top >= 0 && rect.left >= 0 &&
                       rect.bottom <= window.innerHeight + 100;
            }})()
            """
        else:
            js = f"""
            (() => {{
                return document.querySelector({selector!r}) !== null;
            }})()
            """
        
        while time.time() < deadline:
            try:
                result = await self.session.eval_js(js)
                if result:
                    logger.debug(f"选择器 {selector} 已出现")
                    return True
            except Exception as e:
                logger.debug(f"选择器检查出错: {e}")
            await asyncio.sleep(self.options.check_interval)
        
        return False
    
    async def wait_for_condition(
        self,
        condition: Callable,
        timeout: Optional[float] = None,
        check_interval: Optional[float] = None,
    ) -> bool:
        """
        等待自定义条件满足
        
        Args:
            condition: 返回 bool 的异步函数
            timeout: 超时时间
            check_interval: 检查间隔
        
        Returns:
            是否成功
        """
        timeout = timeout or self.options.timeout
        check_interval = check_interval or self.options.check_interval
        
        deadline = time.time() + timeout
        
        while time.time() < deadline:
            try:
                result = condition() if asyncio.iscoroutinefunction(condition) else condition
                if asyncio.isawaitable(result):
                    result = await result
                if result:
                    logger.debug("自定义条件满足")
                    return True
            except Exception as e:
                logger.debug(f"条件检查出错: {e}")
            await asyncio.sleep(check_interval)
        
        return False
    
    async def wait_for_content_stable(
        self,
        stable_count: Optional[int] = None,
        timeout: Optional[float] = None,
    ) -> bool:
        """
        等待内容稳定（多次读取内容不变）
        
        Args:
            stable_count: 连续稳定次数
            timeout: 超时时间
        
        Returns:
            是否成功
        """
        stable_count = stable_count or self.options.stable_count
        timeout = timeout or self.options.timeout
        
        previous_content = None
        stable_iterations = 0
        deadline = time.time() + timeout
        
        while time.time() < deadline:
            try:
                content = await self.session.eval_js("document.body.innerText")
                
                if previous_content is not None:
                    if content == previous_content:
                        stable_iterations += 1
                        logger.debug(f"内容稳定检测 #{stable_iterations}/{stable_count}")
                        
                        if stable_iterations >= stable_count:
                            logger.debug("内容稳定性检测通过")
                            return True
                    else:
                        stable_iterations = 0
                        logger.debug("内容发生变化，重置稳定计数")
                
                previous_content = content
            except Exception as e:
                logger.debug(f"内容检查出错: {e}")
            
            await asyncio.sleep(self.options.check_interval)
        
        return False
    
    async def get_render_metrics(self) -> RenderMetrics:
        """
        获取当前渲染指标
        
        Returns:
            RenderMetrics 对象
        """
        self._update_metrics()
        return self._metrics
    
    def on_render_change(self, callback: Callable):
        """
        注册渲染状态变化回调
        
        Args:
            callback: 回调函数，接收 RenderMetrics 参数
        """
        self._callbacks.append(callback)
    
    # =========================================================================
    # 内部实现
    # =========================================================================
    
    def _enable_network_monitoring(self):
        """启用 CDP Network 监控"""
        if self._network_enabled:
            return
        
        self.session.subscribe('Network.requestWillBeSent', self._on_request_will_be_sent)
        self.session.subscribe('Network.loadingFinished', self._on_loading_finished)
        self.session.subscribe('Network.loadingError', self._on_loading_error)
        self.session.subscribe('Network.responseReceived', self._on_response_received)
        self.session.send('Network.enable')
        self._network_enabled = True
        logger.debug("已启用 CDP Network 监控")
    
    def _disable_network_monitoring(self):
        """禁用 CDP Network 监控"""
        if not self._network_enabled:
            return
        
        try:
            self.session.unsubscribe('Network.requestWillBeSent', self._on_request_will_be_sent)
            self.session.unsubscribe('Network.loadingFinished', self._on_loading_finished)
            self.session.unsubscribe('Network.loadingError', self._on_loading_error)
            self.session.unsubscribe('Network.responseReceived', self._on_response_received)
            self.session.send('Network.disable')
        except Exception as e:
            logger.debug(f"禁用 Network 监控时出错（可忽略）: {e}")
        finally:
            self._network_enabled = False
            self._pending_requests = 0
            self._active_xhr_fetch = 0
            self._critical_pending = 0
            self._request_details.clear()
    
    def _on_request_will_be_sent(self, params: dict):
        """CDP Network.requestWillBeSent 回调"""
        request_id = params.get('requestId', '')
        self._pending_requests += 1
        
        # 记录请求详情
        self._request_details[request_id] = params
        
        initiator = params.get('request', {}).get('initiator', {})
        if initiator.get('type') in self.CRITICAL_REQUEST_TYPES:
            self._active_xhr_fetch += 1
        
        # 统计关键请求
        if self._is_critical_request(params):
            self._critical_pending += 1
        
        self._metrics.total_requests += 1
        self._metrics.active_requests = self._pending_requests
        self._metrics.last_request_time = time.time()
        self._metrics.idle_since = 0.0
        
        # 通知回调
        for cb in self._callbacks:
            try:
                cb(self._metrics)
            except Exception:
                pass
    
    def _on_loading_finished(self, params: dict):
        """CDP Network.loadingFinished 回调"""
        request_id = params.get('requestId', '')
        self._pending_requests = max(0, self._pending_requests - 1)
        
        if request_id in self._request_details:
            if self._is_critical_request(self._request_details[request_id]):
                self._critical_pending = max(0, self._critical_pending - 1)
            if self._request_details[request_id].get('request', {}).get('initiator', {}).get('type') in self.CRITICAL_REQUEST_TYPES:
                self._active_xhr_fetch = max(0, self._active_xhr_fetch - 1)
            del self._request_details[request_id]
        
        self._metrics.active_requests = self._pending_requests
    
    def _on_loading_error(self, params: dict):
        """CDP Network.loadingError 回调"""
        self._on_loading_finished(params)
    
    def _on_response_received(self, params: dict):
        """CDP Network.responseReceived 回调"""
        request_id = params.get('requestId', '')
        if request_id not in self._request_details:
            self._request_details[request_id] = {}
        self._request_details[request_id]['response'] = params.get('response', {})
    
    def _is_critical_request(self, params: dict) -> bool:
        """判断请求是否关键"""
        request = params.get('request', {})
        url = request.get('url', '')
        initiator = request.get('initiator', {})
        request_type = initiator.get('type', '')
        
        # 检查请求类型
        if request_type in self.CRITICAL_REQUEST_TYPES:
            return True
        
        # 检查 MIME 类型
        response = params.get('response', {})
        mime_type = response.get('mimeType', '').lower()
        for static_mime in self.STATIC_MIME_TYPES:
            if mime_type.startswith(static_mime):
                return False
        
        # 检查路径模式
        url_lower = url.lower()
        for pattern in self.STATIC_RESOURCE_PATTERNS:
            if url_lower.endswith(pattern):
                return False
        
        return True
    
    async def _wait_dom_ready(self, timeout: float = 5.0) -> bool:
        """等待 DOM 基本就绪"""
        js = """
        (() => {
            return document.readyState === 'interactive' || document.readyState === 'complete';
        })()
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                ready = await self.session.eval_js(js)
                if ready:
                    logger.debug("DOM 基本就绪")
                    return True
            except Exception:
                pass
            await asyncio.sleep(0.2)
        return False
    
    async def _wait_resources_loaded(self, timeout: float = 5.0) -> bool:
        """等待所有资源加载完成"""
        js = """
        (() => {
            const images = document.querySelectorAll('img');
            if (images.length === 0) return true;
            let allLoaded = true;
            images.forEach(img => {
                if (!img.complete) allLoaded = false;
            });
            return allLoaded;
        })()
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                result = await self.session.eval_js(js)
                if result:
                    logger.debug("所有资源加载完成")
                    return True
            except Exception:
                pass
            await asyncio.sleep(0.3)
        return False
    
    def _update_metrics(self):
        """更新渲染指标"""
        try:
            # 获取 DOM 节点数量
            self._metrics.dom_nodes_count = asyncio.run(
                self.session.eval_js("document.documentElement.innerHTML.length")
            )
            
            # 获取图片状态
            pending, loaded = asyncio.run(self.session.eval_js('''
                (() => {
                    const imgs = document.querySelectorAll('img');
                    let pending = 0, loaded = 0;
                    imgs.forEach(img => {
                        if (img.complete && img.naturalWidth > 0) loaded++;
                        else pending++;
                    });
                    return [pending, loaded];
                })()
            '''))
            self._metrics.pending_images = pending
            self._metrics.loaded_images = loaded
            
            # 更新状态
            if self._critical_pending == 0 and self._pending_requests == 0:
                self._metrics.render_status = RenderStatus.STABLE
            elif self._active_xhr_fetch > 0:
                self._metrics.render_status = RenderStatus.NETWORK_BUSY
            else:
                self._metrics.render_status = RenderStatus.CONTENT_FILLING
            
        except Exception as e:
            logger.debug(f"更新指标时出错: {e}")
    
    def _detect_strategy_used(self) -> str:
        """检测使用的等待策略"""
        strategies = []
        if self._metrics.active_xhr_fetch == 0:
            strategies.append("network_idle")
        if self._metrics.dom_nodes_count > 0:
            strategies.append("dom_ready")
        if self._metrics.pending_images == 0:
            strategies.append("resources_loaded")
        return "+".join(strategies) if strategies else "unknown"


class EnhancedPageWait:
    """
    增强的页面等待管理器
    
    整合多种等待策略，提供智能的页面就绪检测。
    """
    
    def __init__(self, session, default_options: WaitForOptions = None):
        self.session = session
        self.default_options = default_options or WaitForOptions()
        self._monitors: Dict[str, PageRenderMonitor] = {}
    
    async def wait_for_page_ready(
        self,
        strategy: str = "adaptive",
        timeout: Optional[float] = None,
        selector: Optional[str] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """
        等待页面就绪（支持多种策略）
        
        Args:
            strategy: 等待策略 (adaptive/networkidle/selector/condition/dom_stable)
            timeout: 超时时间
            selector: CSS 选择器
            **kwargs: 其他参数
        
        Returns:
            等待结果字典
        """
        timeout = timeout or self.default_options.timeout
        options = WaitForOptions(timeout=timeout, **kwargs)
        
        monitor = PageRenderMonitor(self.session, options)
        
        if strategy == "adaptive":
            return await monitor.wait_for_page_ready(
                selector=selector,
                timeout=timeout,
                wait_all_resources=False,
            )
        elif strategy == "networkidle":
            return await self._wait_network_idle(monitor, timeout)
        elif strategy == "selector":
            if not selector:
                raise ValueError("selector 策略需要指定 selector 参数")
            return await self._wait_selector_only(monitor, selector, timeout)
        elif strategy == "condition":
            condition = kwargs.get('condition')
            if not condition:
                raise ValueError("condition 策略需要指定 condition 参数")
            return await self._wait_condition(monitor, condition, timeout)
        elif strategy == "dom_stable":
            return await self._wait_dom_stable(monitor, timeout)
        else:
            raise ValueError(f"未知的等待策略: {strategy}")
    
    async def _wait_network_idle(self, monitor: PageRenderMonitor, timeout: float) -> Dict:
        """网络空闲策略"""
        start = time.time()
        success = await monitor.wait_for_network_idle(timeout=timeout)
        return {
            "success": success,
            "elapsed": time.time() - start,
            "strategy": "networkidle",
        }
    
    async def _wait_selector_only(self, monitor: PageRenderMonitor, selector: str, timeout: float) -> Dict:
        """选择器策略"""
        start = time.time()
        success = await monitor.wait_for_selector(selector, timeout=timeout)
        return {
            "success": success,
            "elapsed": time.time() - start,
            "strategy": "selector",
            "selector": selector,
        }
    
    async def _wait_condition(self, monitor: PageRenderMonitor, condition: Callable, timeout: float) -> Dict:
        """条件策略"""
        start = time.time()
        success = await monitor.wait_for_condition(condition, timeout=timeout)
        return {
            "success": success,
            "elapsed": time.time() - start,
            "strategy": "condition",
        }
    
    async def _wait_dom_stable(self, monitor: PageRenderMonitor, timeout: float) -> Dict:
        """DOM 稳定策略"""
        start = time.time()
        success = await monitor.wait_for_content_stable(timeout=timeout)
        return {
            "success": success,
            "elapsed": time.time() - start,
            "strategy": "dom_stable",
        }


# =====================================================================
# 便捷函数
# =====================================================================

async def wait_for_page_ready(
    session,
    timeout: float = 30.0,
    selector: Optional[str] = None,
    wait_all_resources: bool = False,
) -> Dict[str, Any]:
    """
    便捷函数：等待页面就绪
    
    Args:
        session: CDP session
        timeout: 超时时间
        selector: 等待的选择器
        wait_all_resources: 是否等待所有资源
    
    Returns:
        渲染指标字典
    """
    monitor = PageRenderMonitor(session)
    return await monitor.wait_for_page_ready(
        selector=selector,
        timeout=timeout,
        wait_all_resources=wait_all_resources,
    )


async def wait_for_network_idle(
    session,
    timeout: float = 10.0,
    idle_timeout: float = 0.5,
) -> bool:
    """
    便捷函数：等待网络空闲
    
    Args:
        session: CDP session
        timeout: 总超时
        idle_timeout: 空闲阈值
    
    Returns:
        是否成功
    """
    monitor = PageRenderMonitor(session)
    return await monitor.wait_for_network_idle(
        timeout=timeout,
        idle_timeout=idle_timeout,
    )


async def wait_for_selector(
    session,
    selector: str,
    timeout: float = 10.0,
    visible: bool = True,
) -> bool:
    """
    便捷函数：等待选择器出现
    
    Args:
        session: CDP session
        selector: CSS 选择器
        timeout: 超时时间
        visible: 是否等待可见
    
    Returns:
        是否成功
    """
    monitor = PageRenderMonitor(session)
    return await monitor.wait_for_selector(
        selector=selector,
        timeout=timeout,
        visible=visible,
    )


# =====================================================================
# DOM 变化监听器
# =====================================================================

class DOMMutationWatcher:
    """
    DOM 变化监听器（基于 MutationObserver）
    
    用于检测页面内容的动态变化。
    """
    
    def __init__(self, session, options: dict = None):
        self.session = session
        self.options = options or {
            "child_list": True,
            "attributes": True,
            "character_data": False,
            "subtree": True,
        }
        self._mutation_count = 0
        self._last_mutation_time = 0.0
        self._watching = False
    
    async def start_watching(self) -> bool:
        """
        启动 DOM 变化监听
        
        Returns:
            是否成功启动
        """
        js = '''
        (function() {
            if (window.__mutationWatcher__) return false;
            
            let mutationCount = 0;
            let lastTime = Date.now();
            
            const observer = new MutationObserver((mutations) => {
                mutations.forEach((mutation) => {
                    mutationCount++;
                    lastTime = Date.now();
                });
            });
            
            observer.observe(document.body, {
                childList: true,
                attributes: true,
                characterData: true,
                subtree: true
            });
            
            window.__mutationWatcher__ = {
                observer: observer,
                get count() { return mutationCount; },
                get lastTime() { return lastTime; },
                reset: function() { mutationCount = 0; },
                stop: function() { observer.disconnect(); delete window.__mutationWatcher__; }
            };
            
            return true;
        })();
        '''
        result = await self.session.eval_js(js)
        self._watching = result
        return result
    
    async def stop_watching(self) -> bool:
        """停止 DOM 变化监听"""
        js = '''
        (function() {
            if (window.__mutationWatcher__) {
                window.__mutationWatcher__.stop();
                return true;
            }
            return false;
        })();
        '''
        result = await self.session.eval_js(js)
        self._watching = False
        return result
    
    async def get_mutation_count(self) -> int:
        """获取变化计数"""
        js = '''
        (function() {
            if (window.__mutationWatcher__) return window.__mutationWatcher__.count;
            return 0;
        })();
        '''
        return await self.session.eval_js(js)
    
    async def wait_for_stable(self, timeout: float = 5.0, check_interval: float = 0.5) -> bool:
        """
        等待 DOM 稳定
        
        Args:
            timeout: 超时时间
            check_interval: 检查间隔
        
        Returns:
            是否稳定
        """
        if not self._watching:
            await self.start_watching()
        
        deadline = time.time() + timeout
        last_count = 0
        
        while time.time() < deadline:
            await asyncio.sleep(check_interval)
            current_count = await self.get_mutation_count()
            
            if current_count == last_count and current_count > 0:
                logger.debug("DOM 稳定检测通过")
                return True
            
            last_count = current_count
        
        return False
    
    async def __aenter__(self):
        await self.start_watching()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.stop_watching()