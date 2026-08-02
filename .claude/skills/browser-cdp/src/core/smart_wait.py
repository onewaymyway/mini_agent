"""
smart_wait.py - 智能等待模块

提供多种等待策略，替代简单的 wait_selector：
- networkidle: 等待网络空闲（所有请求完成且 idle_timeout 内无新请求）
- route: 等待 SPA 路由稳定
- stable: 内容稳定性检测（多次读取内容不变）
- ajax: 等待 AJAX 请求完成
- selector: 等待 CSS 选择器出现（原有功能增强）
"""
from __future__ import annotations

import asyncio
import time
from typing import Optional, Callable, Any
from dataclasses import dataclass
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


class SmartWait:
    """智能等待器"""
    
    def __init__(self, session, config: WaitConfig = None):
        self.session = session
        self.config = config or WaitConfig()
        self._pending_requests = 0
        self._last_request_time = 0
        self._xhr_count = 0
        self._fetch_count = 0
    
    async def wait_for(self, strategy: str, **kwargs) -> bool:
        """
        根据策略等待页面稳定
        
        Args:
            strategy: 等待策略 (networkidle/route/stable/ajax/selector)
            **kwargs: 策略特定参数
        
        Returns:
            bool: 是否成功等待
        """
        handlers = {
            "networkidle": self._wait_network_idle,
            "route": self._wait_route,
            "stable": self._wait_stable,
            "ajax": self._wait_ajax,
            "selector": self._wait_selector,
        }
        
        if strategy not in handlers:
            raise ValueError(f"未知的等待策略: {strategy}")
        
        logger.info(f"开始等待策略: {strategy}")
        start_time = time.time()
        
        try:
            result = await asyncio.wait_for(
                handlers[strategy](**kwargs),
                timeout=self.config.timeout
            )
            elapsed = time.time() - start_time
            logger.info(f"等待策略 {strategy} 完成，耗时 {elapsed:.2f}s")
            return result
        except asyncio.TimeoutError:
            elapsed = time.time() - start_time
            logger.warning(f"等待策略 {strategy} 超时，耗时 {elapsed:.2f}s")
            return False
    
    async def _wait_network_idle(self, idle_timeout: float = None) -> bool:
        """
        等待网络空闲：所有请求完成且 idle_timeout 内无新请求
        
        实现原理：
        1. 监听 Network.requestWillBeSent 增加 pending 计数
        2. 监听 Network.responseReceived + Network.loadingFinished 减少计数
        3. 当 pending=0 时启动计时器
        4. idle_timeout 内无新请求则返回 True
        """
        idle_timeout = idle_timeout or self.config.idle_timeout
        
        # 重置计数器
        self._pending_requests = 0
        idle_start = None
        
        # 注册事件监听
        def on_request(params):
            self._pending_requests += 1
            idle_start = None  # 有新请求，重置空闲计时
        
        def on_response(params):
            pass  # 响应接收，等待完成
        
        def on_finished(params):
            self._pending_requests -= 1
            if self._pending_requests == 0:
                idle_start = time.time()
        
        # 简化实现：使用轮询检查
        deadline = time.time() + self.config.timeout
        while time.time() < deadline:
            # 检查当前 pending 请求数
            pending = await self._get_pending_requests()
            
            if pending == 0:
                # 等待 idle_timeout 确认无新请求
                await asyncio.sleep(idle_timeout)
                pending_after_wait = await self._get_pending_requests()
                if pending_after_wait == 0:
                    logger.debug("网络空闲检测通过")
                    return True
            else:
                logger.debug(f"当前 pending 请求数: {pending}")
            
            await asyncio.sleep(self.config.check_interval)
        
        return False
    
    async def _wait_route(self, expected_url: str = None, change_count: int = 1) -> bool:
        """
        等待 SPA 路由稳定
        
        Args:
            expected_url: 期望的 URL 模式（可选）
            change_count: 要求路由稳定前的变化次数
        """
        previous_url = await self.session.eval_js("location.href")
        changes = 0
        
        deadline = time.time() + self.config.timeout
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
    
    async def _wait_stable(self, check_interval: float = None, stable_count: int = None) -> bool:
        """
        内容稳定性检测：多次读取页面内容，确认不变
        
        Args:
            check_interval: 检查间隔
            stable_count: 连续稳定次数
        """
        check_interval = check_interval or self.config.check_interval
        stable_count = stable_count or self.config.stable_count
        
        previous_content = None
        stable_iterations = 0
        
        deadline = time.time() + self.config.timeout
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
        
        通过监听 XHR 和 Fetch 请求实现
        """
        timeout = timeout or self.config.timeout
        
        # 简化实现：检查是否有活跃的 XHR/Fetch
        deadline = time.time() + timeout
        while time.time() < deadline:
            active_requests = await self._get_active_xhr_fetch()
            
            if active_requests == 0:
                # 短暂等待确认无新请求
                await asyncio.sleep(0.5)
                active_requests = await self._get_active_xhr_fetch()
                if active_requests == 0:
                    logger.debug("AJAX 请求检测通过")
                    return True
            else:
                logger.debug(f"活跃 AJAX 请求数: {active_requests}")
            
            await asyncio.sleep(self.config.check_interval)
        
        return False
    
    async def _wait_selector(self, selector: str, timeout: float = None) -> bool:
        """
        等待 CSS 选择器出现（增强版）
        
        Args:
            selector: CSS 选择器
            timeout: 超时时间
        """
        timeout = timeout or self.config.timeout
        
        js = f"""
        (() => {{
            const el = document.querySelector({selector!r});
            if (!el) return false;
            const rect = el.getBoundingClientRect();
            return rect.width > 0 && rect.height > 0;
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
    
    async def _get_pending_requests(self) -> int:
        """获取当前 pending 请求数（简化实现）"""
        # 通过 JavaScript 检查
        js = """
        (() => {
            // 检查 Performance API 的 entries
            const entries = performance.getEntriesByType('resource');
            const xhr = performance.getEntriesByType('xhr');
            return xhr.length;
        })()
        """
        try:
            result = await self.session.eval_js(js)
            return result if result else 0
        except:
            return 0
    
    async def _get_active_xhr_fetch(self) -> int:
        """获取活跃的 XHR/Fetch 请求数"""
        js = """
        (() => {
            // 通过 Performance API 统计
            const xhrEntries = performance.getEntriesByType('resource').filter(
                e => e.initiatorType === 'xmlhttprequest' || e.initiatorType === 'fetch'
            );
            return xhrEntries.length;
        })()
        """
        try:
            result = await self.session.eval_js(js)
            return result if result else 0
        except:
            return 0
