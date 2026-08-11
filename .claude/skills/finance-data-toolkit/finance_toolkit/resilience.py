# -*- coding: utf-8 -*-
"""
Finance Data Toolkit - 容错机制

提供熔断器、降级策略、重试机制、限流器、健康检查器等容错功能。

使用示例：
    from finance_toolkit.resilience import CircuitBreaker, FallbackManager, RateLimiter, HealthChecker
    
    # 熔断器示例
    cb = CircuitBreaker("akshare", failure_threshold=5, reset_timeout=60)
    try:
        with cb.guard():
            data = fetch_data()
    except CircuitBreakerError:
        print("使用备用数据源")
    
    # 限流器示例
    rl = RateLimiter(max_calls=10, period=60)
    with rl.acquire():
        data = fetch_data()
    
    # 健康检查器示例
    hc = HealthChecker(sources=["akshare", "eastmoney"])
    status = hc.check_all()
"""

import asyncio
import time
import logging
from typing import List, Dict, Callable, Any, Optional, Tuple
from functools import wraps
from collections import defaultdict

from .exceptions import (
    CircuitBreakerError,
    SourceUnavailableError,
    FallbackError,
    SourceRateLimitedError,
    RateLimitError,
    ConnectionError,
    TimeoutError,
    SourceHealthError,
    DataNotFoundError,
    DataEmptyError,
)

logger = logging.getLogger(__name__)


class CircuitBreaker:
    """
    熔断器实现
    
    状态机：
    - CLOSED（正常）：请求正常通过，失败计数累加
    - OPEN（熔断）：直接拒绝请求，等待重置时间后进入 HALF_OPEN
    - HALF_OPEN（半开）：允许少量请求试探，成功则恢复 CLOSED，失败则继续 OPEN
    
    参数：
        source: 数据源名称
        failure_threshold: 失败阈值（达到此次数后触发熔断）
        reset_timeout: 重置超时时间（秒），OPEN 状态持续时间
        half_open_max_calls: 半开状态下的最大试探调用次数
    """
    
    def __init__(
        self,
        source: str,
        failure_threshold: int = 5,
        reset_timeout: int = 60,
        half_open_max_calls: int = 3
    ):
        self.source = source
        self.failure_threshold = failure_threshold
        self.reset_timeout = reset_timeout
        self.half_open_max_calls = half_open_max_calls
        
        self._state = "CLOSED"
        self._failure_count = 0
        self._last_failure_time: Optional[float] = None
        self._half_open_calls = 0
        try:
            self._lock = asyncio.Lock() if asyncio.get_event_loop().is_running() else None
        except RuntimeError:
            self._lock = None
    
    @property
    def state(self) -> str:
        """获取当前状态"""
        if self._state == "OPEN" and self._last_failure_time:
            if time.time() - self._last_failure_time >= self.reset_timeout:
                return "HALF_OPEN"
        return self._state
    
    @property
    def failure_count(self) -> int:
        return self._failure_count
    
    async def _acquire_lock(self):
        if self._lock:
            await self._lock.acquire()
    
    def _release_lock(self):
        if self._lock and self._lock.locked():
            self._lock.release()
    
    def guard(self):
        """
        上下文管理器，保护被熔断器监控的代码块

        使用示例：
            with cb.guard():
                result = fetch_data()

        异步版本：
            guard = await cb.guard_async()
            async with guard:
                result = await fetch_data()
        """
        current_state = self.state

        if current_state == "OPEN":
            raise CircuitBreakerError(
                self.source,
                self._failure_count,
                int(self.reset_timeout - (time.time() - self._last_failure_time)) if self._last_failure_time else self.reset_timeout
            )

        if current_state == "HALF_OPEN":
            if self._half_open_calls >= self.half_open_max_calls:
                raise CircuitBreakerError(self.source, self._failure_count, self.reset_timeout)
            self._half_open_calls += 1

        return _SyncCircuitBreakerGuard(self)

    async def guard_async(self):
        """异步上下文管理器"""
        await self._acquire_lock()
        try:
            current_state = self.state

            if current_state == "OPEN":
                raise CircuitBreakerError(
                    self.source,
                    self._failure_count,
                    int(self.reset_timeout - (time.time() - self._last_failure_time)) if self._last_failure_time else self.reset_timeout
                )

            if current_state == "HALF_OPEN":
                if self._half_open_calls >= self.half_open_max_calls:
                    raise CircuitBreakerError(self.source, self._failure_count, self.reset_timeout)
                self._half_open_calls += 1

            return _CircuitBreakerGuard(self)
        finally:
            self._release_lock()

    def guard_sync(self):
        """同步上下文管理器"""
        current_state = self.state

        if current_state == "OPEN":
            raise CircuitBreakerError(
                self.source,
                self._failure_count,
                int(self.reset_timeout - (time.time() - self._last_failure_time)) if self._last_failure_time else self.reset_timeout
            )

        if current_state == "HALF_OPEN":
            if self._half_open_calls >= self.half_open_max_calls:
                raise CircuitBreakerError(self.source, self._failure_count, self.reset_timeout)
            self._half_open_calls += 1

        return _SyncCircuitBreakerGuard(self)
    
    async def record_success(self):
        """记录成功调用"""
        async with self._lock if self._lock else _nullcontext():
            current_state = self.state
            if current_state == "HALF_OPEN":
                self._half_open_calls = 0
                self._state = "CLOSED"
                self._failure_count = 0
                logger.info(f"熔断器 [{self.source}] 恢复 CLOSED 状态")
            elif current_state == "CLOSED":
                self._failure_count = max(0, self._failure_count - 1)
    
    async def record_failure(self, error: Exception = None):
        """记录失败调用"""
        async with self._lock if self._lock else _nullcontext():
            self._failure_count += 1
            self._last_failure_time = time.time()
            
            if self._state == "HALF_OPEN":
                self._state = "OPEN"
                self._half_open_calls = 0
                logger.warning(f"熔断器 [{self.source}] HALF_OPEN -> OPEN (失败 {self._failure_count} 次)")
            elif self._state == "CLOSED" and self._failure_count >= self.failure_threshold:
                self._state = "OPEN"
                logger.warning(f"熔断器 [{self.source}] CLOSED -> OPEN (失败 {self._failure_count} 次，{self.reset_timeout}秒后恢复)")
    
    def reset(self):
        """手动重置熔断器"""
        self._state = "CLOSED"
        self._failure_count = 0
        self._last_failure_time = None
        self._half_open_calls = 0
        logger.info(f"熔断器 [{self.source}] 已手动重置")


class _CircuitBreakerGuard:
    """熔断器上下文管理器内部类"""
    
    def __init__(self, breaker: CircuitBreaker):
        self.breaker = breaker
    
    async def __aenter__(self):
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if exc_type is None:
            await self.breaker.record_success()
        else:
            await self.breaker.record_failure(exc_val)
        return False


class _SyncCircuitBreakerGuard:
    """同步熔断器上下文管理器"""

    def __init__(self, breaker: CircuitBreaker):
        self.breaker = breaker

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is None:
            self.breaker._failure_count = max(0, self.breaker._failure_count - 1)
        else:
            self.breaker._failure_count += 1
            self.breaker._last_failure_time = time.time()
            if self.breaker._failure_count >= self.breaker.failure_threshold:
                self.breaker._state = "OPEN"
        return False


class _nullcontext:
    """空上下文管理器（用于同步场景）"""
    async def __aenter__(self):
        pass
    async def __aexit__(self, *args):
        pass


class FallbackManager:
    """
    降级策略管理器
    
    按优先级尝试多个数据源，当前源失败时自动切换到下一个。
    
    参数：
        sources: 数据源列表 [(source_name, fetch_func), ...]
        circuit_breakers: 可选的熔断器字典 {source_name: CircuitBreaker}
    """
    
    def __init__(
        self,
        sources: List[Tuple[str, Callable]],
        circuit_breakers: Optional[Dict[str, CircuitBreaker]] = None
    ):
        self.sources = sources
        self.circuit_breakers = circuit_breakers or {}
        self._fallback_history: Dict[str, int] = {s[0]: 0 for s in sources}
    
    async def fetch(
        self,
        *args,
        skip_sources: Optional[List[str]] = None,
        **kwargs
    ) -> Any:
        """
        尝试从多个数据源获取数据
        
        参数：
            skip_sources: 跳过的数据源列表
            *args, **kwargs: 传递给各 fetch 函数的参数
        
        返回：
            第一个成功的数据源返回结果
        
        异常：
            FallbackError: 所有数据源都失败时抛出
        """
        skip_sources = skip_sources or []
        errors: Dict[str, str] = {}
        
        for source_name, fetch_func in self.sources:
            # 跳过指定的源
            if source_name in skip_sources:
                continue
            
            # 检查熔断器
            if source_name in self.circuit_breakers:
                cb = self.circuit_breakers[source_name]
                if cb.state == "OPEN":
                    logger.debug(f"跳过熔断的数据源：{source_name}")
                    continue
            
            try:
                logger.debug(f"尝试数据源：{source_name}")
                
                # 检查是否是异步函数
                if asyncio.iscoroutinefunction(fetch_func):
                    result = await fetch_func(*args, **kwargs)
                else:
                    result = fetch_func(*args, **kwargs)
                
                # 记录成功
                if source_name in self.circuit_breakers:
                    await self.circuit_breakers[source_name].record_success()
                
                self._fallback_history[source_name] = 0
                logger.info(f"数据源 {source_name} 获取成功")
                return result
                
            except CircuitBreakerError:
                # 熔断器触发，跳过
                errors[source_name] = "Circuit breaker open"
                continue
                
            except SourceRateLimitedError as e:
                # 限流，记录并尝试下一个
                errors[source_name] = f"Rate limited (retry after: {e.details.get('retry_after_seconds', 'unknown')}s)"
                continue
                
            except SourceUnavailableError as e:
                # 源不可用，记录错误
                errors[source_name] = str(e)
                if source_name in self.circuit_breakers:
                    await self.circuit_breakers[source_name].record_failure(e)
                continue

            except (DataNotFoundError, DataEmptyError) as e:
                # 数据为空或未找到，记录错误并尝试下一个源
                errors[source_name] = f"{type(e).__name__}: {str(e)[:100]}"
                logger.warning(f"数据源 {source_name} 返回空数据，尝试备用源")
                continue

            except Exception as e:
                # 其他异常
                errors[source_name] = f"{type(e).__name__}: {str(e)[:100]}"
                if source_name in self.circuit_breakers:
                    await self.circuit_breakers[source_name].record_failure(e)
                continue
        
        # 所有源都失败
        primary = self.sources[0][0] if self.sources else "unknown"
        fallbacks = [s[0] for s in self.sources[1:]]
        
        logger.error(f"所有数据源均失败：primary={primary}, fallbacks={fallbacks}")
        raise FallbackError(primary, fallbacks, errors)
    
    def get_fallback_order(self) -> List[str]:
        """获取数据源优先级顺序"""
        return [s[0] for s in self.sources]


class RateLimiter:
    """
    令牌桶限流器
    
    控制 API 调用频率，防止触发限流。
    
    参数：
        max_calls: 周期内最大调用次数
        period: 周期时间（秒）
        burst: 突发容量（默认等于 max_calls）
    """
    
    def __init__(self, max_calls: int = 10, period: int = 60, burst: Optional[int] = None):
        self.max_calls = max_calls
        self.period = period
        self.burst = burst or max_calls
        self._tokens = float(self.burst)
        self._last_refill = time.time()
        try:
            self._lock = asyncio.Lock() if asyncio.get_event_loop().is_running() else None
        except RuntimeError:
            self._lock = None
    
    def _refill(self):
        """补充令牌"""
        now = time.time()
        elapsed = now - self._last_refill
        self._tokens = min(self.burst, self._tokens + elapsed * (self.max_calls / self.period))
        self._last_refill = now
    
    async def acquire(self, tokens: int = 1):
        """获取令牌（异步）"""
        while True:
            async with self._lock:
                self._refill()
                if self._tokens >= tokens:
                    self._tokens -= tokens
                    return
            # 等待令牌补充
            wait_time = (tokens - self._tokens) * (self.period / self.max_calls)
            logger.debug(f"限流器等待 {wait_time:.2f} 秒")
            await asyncio.sleep(wait_time)
    
    def acquire_sync(self, tokens: int = 1):
        """获取令牌（同步）"""
        while True:
            with _sync_lock():
                self._refill()
                if self._tokens >= tokens:
                    self._tokens -= tokens
                    return
            wait_time = (tokens - self._tokens) * (self.period / self.max_calls)
            logger.debug(f"限流器等待 {wait_time:.2f} 秒")
            time.sleep(wait_time)
    
    @property
    def available_tokens(self) -> float:
        """获取可用令牌数"""
        self._refill()
        return self._tokens


class HealthChecker:
    """
    数据源健康检查器

    定期检查数据源可用性，维护健康状态。
    区分临时故障（网络抖动、限流）与永久故障（服务下线、配置错误）。

    参数：
        sources: 数据源列表 [{"name": str, "check_func": Callable, "timeout": float}]
        check_interval: 检查间隔（秒）
        success_rate_threshold: 成功率阈值（低于此值触发告警）
        temp_failure_threshold: 临时故障判定阈值（连续失败次数）
        permanent_failure_threshold: 永久故障判定阈值（成功率低于此值）
    """

    # 临时故障类型
    TEMPORARY_ERRORS = (
        ConnectionError,
        TimeoutError,
        SourceRateLimitedError,
        OSError,
    )

    # 永久故障类型
    PERMANENT_ERRORS = (
        SourceUnavailableError,
        DataNotFoundError,
        DataEmptyError,
    )

    def __init__(
        self,
        sources: List[Dict[str, Any]],
        check_interval: int = 300,
        success_rate_threshold: float = 0.8,
        temp_failure_threshold: int = 3,
        permanent_failure_threshold: float = 0.5,
    ):
        self.sources = sources
        self.check_interval = check_interval
        self.success_rate_threshold = success_rate_threshold
        self.temp_failure_threshold = temp_failure_threshold
        self.permanent_failure_threshold = permanent_failure_threshold
        self._health_status: Dict[str, Dict[str, Any]] = {}
        self._last_check: Dict[str, float] = {}
        self._running = False
        self._task: Optional[asyncio.Task] = None
        # 成功率统计
        self._success_counts: Dict[str, int] = {s["name"]: 0 for s in sources}
        self._failure_counts: Dict[str, int] = {s["name"]: 0 for s in sources}
        self._alerts: List[Dict[str, Any]] = []
        # 故障类型统计
        self._temp_failure_counts: Dict[str, int] = {s["name"]: 0 for s in sources}
        self._permanent_failure_counts: Dict[str, int] = {s["name"]: 0 for s in sources}
        # 故障历史
        self._failure_history: Dict[str, List[Dict[str, Any]]] = {s["name"]: [] for s in sources}

    def record_fetch_result(self, source_name: str, success: bool):
        """记录抓取结果，用于成功率统计"""
        if source_name not in self._success_counts:
            self._success_counts[source_name] = 0
            self._failure_counts[source_name] = 0
            self._temp_failure_counts[source_name] = 0
            self._permanent_failure_counts[source_name] = 0

        if success:
            self._success_counts[source_name] += 1
            # 成功时重置临时故障计数
            self._temp_failure_counts[source_name] = 0
        else:
            self._failure_counts[source_name] += 1
            # 检查成功率是否低于阈值
            total = self._success_counts[source_name] + self._failure_counts[source_name]
            if total >= 10:  # 至少10次调用后才评估
                rate = self._success_counts[source_name] / total
                if rate < self.success_rate_threshold:
                    alert = {
                        "source": source_name,
                        "type": "low_success_rate",
                        "rate": round(rate, 4),
                        "total_calls": total,
                        "success_calls": self._success_counts[source_name],
                        "failure_calls": self._failure_counts[source_name],
                        "threshold": self.success_rate_threshold,
                        "timestamp": time.time()
                    }
                    self._alerts.append(alert)
                    logger.warning(f"数据源 {source_name} 成功率过低：{rate:.2%} (阈值：{self.success_rate_threshold:.0%})")

    def record_failure(self, source_name: str, error: Exception):
        """记录失败并分类为临时或永久故障"""
        if source_name not in self._success_counts:
            self._success_counts[source_name] = 0
            self._failure_counts[source_name] = 0
            self._temp_failure_counts[source_name] = 0
            self._permanent_failure_counts[source_name] = 0

        self._failure_counts[source_name] += 1

        # 分类故障类型
        if isinstance(error, self.TEMPORARY_ERRORS):
            self._temp_failure_counts[source_name] += 1
            failure_type = "temporary"
        elif isinstance(error, self.PERMANENT_ERRORS):
            self._permanent_failure_counts[source_name] += 1
            failure_type = "permanent"
        else:
            # 未知类型，默认视为临时故障
            self._temp_failure_counts[source_name] += 1
            failure_type = "unknown"

        # 记录故障历史
        self._failure_history[source_name].append({
            "timestamp": time.time(),
            "type": failure_type,
            "error": str(error)[:200],
            "error_type": type(error).__name__,
        })
        # 保留最近100条记录
        self._failure_history[source_name] = self._failure_history[source_name][-100:]

        # 检查是否达到临时故障阈值
        if self._temp_failure_counts[source_name] >= self.temp_failure_threshold:
            alert = {
                "source": source_name,
                "type": "temp_failure_threshold",
                "consecutive_failures": self._temp_failure_counts[source_name],
                "threshold": self.temp_failure_threshold,
                "timestamp": time.time()
            }
            self._alerts.append(alert)
            logger.warning(f"数据源 {source_name} 连续临时故障 {self._temp_failure_counts[source_name]} 次")

        # 检查是否达到永久故障阈值
        if self._permanent_failure_counts[source_name] >= self.temp_failure_threshold:
            alert = {
                "source": source_name,
                "type": "permanent_failure",
                "consecutive_failures": self._permanent_failure_counts[source_name],
                "threshold": self.temp_failure_threshold,
                "timestamp": time.time()
            }
            self._alerts.append(alert)
            logger.error(f"数据源 {source_name} 可能已永久失效，连续永久故障 {self._permanent_failure_counts[source_name]} 次")

    def get_failure_type(self, source_name: str) -> Optional[str]:
        """获取数据源最近的故障类型"""
        history = self._failure_history.get(source_name, [])
        if not history:
            return None
        return history[-1].get("type")

    def get_temp_failure_count(self, source_name: str) -> int:
        """获取临时故障计数"""
        return self._temp_failure_counts.get(source_name, 0)

    def get_permanent_failure_count(self, source_name: str) -> int:
        """获取永久故障计数"""
        return self._permanent_failure_counts.get(source_name, 0)

    def is_temporarily_unavailable(self, source_name: str) -> bool:
        """检查数据源是否临时不可用"""
        return self._temp_failure_counts.get(source_name, 0) >= self.temp_failure_threshold

    def is_permanently_unavailable(self, source_name: str) -> bool:
        """检查数据源是否永久不可用"""
        return self._permanent_failure_counts.get(source_name, 0) >= self.temp_failure_threshold

    def reset_failure_counts(self, source_name: str):
        """重置指定数据源的故障计数"""
        self._temp_failure_counts[source_name] = 0
        self._permanent_failure_counts[source_name] = 0
        self._failure_history[source_name] = []
        logger.info(f"已重置数据源 {source_name} 的故障计数")

    def get_success_rate(self, source_name: str) -> float:
        """获取数据源成功率"""
        success = self._success_counts.get(source_name, 0)
        failure = self._failure_counts.get(source_name, 0)
        total = success + failure
        if total == 0:
            return 1.0  # 无记录时默认健康
        return success / total

    def get_recent_alerts(self, source_name: Optional[str] = None, limit: int = 10) -> List[Dict[str, Any]]:
        """获取最近的告警"""
        if source_name:
            return [a for a in self._alerts if a["source"] == source_name][-limit:]
        return self._alerts[-limit:]

    def clear_alerts(self, source_name: Optional[str] = None):
        """清除告警"""
        if source_name:
            self._alerts = [a for a in self._alerts if a["source"] != source_name]
        else:
            self._alerts = []
    
    async def check(self, source_name: str) -> Dict[str, Any]:
        """检查单个数据源健康状态"""
        source_info = next((s for s in self.sources if s["name"] == source_name), None)
        if not source_info:
            raise ValueError(f"未知数据源：{source_name}")

        check_func = source_info.get("check_func")
        timeout = source_info.get("timeout", 10)

        try:
            start_time = time.time()
            if asyncio.iscoroutinefunction(check_func):
                await asyncio.wait_for(check_func(), timeout=timeout)
            else:
                check_func()

            elapsed = time.time() - start_time
            status = {
                "source": source_name,
                "healthy": True,
                "latency_ms": round(elapsed * 1000, 2),
                "last_check": time.time(),
                "consecutive_failures": 0,
                "failure_type": None,
                "success_rate": self.get_success_rate(source_name),
            }
            logger.debug(f"数据源 {source_name} 健康检查通过（{elapsed*1000:.1f}ms）")
            # 成功时重置故障计数
            self.reset_failure_counts(source_name)

        except Exception as e:
            elapsed = time.time() - start_time
            prev_failures = self._health_status.get(source_name, {}).get("consecutive_failures", 0)
            # 分类故障类型
            if isinstance(e, self.TEMPORARY_ERRORS):
                failure_type = "temporary"
            elif isinstance(e, self.PERMANENT_ERRORS):
                failure_type = "permanent"
            else:
                failure_type = "unknown"

            status = {
                "source": source_name,
                "healthy": False,
                "error": str(e)[:100],
                "error_type": type(e).__name__,
                "failure_type": failure_type,
                "latency_ms": round(elapsed * 1000, 2),
                "last_check": time.time(),
                "consecutive_failures": prev_failures + 1,
                "success_rate": self.get_success_rate(source_name),
            }
            logger.warning(f"数据源 {source_name} 健康检查失败：{e}")
            # 记录故障
            self.record_failure(source_name, e)

        self._health_status[source_name] = status
        self._last_check[source_name] = time.time()
        return status
    
    async def check_all(self) -> Dict[str, Dict[str, Any]]:
        """检查所有数据源健康状态"""
        results = {}
        for source_info in self.sources:
            name = source_info["name"]
            results[name] = await self.check(name)
        return results
    
    def get_status(self, source_name: str) -> Optional[Dict[str, Any]]:
        """获取数据源当前健康状态"""
        return self._health_status.get(source_name)
    
    def is_healthy(self, source_name: str) -> bool:
        """检查数据源是否健康"""
        status = self._health_status.get(source_name)
        return status and status.get("healthy", False)
    
    async def start_monitoring(self):
        """启动后台健康监控"""
        if self._running:
            return
        
        self._running = True
        self._task = asyncio.create_task(self._monitor_loop())
        logger.info(f"健康检查器已启动，间隔 {self.check_interval} 秒")
    
    async def _monitor_loop(self):
        """监控循环"""
        while self._running:
            try:
                await self.check_all()
            except Exception as e:
                logger.error(f"健康检查循环出错：{e}")
            await asyncio.sleep(self.check_interval)
    
    def stop_monitoring(self):
        """停止后台监控"""
        self._running = False
        if self._task:
            self._task.cancel()
            self._task = None
        logger.info("健康检查器已停止")


def retry_with_backoff(
    max_retries: int = 3,
    backoff_factors: List[float] = None,
    retryable_exceptions: tuple = (Exception,)
):
    """
    带指数退避的重试装饰器
    
    参数：
        max_retries: 最大重试次数
        backoff_factors: 退避因子列表 [1, 2, 5] 表示等待 1s, 2s, 5s
        retryable_exceptions: 需要重试的异常类型
    
    使用示例：
        @retry_with_backoff(max_retries=3, backoff_factors=[1, 2, 5])
        async def fetch_data():
            ...
        
        @retry_with_backoff(max_retries=3, backoff_factors=[1, 2, 5])
        def fetch_data_sync():
            ...
    """
    if backoff_factors is None:
        backoff_factors = [1, 2, 5]
    
    def decorator(func):
        if asyncio.iscoroutinefunction(func):
            @wraps(func)
            async def async_wrapper(*args, **kwargs):
                last_exception = None
                
                for attempt in range(max_retries + 1):
                    try:
                        return await func(*args, **kwargs)
                    except retryable_exceptions as e:
                        last_exception = e
                        
                        if attempt < max_retries:
                            wait_time = backoff_factors[min(attempt, len(backoff_factors) - 1)]
                            logger.warning(
                                f"{func.__name__} 第 {attempt + 1} 次失败，"
                                f"等待 {wait_time}s 后重试：{str(e)[:100]}"
                            )
                            await asyncio.sleep(wait_time)
                        else:
                            logger.error(f"{func.__name__} 重试 {max_retries} 次后仍失败")
                
                raise last_exception
            
            return async_wrapper
        else:
            @wraps(func)
            def sync_wrapper(*args, **kwargs):
                last_exception = None
                
                for attempt in range(max_retries + 1):
                    try:
                        return func(*args, **kwargs)
                    except retryable_exceptions as e:
                        last_exception = e
                        
                        if attempt < max_retries:
                            wait_time = backoff_factors[min(attempt, len(backoff_factors) - 1)]
                            logger.warning(
                                f"{func.__name__} 第 {attempt + 1} 次失败，"
                                f"等待 {wait_time}s 后重试：{str(e)[:100]}"
                            )
                            time.sleep(wait_time)
                        else:
                            logger.error(f"{func.__name__} 重试 {max_retries} 次后仍失败")
                
                raise last_exception
            
            return sync_wrapper
    
    return decorator


# 默认熔断器配置
DEFAULT_CIRCUIT_BREAKERS = {
    "akshare": CircuitBreaker("akshare", failure_threshold=5, reset_timeout=60),
    "eastmoney": CircuitBreaker("eastmoney", failure_threshold=5, reset_timeout=60),
    "sina": CircuitBreaker("sina", failure_threshold=5, reset_timeout=60),
    "tushare": CircuitBreaker("tushare", failure_threshold=3, reset_timeout=120),
}

# 默认数据源优先级
DEFAULT_SOURCE_PRIORITY = [
    ("akshare", None),  # 第一个为 None 表示需要动态导入
    ("eastmoney", None),
    ("sina", None),
]
