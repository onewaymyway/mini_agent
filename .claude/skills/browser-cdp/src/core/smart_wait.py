"""
smart_wait.py - 智能等待模块（增强版）

提供多种等待策略，替代简单的 wait_selector：
- networkidle: 等待网络空闲（所有请求完成且 idle_timeout 内无新请求）
- route: 等待 SPA 路由稳定
- stable: 内容稳定性检测（多次读取内容不变）
- ajax: 等待 AJAX 请求完成
- selector: 等待 CSS 选择器出现（原有功能增强）
- adaptive: 自适应等待（根据页面复杂度自动调整）
- condition: 条件等待（自定义条件函数）
- parallel: 并行等待（同时等待多个条件）
- retry: 带退避的重试等待
"""
from __future__ import annotations

import asyncio
import time
from typing import Optional, Callable, Any, List, Union
from dataclasses import dataclass, field
import logging

logger = logging.getLogger(__name__)


@dataclass
class WaitConfig:
    """等待配置"""
    timeout: float = 30.0
    idle_timeout: float = 0.5  # networkidle 的空闲阈值
    check_interval: float = 0.3  # 轮询间隔
    stable_count: int = 3  # stable 策略的连续稳定次数
    max_retries: int = 3  # 最大重试次数
    backoff_factor: float = 2.0  # 退避因子
    adaptive_timeout: bool = True  # 是否启用自适应超时
    page_complexity: str = "auto"  # 页面复杂度: auto/low/medium/high


@dataclass
class WaitResult:
    """等待结果"""
    success: bool
    strategy: str
    elapsed: float
    details: dict = field(default_factory=dict)


class SmartWait:
    """智能等待器（增强版）"""

    # 页面复杂度对应的超时倍数
    COMPLEXITY_MULTIPLIERS = {
        "low": 0.8,
        "medium": 1.0,
        "high": 1.5,
        "auto": 1.0,
    }

    # 关键请求类型（需要等待完成的请求）
    CRITICAL_REQUEST_TYPES = {'xhr', 'fetch', 'websocket'}
    # 非关键请求类型（可忽略的静态资源）
    NON_CRITICAL_MIME_TYPES = {
        'image/', 'text/css', 'application/javascript', 'font/',
        'image/svg+xml', 'application/font', 'text/html',
    }
    # 非关键请求路径模式
    NON_CRITICAL_PATH_PATTERNS = {
        '.png', '.jpg', '.jpeg', '.gif', '.svg', '.webp', '.ico',
        '.css', '.js', '.woff', '.woff2', '.ttf', '.eot', '.otf',
        '.mp4', '.mp3', '.webm', '.avi',
    }

    def __init__(self, session, config: WaitConfig = None):
        self.session = session
        self.config = config or WaitConfig()
        self._pending_requests = 0
        self._active_xhr_fetch = 0
        self._critical_pending = 0  # 关键请求计数
        self._network_enabled = False
        self._xhr_callbacks = []
        self._wait_history: List[dict] = []
        self._request_details: dict = {}  # 请求 ID → 请求详情

    def get_effective_timeout(self) -> float:
        """根据页面复杂度计算有效超时时间"""
        if not self.config.adaptive_timeout:
            return self.config.timeout
        multiplier = self.COMPLEXITY_MULTIPLIERS.get(
            self.config.page_complexity, 1.0
        )
        return self.config.timeout * multiplier

    async def wait_for(
        self,
        strategy: str,
        timeout: float = None,
        **kwargs,
    ) -> WaitResult:
        """
        根据策略等待页面稳定

        Args:
            strategy: 等待策略
            timeout: 超时时间（覆盖配置）
            **kwargs: 策略特定参数

        Returns:
            WaitResult: 等待结果
        """
        effective_timeout = timeout or self.get_effective_timeout()
        handlers = {
            "networkidle": self._wait_network_idle,
            "route": self._wait_route,
            "stable": self._wait_stable,
            "ajax": self._wait_ajax,
            "selector": self._wait_selector,
            "adaptive": self._wait_adaptive,
            "condition": self._wait_condition,
            "parallel": self._wait_parallel,
            "retry": self._wait_retry,
            "animation": self._wait_animation,
            "font": self._wait_font,
            "image": self._wait_image,
            "iframe": self._wait_iframe,
            "shadow_dom": self._wait_shadow_dom,
            "dom_stable": self._wait_dom_stable,
            "data_loaded": self._wait_data_loaded,
        }

        if strategy not in handlers:
            raise ValueError(f"未知的等待策略: {strategy}")

        logger.info(f"开始等待策略: {strategy}，超时: {effective_timeout}s")
        start_time = time.time()

        try:
            result = await asyncio.wait_for(
                handlers[strategy](timeout=effective_timeout, **kwargs),
                timeout=effective_timeout,
            )
            elapsed = time.time() - start_time
            wait_result = WaitResult(
                success=True,
                strategy=strategy,
                elapsed=elapsed,
                details=kwargs,
            )
            self._wait_history.append(wait_result.__dict__)
            logger.info(f"等待策略 {strategy} 完成，耗时 {elapsed:.2f}s")
            return wait_result
        except asyncio.TimeoutError:
            elapsed = time.time() - start_time
            wait_result = WaitResult(
                success=False,
                strategy=strategy,
                elapsed=elapsed,
                details={"error": "timeout"},
            )
            self._wait_history.append(wait_result.__dict__)
            logger.warning(f"等待策略 {strategy} 超时，耗时 {elapsed:.2f}s")
            return wait_result
        finally:
            self._cleanup_network_events()

    async def wait_for_selector(
        self,
        selector: str,
        timeout: float = None,
        visible: bool = True,
    ) -> WaitResult:
        """便捷方法：等待选择器出现"""
        return await self.wait_for(
            "selector",
            timeout=timeout,
            selector=selector,
            visible=visible,
        )

    async def wait_for_network_idle(
        self,
        idle_timeout: float = None,
        timeout: float = None,
    ) -> WaitResult:
        """便捷方法：等待网络空闲"""
        return await self.wait_for(
            "networkidle",
            timeout=timeout,
            idle_timeout=idle_timeout,
        )

    async def wait_for_content_stable(
        self,
        stable_count: int = None,
        timeout: float = None,
    ) -> WaitResult:
        """便捷方法：等待内容稳定"""
        return await self.wait_for(
            "stable",
            timeout=timeout,
            stable_count=stable_count,
        )

    async def wait_adaptive(
        self,
        selector: str = None,
        network_idle: bool = True,
        content_stable: bool = True,
        timeout: float = None,
    ) -> WaitResult:
        """自适应等待：根据页面类型自动选择策略"""
        return await self.wait_for(
            "adaptive",
            timeout=timeout,
            selector=selector,
            network_idle=network_idle,
            content_stable=content_stable,
        )

    async def wait_condition(
        self,
        condition: Callable,
        timeout: float = None,
        check_interval: float = None,
    ) -> WaitResult:
        """条件等待：等待自定义条件满足"""
        return await self.wait_for(
            "condition",
            timeout=timeout,
            condition=condition,
            check_interval=check_interval,
        )

    async def wait_parallel(
        self,
        conditions: List[Union[str, dict]],
        timeout: float = None,
    ) -> WaitResult:
        """并行等待：同时等待多个条件"""
        return await self.wait_for(
            "parallel",
            timeout=timeout,
            conditions=conditions,
        )

    async def wait_with_retry(
        self,
        strategy: str,
        max_retries: int = None,
        backoff_factor: float = None,
        **kwargs,
    ) -> WaitResult:
        """带退避的重试等待"""
        return await self.wait_for(
            "retry",
            strategy=strategy,
            max_retries=max_retries,
            backoff_factor=backoff_factor,
            **kwargs,
        )

    def get_wait_stats(self) -> dict:
        """获取等待统计信息"""
        if not self._wait_history:
            return {"total_waits": 0, "success_rate": 0.0}

        total = len(self._wait_history)
        successes = sum(1 for w in self._wait_history if w["success"])
        avg_elapsed = sum(w["elapsed"] for w in self._wait_history) / total

        return {
            "total_waits": total,
            "successes": successes,
            "failures": total - successes,
            "success_rate": successes / total if total > 0 else 0.0,
            "avg_elapsed": round(avg_elapsed, 2),
            "strategies_used": list(set(w["strategy"] for w in self._wait_history)),
        }

    def clear_wait_history(self):
        """清空等待历史"""
        self._wait_history.clear()
    def _is_critical_request(self, params: dict) -> bool:
        """判断请求是否关键（需要等待完成）"""
        request = params.get('request', {})
        url = request.get('url', '')
        initiator = request.get('initiator', {})
        request_type = initiator.get('type', '')

        # 检查请求类型
        if request_type in self.CRITICAL_REQUEST_TYPES:
            return True

        # 检查 MIME 类型（在 response 阶段更准确）
        response = params.get('response', {})
        mime_type = response.get('mimeType', '').lower()
        for non_critical in self.NON_CRITICAL_MIME_TYPES:
            if mime_type.startswith(non_critical):
                return False

        # 检查路径模式
        url_lower = url.lower()
        for pattern in self.NON_CRITICAL_PATH_PATTERNS:
            if url_lower.endswith(pattern):
                return False

        # 默认视为关键请求
        return True

    def _on_request_will_be_sent(self, params: dict) -> None:
        """CDP Network.requestWillBeSent 回调"""
        request_id = params.get('requestId', '')
        self._pending_requests += 1

        # 记录请求详情用于后续判断
        self._request_details[request_id] = params

        initiator = params.get('request', {}).get('initiator', {})
        if initiator.get('type') in ('xhr', 'fetch'):
            self._active_xhr_fetch += 1

        # 统计关键请求
        if self._is_critical_request(params):
            self._critical_pending += 1

        for cb in self._xhr_callbacks:
            cb('request', params)

    def _on_loading_finished(self, params: dict) -> None:
        """CDP Network.loadingFinished 回调"""
        request_id = params.get('requestId', '')
        self._pending_requests = max(0, self._pending_requests - 1)

        # 减少关键请求计数
        if request_id in self._request_details:
            if self._is_critical_request(self._request_details[request_id]):
                self._critical_pending = max(0, self._critical_pending - 1)
            del self._request_details[request_id]

        for cb in self._xhr_callbacks:
            cb('finish', params)

    def _on_response_received(self, params: dict) -> None:
        """CDP Network.responseReceived 回调"""
        request_id = params.get('requestId', '')
        # 更新请求详情（包含 MIME 类型信息）
        if request_id not in self._request_details:
            self._request_details[request_id] = {}
        self._request_details[request_id]['response'] = params.get('response', {})

        for cb in self._xhr_callbacks:
            cb('response', params)
    
    def _register_network_events(self) -> None:
        """注册 CDP Network 事件监听"""
        if self._network_enabled:
            return
        self.session.subscribe('Network.requestWillBeSent', self._on_request_will_be_sent)
        self.session.subscribe('Network.loadingFinished', self._on_loading_finished)
        self.session.subscribe('Network.responseReceived', self._on_response_received)
        self.session.send('Network.enable')
        self._network_enabled = True
        logger.debug("已注册 CDP Network 事件监听")
    
    def _cleanup_network_events(self) -> None:
        """清理 CDP Network 事件监听"""
        if not self._network_enabled:
            return
        try:
            self.session.unsubscribe('Network.requestWillBeSent', self._on_request_will_be_sent)
            self.session.unsubscribe('Network.loadingFinished', self._on_loading_finished)
            self.session.unsubscribe('Network.responseReceived', self._on_response_received)
            self.session.send('Network.disable')
        except Exception as e:
            logger.debug(f"清理 Network 事件时出错（可忽略）: {e}")
        finally:
            self._network_enabled = False
            self._pending_requests = 0
            self._active_xhr_fetch = 0
            self._critical_pending = 0
            self._request_details.clear()
    
    async def _wait_network_idle(self, idle_timeout: float = None, timeout: float = None, wait_critical_only: bool = True) -> bool:
        """
        等待网络空闲：关键请求完成且 idle_timeout 内无新关键请求

        实现原理：
        1. 启用 CDP Network domain
        2. 监听 Network.requestWillBeSent 增加 pending 计数（区分关键/非关键）
        3. 监听 Network.loadingFinished 减少计数
        4. 当 critical_pending=0 时记录 stable_since 时间，持续 idle_timeout 内无新关键请求则通过

        关键请求：XHR/Fetch/WebSocket 请求，或 MIME 类型非静态资源的请求
        非关键请求：图片、CSS、JS、字体等静态资源（可忽略）

        Args:
            idle_timeout: 空闲阈值（秒）
            timeout: 总超时时间（秒）
            wait_critical_only: 是否只等待关键请求（默认 True，忽略静态资源）
        """
        idle_timeout = idle_timeout or self.config.idle_timeout
        timeout = timeout or self.config.timeout

        self._register_network_events()

        deadline = time.time() + timeout
        stable_since = 0.0  # 网络空闲开始时间

        while time.time() < deadline:
            # 根据 wait_critical_only 选择计数方式
            current_pending = self._critical_pending if wait_critical_only else self._pending_requests

            if current_pending == 0:
                if stable_since == 0:
                    stable_since = time.time()  # 记录空闲开始时间
                # 检查是否持续 idle_timeout 内无新关键请求
                if time.time() - stable_since >= idle_timeout:
                    logger.debug(f"网络空闲检测通过 (关键请求={self._critical_pending}, 总请求={self._pending_requests})")
                    return True
            else:
                # 有新请求，重置稳定计时
                stable_since = 0.0
                logger.debug(f"当前请求数: 关键={self._critical_pending}, 总={self._pending_requests}")

            await asyncio.sleep(self.config.check_interval)

        return False
    
    async def _wait_route(self, expected_url: str = None, change_count: int = 1, timeout: float = None) -> bool:
        """
        等待 SPA 路由稳定

        Args:
            expected_url: 期望的 URL 模式（可选）
            change_count: 要求路由稳定前的变化次数
            timeout: 超时时间
        """
        timeout = timeout or self.config.timeout
        previous_url = await self.session.eval_js("location.href")
        changes = 0

        deadline = time.time() + timeout
        while time.time() < deadline:
            current_url = await self.session.eval_js("location.href")
            
            if current_url != previous_url:
                changes += 1
                previous_url = current_url
                logger.debug(f"路由变化 #{changes}: {current_url}")
            
            # 检查是否达到稳定条件
            if changes >= change_count:
                if expected_url and expected_url not in current_url:
                    logger.debug(f"URL 不包含期望模式: {expected_url}")
                    previous_url = current_url  # 重置计数
                    continue
                logger.debug(f"路由稳定检测通过，最终 URL: {current_url}")
                return True
            
            await asyncio.sleep(self.config.check_interval)
        
        return False
    
    async def _wait_stable(self, check_interval: float = None, stable_count: int = None, timeout: float = None) -> bool:
        """
        内容稳定性检测：多次读取页面内容，确认不变

        Args:
            check_interval: 检查间隔
            stable_count: 连续稳定次数
            timeout: 超时时间
        """
        check_interval = check_interval or self.config.check_interval
        stable_count = stable_count or self.config.stable_count
        timeout = timeout or self.config.timeout

        previous_content = None
        stable_iterations = 0

        deadline = time.time() + timeout
        while time.time() < deadline:
            # 获取页面主体内容
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
            await asyncio.sleep(check_interval)
        
        return False
    
    async def _wait_ajax(self, timeout: float = None) -> bool:
        """
        等待 AJAX 请求完成
        
        通过 CDP Network domain 监听 XHR/Fetch 请求实现
        """
        timeout = timeout or self.config.timeout
        
        self._register_network_events()
        
        deadline = time.time() + timeout
        while time.time() < deadline:
            active = await self._get_active_xhr_fetch()
            
            if active == 0:
                # 短暂等待确认无新请求
                await asyncio.sleep(0.5)
                if await self._get_active_xhr_fetch() == 0:
                    logger.debug("AJAX 请求检测通过")
                    return True
            else:
                logger.debug(f"活跃 AJAX 请求数: {active}")
            
            await asyncio.sleep(self.config.check_interval)
        
        return False
    
    async def _wait_selector(self, selector: str, timeout: float = None, visible: bool = True) -> bool:
        """
        等待 CSS 选择器出现（增强版）

        Args:
            selector: CSS 选择器
            timeout: 超时时间
            visible: 是否等待可见
        """
        timeout = timeout or self.config.timeout

        if visible:
            js = f"""
            (() => {{
                const el = document.querySelector({selector!r});
                if (!el) return false;
                const rect = el.getBoundingClientRect();
                return rect.width > 0 && rect.height > 0 &&
                       rect.top >= 0 && rect.left >= 0;
            }})()
            """
        else:
            js = f"""
            (() => {{
                return document.querySelector({selector!r}) !== null;
            }})()
            """

        deadline = time.time() + timeout
        while time.time() < deadline:
            result = await self.session.eval_js(js)
            if result:
                logger.debug(f"选择器 {selector} 已出现")
                return True
            await asyncio.sleep(self.config.check_interval)

        return False

    async def _wait_adaptive(
        self,
        selector: str = None,
        network_idle: bool = True,
        content_stable: bool = True,
        timeout: float = None,
        wait_critical_only: bool = True,
    ) -> bool:
        """
        自适应等待：根据页面类型自动选择策略

        策略选择逻辑：
        1. 如果有 selector，优先等待 selector
        2. 否则等待网络空闲（默认只等待关键请求）
        3. 最后等待内容稳定

        Args:
            wait_critical_only: 是否只等待关键请求（忽略静态资源），默认 True
        """
        timeout = timeout or self.get_effective_timeout()
        deadline = time.time() + timeout

        # 1. 等待选择器（如果提供）
        if selector:
            logger.debug("自适应等待：先等待选择器")
            result = await self._wait_selector(selector, timeout=timeout, visible=True)
            if result:
                return True

        # 2. 等待网络空闲（优先只等待关键请求）
        if network_idle and time.time() < deadline:
            logger.debug("自适应等待：等待网络空闲（关键请求）")
            remaining = deadline - time.time()
            result = await self._wait_network_idle(
                idle_timeout=0.3, timeout=remaining, wait_critical_only=wait_critical_only
            )
            if result:
                return True

        # 3. 等待内容稳定
        if content_stable and time.time() < deadline:
            logger.debug("自适应等待：等待内容稳定")
            remaining = deadline - time.time()
            result = await self._wait_stable(stable_count=2, timeout=remaining)
            if result:
                return True

        return False

    async def _wait_condition(
        self,
        condition: Callable,
        timeout: float = None,
        check_interval: float = None,
    ) -> bool:
        """
        条件等待：等待自定义条件满足

        Args:
            condition: 返回 bool 的异步函数
            timeout: 超时时间
            check_interval: 检查间隔
        """
        timeout = timeout or self.config.timeout
        check_interval = check_interval or self.config.check_interval

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

    async def _wait_parallel(
        self,
        conditions: List[Union[str, dict]],
        timeout: float = None,
    ) -> bool:
        """
        并行等待：同时等待多个条件

        Args:
            conditions: 条件列表，可以是字符串（选择器）或字典（{strategy, **kwargs}）
        """
        timeout = timeout or self.config.timeout
        deadline = time.time() + timeout

        async def _check_condition(cond):
            if isinstance(cond, str):
                return await self._wait_selector(cond, timeout=timeout)
            elif isinstance(cond, dict):
                strategy = cond.get("strategy", "selector")
                kwargs = {k: v for k, v in cond.items() if k != "strategy"}
                handler = getattr(self, f"_wait_{strategy}", None)
                if handler:
                    return await handler(**kwargs, timeout=timeout)
            return False

        # 并行检查所有条件
        tasks = [_check_condition(c) for c in conditions]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # 所有条件都满足才返回 True
        return all(r is True for r in results if not isinstance(r, Exception))

    async def _wait_retry(
        self,
        strategy: str,
        max_retries: int = None,
        backoff_factor: float = None,
        timeout: float = None,
        **kwargs,
    ) -> bool:
        """
        带退避的重试等待

        Args:
            strategy: 底层等待策略
            max_retries: 最大重试次数
            backoff_factor: 退避因子
            timeout: 每次重试的超时时间
        """
        max_retries = max_retries or self.config.max_retries
        backoff_factor = backoff_factor or self.config.backoff_factor
        timeout = timeout or self.get_effective_timeout()

        handler = getattr(self, f"_wait_{strategy}", None)
        if not handler:
            raise ValueError(f"未知的等待策略: {strategy}")

        for attempt in range(max_retries):
            logger.debug(f"重试等待 {strategy}，第 {attempt + 1}/{max_retries} 次")
            result = await handler(timeout=timeout, **kwargs)
            if result:
                return True
            if attempt < max_retries - 1:
                wait_time = timeout * (backoff_factor ** attempt)
                logger.debug(f"等待 {wait_time:.2f}s 后重试")
                await asyncio.sleep(wait_time)

        return False

    async def _wait_dom_stable(self, timeout: float = None) -> bool:
        """
        等待 DOM 结构稳定（无大规模 DOM 变化）

        通过比较 DOM 快照来判断页面是否稳定
        """
        timeout = timeout or self.config.timeout
        previous_hash = None
        stable_count = 0

        deadline = time.time() + timeout
        while time.time() < deadline:
            # 获取 DOM 快照哈希
            js = """(() => {
                const html = document.documentElement.outerHTML;
                let hash = 0;
                for (let i = 0; i < html.length; i++) {
                    hash = ((hash << 5) - hash) + html.charCodeAt(i);
                    hash = hash & hash;
                }
                return hash.toString(16);
            })()"""
            current_hash = await self.session.eval_js(js)

            if previous_hash is not None:
                if current_hash == previous_hash:
                    stable_count += 1
                    if stable_count >= 3:
                        logger.debug("DOM 结构稳定检测通过")
                        return True
                else:
                    stable_count = 0
                    logger.debug("DOM 结构发生变化，重置稳定计数")

            previous_hash = current_hash
            await asyncio.sleep(0.5)

        return False

    async def _wait_data_loaded(self, timeout: float = None) -> bool:
        """
        等待数据加载完成（检查 AJAX/Fetch 请求）

        通过监听 XHR/Fetch 请求来判断数据是否加载完成
        """
        timeout = timeout or self.config.timeout

        self._register_network_events()

        deadline = time.time() + timeout
        last_data_request = 0

        while time.time() < deadline:
            # 检查是否有活跃的 XHR/Fetch 请求
            active = await self._get_active_xhr_fetch()

            if active == 0:
                # 检查最近是否有数据请求
                if time.time() - last_data_request > 1.0:
                    logger.debug("数据加载完成")
                    return True
            else:
                last_data_request = time.time()
                logger.debug(f"活跃数据请求数: {active}")

            await asyncio.sleep(0.3)

        return False

    async def _wait_animation(self, selector: str = "*", timeout: float = None) -> bool:
        """
        等待 CSS 动画/过渡完成

        Args:
            selector: 元素选择器
            timeout: 超时时间
        """
        timeout = timeout or self.config.timeout
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
        deadline = time.time() + timeout
        while time.time() < deadline:
            result = await self.session.eval_js(js_code)
            if result:
                logger.debug("CSS 动画/过渡等待完成")
                return True
            await asyncio.sleep(self.config.check_interval)
        return False

    async def _wait_font(self, timeout: float = None) -> bool:
        """
        等待 Web 字体加载完成

        Args:
            timeout: 超时时间
        """
        timeout = timeout or self.config.timeout
        js_code = '''
        (function() {
            if (!document.fonts || document.fonts.ready === undefined) return true;
            return document.fonts.ready.then(() => true).catch(() => true);
        })();
        '''
        try:
            result = await asyncio.wait_for(self.session.eval_js(js_code), timeout=timeout)
            logger.debug("Web 字体加载完成")
            return bool(result)
        except asyncio.TimeoutError:
            return False

    async def _wait_image(self, selector: str = "img", timeout: float = None) -> int:
        """
        等待图片加载完成

        Args:
            selector: 图片选择器
            timeout: 超时时间

        Returns:
            int: 已加载的图片数量
        """
        timeout = timeout or self.config.timeout
        js_code = f'''
        (function() {{
            const images = document.querySelectorAll('{selector}');
            if (images.length === 0) return 0;
            let loaded = 0;
            images.forEach(img => {{
                if (img.complete && img.naturalWidth > 0) {{
                    loaded++;
                }}
            }});
            return loaded;
        }})();
        '''
        deadline = time.time() + timeout
        while time.time() < deadline:
            loaded = await self.session.eval_js(js_code)
            total = await self.session.eval_js(f"document.querySelectorAll('{selector}').length")
            if loaded == total and total > 0:
                logger.debug(f"图片加载完成: {loaded}/{total}")
                return loaded
            await asyncio.sleep(self.config.check_interval)
        return await self.session.eval_js(js_code)

    async def _wait_iframe(self, selector: str = "iframe", timeout: float = None) -> bool:
        """
        等待 iframe 内容加载完成

        Args:
            selector: iframe 选择器
            timeout: 超时时间
        """
        timeout = timeout or self.config.timeout
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
        deadline = time.time() + timeout
        while time.time() < deadline:
            result = await self.session.eval_js(js_code)
            if result:
                logger.debug("iframe 加载完成")
                return True
            await asyncio.sleep(self.config.check_interval)
        return False

    async def _wait_shadow_dom(self, selector: str, timeout: float = None) -> bool:
        """
        等待 Shadow DOM 附加完成

        Args:
            selector: 宿主元素选择器
            timeout: 超时时间
        """
        timeout = timeout or self.config.timeout
        js_code = f'''
        (function() {{
            const el = document.querySelector('{selector}');
            if (!el) return false;
            return !!el.shadowRoot;
        }})();
        '''
        deadline = time.time() + timeout
        while time.time() < deadline:
            result = await self.session.eval_js(js_code)
            if result:
                logger.debug(f"Shadow DOM 等待完成: {selector}")
                return True
            await asyncio.sleep(self.config.check_interval)
        return False

    async def get_pending_requests(self) -> int:
        """获取当前 pending 请求数（通过 CDP Network 事件）"""
        self._register_network_events()
        return self._pending_requests
    
    async def get_active_xhr_fetch(self) -> int:
        """获取活跃的 XHR/Fetch 请求数（通过 CDP Network 事件）"""
        self._register_network_events()
        return self._active_xhr_fetch


# 兼容旧接口（保留 _get_pending_requests 和 _get_active_xhr_fetch 别名）
SmartWait._get_pending_requests = SmartWait.get_pending_requests
SmartWait._get_active_xhr_fetch = SmartWait.get_active_xhr_fetch
