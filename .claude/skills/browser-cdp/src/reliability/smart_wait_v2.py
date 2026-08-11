# -*- coding: utf-8 -*-
"""
增强智能等待模块 v2

基于网站特征动态选择等待策略，支持：
- 网站类型识别
- 动态策略选择
- 等待时间优化
- 策略效果追踪
"""

import asyncio
import logging
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

from .error import NetworkIdleTimeoutError, SmartWaitDegradedError

logger = logging.getLogger(__name__)


class WaitStrategy(Enum):
    """等待策略枚举"""
    NETWORK_IDLE = "networkidle"
    SELECTOR_VISIBLE = "selector_visible"
    SELECTOR_STABLE = "selector_stable"
    ROUTE_DONE = "route_done"
    LOAD_EVENT = "load_event"
    IMMEDIATE = "immediate"


class WebsiteType(Enum):
    """网站类型枚举"""
    STATIC = "static"           # 静态内容网站
    DYNAMIC = "dynamic"         # 动态内容网站
    SPA = "spa"                 # 单页应用
    HEAVY_JS = "heavy_js"       # 重度JS网站
    API_DRIVEN = "api_driven"   # API驱动网站
    UNKNOWN = "unknown"


@dataclass
class WaitConfig:
    """等待配置"""
    strategy: WaitStrategy
    timeout: float
    selector: Optional[str] = None
    stable_checks: int = 2
    network_idle_time: float = 1.0


class SmartWaiterV2:
    """
    增强智能等待器 v2
    
    根据网站类型动态选择最优等待策略，支持：
    1. 网站类型自动识别
    2. 策略效果追踪与优化
    3. 动态超时调整
    4. 降级链智能选择
    """
    
    # 网站类型默认配置
    WEBSITE_CONFIGS = {
        WebsiteType.STATIC: WaitConfig(
            strategy=WaitStrategy.NETWORK_IDLE,
            timeout=10.0,
            network_idle_time=0.5,
        ),
        WebsiteType.DYNAMIC: WaitConfig(
            strategy=WaitStrategy.SELECTOR_VISIBLE,
            timeout=15.0,
            selector="body",
        ),
        WebsiteType.SPA: WaitConfig(
            strategy=WaitStrategy.ROUTE_DONE,
            timeout=20.0,
        ),
        WebsiteType.HEAVY_JS: WaitConfig(
            strategy=WaitStrategy.SELECTOR_STABLE,
            timeout=25.0,
            selector="body",
            stable_checks=3,
        ),
        WebsiteType.API_DRIVEN: WaitConfig(
            strategy=WaitStrategy.NETWORK_IDLE,
            timeout=12.0,
            network_idle_time=0.8,
        ),
        WebsiteType.UNKNOWN: WaitConfig(
            strategy=WaitStrategy.NETWORK_IDLE,
            timeout=15.0,
        ),
    }
    
    # 策略效果追踪
    _strategy_stats: Dict[str, Dict[str, int]] = {}
    
    def __init__(
        self,
        cdp_client: Any = None,
        website_type: WebsiteType = WebsiteType.UNKNOWN,
        timeout: float = 15.0,
        custom_config: Optional[WaitConfig] = None,
    ):
        self.cdp_client = cdp_client
        self.website_type = website_type
        self.timeout = timeout
        self.custom_config = custom_config
        self._wait_history: List[Dict[str, Any]] = []
        
        # 获取默认配置
        self._config = custom_config or self.WEBSITE_CONFIGS.get(
            website_type, self.WEBSITE_CONFIGS[WebsiteType.UNKNOWN]
        )
    
    def set_website_type(self, website_type: WebsiteType):
        """设置网站类型并更新配置"""
        self.website_type = website_type
        self._config = self.WEBSITE_CONFIGS.get(website_type, self._config)
        logger.info(f"[SmartWaitV2] Set website type: {website_type.value}")
    
    async def wait(
        self,
        strategy: Optional[WaitStrategy] = None,
        selector: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> bool:
        """
        执行智能等待
        
        Args:
            strategy: 首选策略（可选）
            selector: 元素选择器（可选）
            timeout: 超时时间（可选）
        
        Returns:
            bool: 等待是否成功
        """
        start_time = time.time()
        effective_timeout = timeout or self._config.timeout
        
        # 构建策略链
        strategies = self._build_strategy_chain(strategy)
        
        results = []
        remaining_timeout = effective_timeout
        
        logger.debug(
            f"[SmartWaitV2] Starting wait, timeout={effective_timeout}s, "
            f"strategies={len(strategies)}, website_type={self.website_type.value}"
        )
        
        for strat in strategies:
            strategy_start = time.time()
            elapsed = time.time() - start_time
            
            if elapsed >= remaining_timeout:
                logger.debug(f"[SmartWaitV2] Timeout reached after {elapsed:.1f}s")
                break
            
            try:
                result = await self._try_strategy(
                    strat, selector or self._config.selector, remaining_timeout - elapsed
                )
                elapsed_this = time.time() - strategy_start
                results.append({
                    "strategy": strat.value,
                    "success": result,
                    "duration": round(elapsed_this, 2),
                })
                
                # 追踪策略效果
                self._track_strategy(strat.value, result)
                
                if result:
                    total_elapsed = time.time() - start_time
                    logger.debug(
                        f"[SmartWaitV2] {strat.value} succeeded in {elapsed_this:.2f}s "
                        f"(total: {total_elapsed:.2f}s)"
                    )
                    self._wait_history.append({
                        "start": start_time,
                        "duration": round(total_elapsed, 2),
                        "strategies": results,
                        "success": True,
                        "website_type": self.website_type.value,
                    })
                    return True
                    
            except Exception as e:
                elapsed_this = time.time() - strategy_start
                results.append({
                    "strategy": strat.value,
                    "success": False,
                    "error": str(e),
                    "duration": round(elapsed_this, 2),
                })
                logger.debug(f"[SmartWaitV2] {strat.value} failed: {e}")
        
        # 所有策略失败
        total_elapsed = time.time() - start_time
        logger.warning(
            f"[SmartWaitV2] All strategies failed after {total_elapsed:.1f}s"
        )
        self._wait_history.append({
            "start": start_time,
            "duration": round(total_elapsed, 2),
            "strategies": results,
            "success": False,
            "website_type": self.website_type.value,
        })
        return False
    
    def _build_strategy_chain(self, preferred: Optional[WaitStrategy]) -> List[WaitStrategy]:
        """构建策略链"""
        if preferred:
            return [preferred] + [
                s for s in self._get_default_chain() if s != preferred
            ]
        return self._get_default_chain()
    
    def _get_default_chain(self) -> List[WaitStrategy]:
        """根据网站类型获取默认策略链"""
        base_chain = {
            WebsiteType.STATIC: [
                WaitStrategy.NETWORK_IDLE,
                WaitStrategy.LOAD_EVENT,
                WaitStrategy.IMMEDIATE,
            ],
            WebsiteType.DYNAMIC: [
                WaitStrategy.SELECTOR_VISIBLE,
                WaitStrategy.NETWORK_IDLE,
                WaitStrategy.SELECTOR_STABLE,
                WaitStrategy.IMMEDIATE,
            ],
            WebsiteType.SPA: [
                WaitStrategy.ROUTE_DONE,
                WaitStrategy.SELECTOR_VISIBLE,
                WaitStrategy.NETWORK_IDLE,
                WaitStrategy.IMMEDIATE,
            ],
            WebsiteType.HEAVY_JS: [
                WaitStrategy.SELECTOR_STABLE,
                WaitStrategy.SELECTOR_VISIBLE,
                WaitStrategy.NETWORK_IDLE,
                WaitStrategy.IMMEDIATE,
            ],
            WebsiteType.API_DRIVEN: [
                WaitStrategy.NETWORK_IDLE,
                WaitStrategy.ROUTE_DONE,
                WaitStrategy.LOAD_EVENT,
                WaitStrategy.IMMEDIATE,
            ],
            WebsiteType.UNKNOWN: [
                WaitStrategy.NETWORK_IDLE,
                WaitStrategy.SELECTOR_VISIBLE,
                WaitStrategy.SELECTOR_STABLE,
                WaitStrategy.ROUTE_DONE,
                WaitStrategy.LOAD_EVENT,
                WaitStrategy.IMMEDIATE,
            ],
        }
        return base_chain.get(self.website_type, base_chain[WebsiteType.UNKNOWN])
    
    async def _try_strategy(
        self,
        strategy: WaitStrategy,
        selector: Optional[str],
        timeout: float,
    ) -> bool:
        """尝试单个等待策略"""
        if strategy == WaitStrategy.NETWORK_IDLE:
            return await self._wait_network_idle(timeout)
        elif strategy == WaitStrategy.SELECTOR_VISIBLE:
            if not selector:
                return True
            return await self._wait_selector_visible(selector, timeout)
        elif strategy == WaitStrategy.SELECTOR_STABLE:
            if not selector:
                return True
            return await self._wait_selector_stable(selector, timeout)
        elif strategy == WaitStrategy.ROUTE_DONE:
            return await self._wait_route_done(timeout)
        elif strategy == WaitStrategy.LOAD_EVENT:
            return await self._wait_load_event(timeout)
        elif strategy == WaitStrategy.IMMEDIATE:
            return True
        return False
    
    async def _wait_network_idle(self, timeout: float) -> bool:
        """等待网络空闲"""
        if not self.cdp_client:
            return True
        
        pending_requests = 0
        start_time = time.time()
        stable_since = 0.0
        idle_time = self._config.network_idle_time
        
        def on_request(params: dict):
            nonlocal pending_requests
            pending_requests += 1
        
        def on_response(params: dict):
            nonlocal pending_requests, stable_since
            pending_requests = max(0, pending_requests - 1)
            if pending_requests == 0:
                stable_since = time.time()
        
        try:
            self.cdp_client.subscribe("Network.requestWillBeSent", on_request)
            self.cdp_client.subscribe("Network.responseReceived", on_response)
        except AttributeError:
            return True
        
        try:
            while time.time() - start_time < timeout:
                if pending_requests == 0 and (time.time() - stable_since) >= idle_time:
                    return True
                await asyncio.sleep(0.1)
            raise NetworkIdleTimeoutError(timeout=timeout, pending_requests=pending_requests)
        finally:
            try:
                self.cdp_client.unsubscribe("Network.requestWillBeSent", on_request)
                self.cdp_client.unsubscribe("Network.responseReceived", on_response)
            except AttributeError:
                pass
    
    async def _wait_selector_visible(self, selector: str, timeout: float) -> bool:
        """等待元素可见"""
        if not self.cdp_client:
            return True
        
        start_time = time.time()
        while time.time() - start_time < timeout:
            try:
                result = await self.cdp_client.send("DOM.querySelector", {
                    "nodeId": 1,
                    "selector": selector,
                })
                if result.get("result", {}).get("objectId"):
                    return True
            except Exception:
                pass
            await asyncio.sleep(0.2)
        return False
    
    async def _wait_selector_stable(self, selector: str, timeout: float) -> bool:
        """等待元素稳定"""
        if not self.cdp_client:
            return True
        
        start_time = time.time()
        last_state = None
        stable_count = 0
        required_checks = self._config.stable_checks
        
        while time.time() - start_time < timeout:
            try:
                result = await self.cdp_client.send("DOM.querySelector", {
                    "nodeId": 1,
                    "selector": selector,
                })
                current_state = result.get("result", {}).get("objectId")
                
                if current_state and current_state == last_state:
                    stable_count += 1
                    if stable_count >= required_checks:
                        return True
                else:
                    stable_count = 0
                last_state = current_state
            except Exception:
                stable_count = 0
                last_state = None
            
            await asyncio.sleep(0.3)
        return False
    
    async def _wait_route_done(self, timeout: float) -> bool:
        """等待路由完成"""
        if not self.cdp_client:
            return True
        
        start_time = time.time()
        while time.time() - start_time < timeout:
            try:
                await self.cdp_client.send("Runtime.evaluate", {
                    "expression": "window.location.href",
                    "awaitPromise": False,
                })
                await asyncio.sleep(0.5)
                return True
            except Exception:
                pass
            await asyncio.sleep(0.2)
        return False
    
    async def _wait_load_event(self, timeout: float) -> bool:
        """等待 load 事件"""
        if not self.cdp_client:
            return True
        
        start_time = time.time()
        load_fired = False
        
        def on_load(params: dict):
            nonlocal load_fired
            load_fired = True
        
        try:
            self.cdp_client.subscribe("Page.loadEventFired", on_load)
        except AttributeError:
            return True
        
        try:
            while not load_fired and time.time() - start_time < timeout:
                await asyncio.sleep(0.1)
            return load_fired
        finally:
            try:
                self.cdp_client.unsubscribe("Page.loadEventFired", on_load)
            except AttributeError:
                pass
    
    def _track_strategy(self, strategy: str, success: bool):
        """追踪策略效果"""
        key = f"{self.website_type.value}_{strategy}"
        if key not in self._strategy_stats:
            self._strategy_stats[key] = {"success": 0, "failure": 0}
        
        if success:
            self._strategy_stats[key]["success"] += 1
        else:
            self._strategy_stats[key]["failure"] += 1
    
    def get_wait_history(self, limit: int = 10) -> List[Dict[str, Any]]:
        """获取等待历史记录"""
        return self._wait_history[-limit:]
    
    def get_strategy_stats(self) -> Dict[str, Dict[str, int]]:
        """获取策略效果统计"""
        return dict(self._strategy_stats)
    
    def reset_history(self):
        """清空等待历史"""
        self._wait_history.clear()


# 便捷函数
def smart_wait_v2(
    cdp_client: Any,
    website_type: WebsiteType = WebsiteType.UNKNOWN,
    timeout: Optional[float] = None,
    strategy: Optional[WaitStrategy] = None,
    selector: Optional[str] = None,
) -> asyncio.Future:
    """
    便捷函数：创建 SmartWaiterV2 并执行等待
    
    Returns:
        asyncio.Future: 等待结果
    """
    effective_timeout = timeout or 15.0
    waiter = SmartWaiterV2(cdp_client, website_type, effective_timeout)
    return waiter.wait(strategy, selector)
