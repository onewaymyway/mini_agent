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
        self._active_xhr_fetch = 0
        self._network_enabled = False
        self._xhr_callbacks = []
    
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
        finally:
            self._cleanup_network_events()
    
    def _on_request_will_be_sent(self, params: dict) -> None:
        """CDP Network.requestWillBeSent 回调"""
        self._pending_requests += 1
        initiator = params.get('request', {}).get('initiator', {})
        if initiator.get('type') in ('xhr', 'fetch'):
            self._active_xhr_fetch += 1
        for cb in self._xhr_callbacks:
            cb('request', params)
    
    def _on_loading_finished(self, params: dict) -> None:
        """CDP Network.loadingFinished 回调"""
        self._pending_requests = max(0, self._pending_requests - 1)
        for cb in self._xhr_callbacks:
            cb('finish', params)
    
    def _on_response_received(self, params: dict) -> None:
        """CDP Network.responseReceived 回调"""
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
    
    async def _wait_network_idle(self, idle_timeout: float = None) -> bool:
        """
        等待网络空闲：所有请求完成且 idle_timeout 内无新请求
        
        实现原理：
        1. 启用 CDP Network domain
        2. 监听 Network.requestWillBeSent 增加 pending 计数
        3. 监听 Network.loadingFinished 减少计数
        4. 当 pending=0 时等待 idle_timeout 确认无新请求
        """
        idle_timeout = idle_timeout or self.config.idle_timeout
        
        self._register_network_events()
        
        deadline = time.time() + self.config.timeout
        while time.time() < deadline:
            pending = self._pending_requests
            
            if pending == 0:
                # 等待 idle_timeout 确认无新请求
                await asyncio.sleep(idle_timeout)
                if self._pending_requests == 0:
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
