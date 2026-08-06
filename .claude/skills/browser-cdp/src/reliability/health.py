"""
连接健康检查器

提供 CDP 连接健康检查、自动重连、连接池集成等能力。
"""

import asyncio
import logging
import time
from typing import Any, Dict, Optional

from .error import CDPConnectionLostError

logger = logging.getLogger(__name__)


class ConnectionHealthChecker:
    """
    CDP 连接健康检查器。

    功能：
    1. 定期健康检查（ping Runtime.evaluate）
    2. 自动重连（失败后尝试重建连接）
    3. 连接状态追踪（延迟、失败次数、重连次数）
    """

    def __init__(
        self,
        cdp_client: Any,
        ping_interval: float = 10.0,
        ping_timeout: float = 5.0,
        max_reconnect_attempts: int = 3,
        reconnect_delay: float = 2.0,
    ):
        self.cdp_client = cdp_client
        self.ping_interval = ping_interval
        self.ping_timeout = ping_timeout
        self.max_reconnect_attempts = max_reconnect_attempts
        self.reconnect_delay = reconnect_delay

        # 状态追踪
        self._last_ping_time = 0.0
        self._last_ping_latency = 0.0
        self._is_healthy = True
        self._reconnect_attempts = 0
        self._total_failures = 0
        self._total_successes = 0
        self._last_error: Optional[str] = None

        # 后台任务
        self._health_task: Optional[asyncio.Task] = None
        self._running = False

    async def health_check(self) -> Dict[str, Any]:
        """
        执行一次健康检查。

        Returns:
            dict: 健康检查结果
                - healthy: bool
                - latency_ms: float
                - error: Optional[str]
        """
        start_time = time.time()
        try:
            result = await asyncio.wait_for(
                self.cdp_client.send("Runtime.evaluate", {"expression": "1+1"}),
                timeout=self.ping_timeout,
            )
            latency = (time.time() - start_time) * 1000  # ms
            self._is_healthy = True
            self._last_ping_latency = latency
            self._last_ping_time = time.time()
            self._total_successes += 1
            self._last_error = None

            logger.debug(f"Health check OK, latency={latency:.1f}ms")
            return {
                "healthy": True,
                "latency_ms": round(latency, 2),
                "error": None,
            }
        except Exception as e:
            self._is_healthy = False
            self._total_failures += 1
            self._last_error = str(e)
            logger.warning(f"Health check failed: {e}")
            return {
                "healthy": False,
                "latency_ms": 0,
                "error": str(e),
            }

    async def auto_reconnect(self) -> bool:
        """
        自动重连：尝试重建 CDP 连接。

        Returns:
            bool: 重连是否成功
        """
        if self._reconnect_attempts >= self.max_reconnect_attempts:
            logger.error(f"Max reconnect attempts ({self.max_reconnect_attempts}) reached")
            return False

        self._reconnect_attempts += 1
        logger.info(f"Attempting reconnect ({self._reconnect_attempts}/{self.max_reconnect_attempts})")

        try:
            # 尝试调用重连方法
            if hasattr(self.cdp_client, 'reconnect'):
                await self.cdp_client.reconnect()
            elif hasattr(self.cdp_client, '_reconnect'):
                await self.cdp_client._reconnect()
            else:
                # 尝试重新创建 session
                logger.warning("No reconnect method found, trying to recreate session")
                return False

            self._reconnect_attempts = 0
            self._is_healthy = True
            self._last_error = None
            logger.info("Reconnect successful")
            return True
        except Exception as e:
            logger.error(f"Reconnect failed: {e}")
            return False

    def get_status(self) -> Dict[str, Any]:
        """获取连接状态"""
        return {
            "healthy": self._is_healthy,
            "last_ping_latency_ms": round(self._last_ping_latency * 1000, 2) if self._last_ping_latency else 0,
            "last_ping_time": self._last_ping_time,
            "reconnect_attempts": self._reconnect_attempts,
            "max_reconnect_attempts": self.max_reconnect_attempts,
            "total_failures": self._total_failures,
            "total_successes": self._total_successes,
            "last_error": self._last_error,
            "uptime_seconds": time.time() - self._last_ping_time if self._last_ping_time else 0,
        }

    async def start_background_check(self):
        """启动后台健康检查任务"""
        if self._running:
            return
        self._running = True
        self._health_task = asyncio.create_task(self._health_check_loop())
        logger.info("Background health check started")

    async def stop_background_check(self):
        """停止后台健康检查任务"""
        self._running = False
        if self._health_task:
            self._health_task.cancel()
            try:
                await self._health_task
            except asyncio.CancelledError:
                pass
            self._health_task = None
        logger.info("Background health check stopped")

    async def _health_check_loop(self):
        """后台健康检查循环"""
        while self._running:
            try:
                result = await self.health_check()
                if not result["healthy"]:
                    logger.warning("Connection unhealthy, attempting reconnect")
                    reconnected = await self.auto_reconnect()
                    if not reconnected:
                        raise CDPConnectionLostError(details=self.get_status())
            except Exception as e:
                logger.error(f"Health check loop error: {e}")
            finally:
                await asyncio.sleep(self.ping_interval)

    def reset_stats(self):
        """重置统计信息"""
        self._total_failures = 0
        self._total_successes = 0
        self._reconnect_attempts = 0
        self._last_error = None


class ConnectionPoolHealthChecker(ConnectionHealthChecker):
    """
    连接池健康检查器：集成到 cdp_connection_pool.py 中。

    在获取连接前自动执行健康检查，不健康则尝试重连。
    """

    def __init__(self, connection_pool: Any, **kwargs):
        super().__init__(connection_pool, **kwargs)
        self.connection_pool = connection_pool

    async def get_healthy_session(self, url: str) -> Any:
        """
        获取健康会话：先检查健康，不健康则重连。

        Args:
            url: 目标 URL

        Returns:
            CDP 会话实例

        Raises:
            CDPConnectionLostError: 重连失败
        """
        # 从池中获取或创建新连接
        session = await self.connection_pool.get_session(url)

        # 健康检查
        health = await self.health_check()
        if not health["healthy"]:
            logger.warning(f"Session for {url} unhealthy, attempting reconnect")
            reconnected = await self.auto_reconnect()
            if not reconnected:
                raise CDPConnectionLostError(details={
                    "url": url,
                    "health": health,
                    "status": self.get_status(),
                })

        return session

    async def check_all_sessions(self) -> Dict[str, Dict[str, Any]]:
        """检查池中所有会话的健康状态"""
        results = {}
        for url, session in self.connection_pool.sessions.items():
            checker = ConnectionHealthChecker(session)
            health = await checker.health_check()
            results[url] = {
                **health,
                "status": checker.get_status(),
            }
        return results
