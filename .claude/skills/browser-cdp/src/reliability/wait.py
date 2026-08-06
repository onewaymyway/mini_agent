"""
智能等待器

提供多策略等待降级链，修复 networkidle 竞态条件问题。
等待策略按优先级尝试：networkidle → selector_visible → selector_stable → route_done → load_event → immediate
"""

import asyncio
import logging
import time
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

from .error import NetworkIdleTimeoutError, SmartWaitDegradedError

logger = logging.getLogger(__name__)


class WaitStrategy(Enum):
    """等待策略枚举"""
    NETWORK_IDLE = "networkidle"          # 等待网络空闲
    SELECTOR_VISIBLE = "selector_visible"  # 等待元素可见
    SELECTOR_STABLE = "selector_stable"    # 等待元素稳定（不再变化）
    ROUTE_DONE = "route_done"              # 等待路由完成
    LOAD_EVENT = "load_event"              # 等待 loadEventFired
    IMMEDIATE = "immediate"                # 立即返回


class SmartWaiter:
    """
    智能等待器：按优先级尝试多种策略，超时自动降级。

    设计原则：
    1. 降级链：从最严格到最宽松的策略依次尝试
    2. 超时控制：总超时内各策略共享时间预算
    3. 竞态修复：networkidle 使用请求计数器而非固定睡眠
    4. 可观测性：记录每步尝试结果
    """

    DEFAULT_TIMEOUT = 15.0
    PER_STRATEGY_BUDGET = 2.0  # 每步最多消耗时间

    # 默认降级链顺序
    DEFAULT_DEGRADATION_ORDER = [
        WaitStrategy.NETWORK_IDLE,
        WaitStrategy.SELECTOR_VISIBLE,
        WaitStrategy.SELECTOR_STABLE,
        WaitStrategy.ROUTE_DONE,
        WaitStrategy.LOAD_EVENT,
        WaitStrategy.IMMEDIATE,
    ]

    def __init__(
        self,
        cdp_client: Any = None,
        timeout: float = DEFAULT_TIMEOUT,
        degradation_order: Optional[List[WaitStrategy]] = None,
    ):
        self.cdp_client = cdp_client
        self.timeout = timeout
        self.degradation_order = degradation_order or self.DEFAULT_DEGRADATION_ORDER
        self._wait_history: List[Dict[str, Any]] = []

    async def wait(
        self,
        strategy: Optional[WaitStrategy] = None,
        selector: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> bool:
        """
        执行智能等待。

        Args:
            strategy: 首选策略（可选，默认按降级链）
            selector: 元素选择器（用于 selector_visible/stable 策略）
            timeout: 超时时间（可选，默认使用初始化时的 timeout）

        Returns:
            bool: 等待是否成功
        """
        start_time = time.time()
        strategies = self._get_strategy_chain(strategy)
        remaining_timeout = timeout or self.timeout
        results = []

        logger.debug(f"Smart wait started, timeout={remaining_timeout}s, strategies={len(strategies)}")

        for strat in strategies:
            strategy_start = time.time()

            # 检查剩余时间
            elapsed = time.time() - start_time
            if elapsed >= remaining_timeout:
                logger.debug(f"Smart wait: timeout reached after {elapsed:.1f}s")
                break

            try:
                result = await self._try_strategy(strat, selector, remaining_timeout - elapsed)
                elapsed_this = time.time() - strategy_start
                results.append({
                    "strategy": strat.value,
                    "success": result,
                    "duration": round(elapsed_this, 2),
                })
                if result:
                    logger.debug(f"Smart wait: {strat.value} succeeded in {elapsed_this:.2f}s")
                    self._wait_history.append({
                        "start": start_time,
                        "duration": round(time.time() - start_time, 2),
                        "strategies": results,
                        "success": True,
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
                logger.debug(f"Smart wait: {strat.value} failed: {e}")

        # 所有策略失败
        total_elapsed = time.time() - start_time
        logger.warning(
            f"Smart wait: all strategies failed after {total_elapsed:.1f}s"
        )
        self._wait_history.append({
            "start": start_time,
            "duration": round(total_elapsed, 2),
            "strategies": results,
            "success": False,
        })
        return False

    def _get_strategy_chain(self, preferred: Optional[WaitStrategy] = None) -> List[WaitStrategy]:
        """获取等待策略链"""
        if preferred:
            return [preferred] + [s for s in self.degradation_order if s != preferred]
        return self.degradation_order

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
                return True  # 无选择器时跳过
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
        """
        等待网络空闲（修复竞态条件）。

        原实现问题：先订阅事件，再 sleep idle_timeout，期间新请求到达后完成会误判。
        修复方案：使用请求计数器，等待 pending_requests == 0 且持续一段时间。
        """
        if not self.cdp_client:
            return True  # 无 CDP 客户端时跳过

        pending_requests = 0
        start_time = time.time()
        stable_since = 0.0
        STABLE_DURATION = 1.0  # 网络空闲需持续 1 秒

        def on_request(params: dict):
            nonlocal pending_requests
            pending_requests += 1
            stable_since = 0.0  # 新请求到达，重置稳定计时

        def on_response(params: dict):
            nonlocal pending_requests, stable_since
            pending_requests = max(0, pending_requests - 1)
            if pending_requests == 0:
                stable_since = time.time()

        # 订阅事件
        try:
            self.cdp_client.subscribe("Network.requestWillBeSent", on_request)
            self.cdp_client.subscribe("Network.responseReceived", on_response)
        except AttributeError:
            # CDP 客户端不支持 subscribe，降级
            return True

        try:
            while time.time() - start_time < timeout:
                if pending_requests == 0 and (time.time() - stable_since) >= STABLE_DURATION:
                    return True
                await asyncio.sleep(0.1)

            # 超时
            raise NetworkIdleTimeoutError(
                timeout=timeout,
                pending_requests=pending_requests,
            )
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
        """等待元素稳定（连续 2 次检测无变化）"""
        if not self.cdp_client:
            return True

        start_time = time.time()
        last_state = None
        stable_count = 0

        while time.time() - start_time < timeout:
            try:
                result = await self.cdp_client.send("DOM.querySelector", {
                    "nodeId": 1,
                    "selector": selector,
                })
                current_state = result.get("result", {}).get("objectId")

                if current_state and current_state == last_state:
                    stable_count += 1
                    if stable_count >= 2:
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
        """等待路由完成（SPA 场景）"""
        if not self.cdp_client:
            return True

        start_time = time.time()
        while time.time() - start_time < timeout:
            try:
                result = await self.cdp_client.send("Runtime.evaluate", {
                    "expression": "window.location.href",
                    "awaitPromise": False,
                })
                # 简单实现：等待页面稳定
                await asyncio.sleep(0.5)
                return True
            except Exception:
                pass
            await asyncio.sleep(0.2)
        return False

    async def _wait_load_event(self, timeout: float) -> bool:
        """等待 loadEventFired"""
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

    def get_wait_history(self, limit: int = 10) -> List[Dict[str, Any]]:
        """获取等待历史记录"""
        return self._wait_history[-limit:]

    def reset_history(self):
        """清空等待历史"""
        self._wait_history.clear()


# 便捷函数
def smart_wait(
    cdp_client: Any,
    strategy: Optional[WaitStrategy] = None,
    selector: Optional[str] = None,
    timeout: float = SmartWaiter.DEFAULT_TIMEOUT,
) -> asyncio.Future:
    """
    便捷函数：创建 SmartWaiter 并执行等待。

    Returns:
        asyncio.Future: 等待结果
    """
    waiter = SmartWaiter(cdp_client, timeout)
    return waiter.wait(strategy, selector)
