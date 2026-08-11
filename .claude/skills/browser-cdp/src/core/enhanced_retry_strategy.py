"""
enhanced_retry_strategy.py - 增强重试策略模块

提供智能重试机制，支持：
- 指数退避重试
- 自适应超时调整
- 错误类型分类处理
- 重试次数限制与熔断
"""
from __future__ import annotations

import asyncio
import logging
import random
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Type, TYPE_CHECKING

if TYPE_CHECKING:
    from playwright.async_api import Page

logger = logging.getLogger(__name__)


class ErrorType(Enum):
    """错误类型分类"""
    TIMEOUT = "timeout"
    ELEMENT_NOT_FOUND = "element_not_found"
    CAPTCHA_DETECTED = "captcha_detected"
    NETWORK_ERROR = "network_error"
    SELECTOR_INVALID = "selector_invalid"
    PAGE_ERROR = "page_error"
    UNKNOWN = "unknown"


@dataclass
class RetryConfig:
    """重试配置"""
    max_retries: int = 3  # 最大重试次数
    base_delay: float = 1.0  # 基础延迟（秒）
    max_delay: float = 30.0  # 最大延迟（秒）
    exponential_base: float = 2.0  # 指数退避基数
    jitter_range: float = 0.5  # 随机抖动范围
    
    # 错误类型特定配置
    error_delays: Dict[ErrorType, float] = field(default_factory=lambda: {
        ErrorType.TIMEOUT: 2.0,
        ErrorType.ELEMENT_NOT_FOUND: 1.5,
        ErrorType.CAPTCHA_DETECTED: 5.0,
        ErrorType.NETWORK_ERROR: 3.0,
        ErrorType.SELECTOR_INVALID: 1.0,
        ErrorType.PAGE_ERROR: 2.0,
    })
    
    # 熔断配置
    circuit_breaker_threshold: int = 5  # 熔断阈值（连续失败次数）
    circuit_breaker_timeout: float = 60.0  # 熔断恢复时间（秒）


@dataclass
class RetryResult:
    """重试结果"""
    success: bool
    result: Optional[Any] = None
    error: Optional[Exception] = None
    error_type: Optional[ErrorType] = None
    retry_count: int = 0
    total_time: float = 0.0
    final_delay: float = 0.0


class CircuitBreaker:
    """熔断器 - 防止连续失败导致资源浪费"""
    
    def __init__(self, threshold: int = 5, timeout: float = 60.0):
        self.threshold = threshold
        self.timeout = timeout
        self.failure_count = 0
        self.last_failure_time: Optional[float] = None
        self.state = "closed"  # closed, open, half_open
    
    def record_success(self):
        """记录成功"""
        self.failure_count = 0
        self.state = "closed"
    
    def record_failure(self):
        """记录失败"""
        self.failure_count += 1
        self.last_failure_time = time.time()
        
        if self.failure_count >= self.threshold:
            self.state = "open"
            logger.warning(f"熔断器触发：连续失败 {self.failure_count} 次")
    
    def can_execute(self) -> bool:
        """检查是否允许执行"""
        if self.state == "closed":
            return True
        
        if self.state == "open":
            if self.last_failure_time and (time.time() - self.last_failure_time) >= self.timeout:
                self.state = "half_open"
                logger.info("熔断器进入半开状态，允许尝试")
                return True
            return False
        
        # half_open 状态允许一次尝试
        return True


class EnhancedRetryStrategy:
    """增强重试策略"""
    
    def __init__(self, config: Optional[RetryConfig] = None):
        self.config = config or RetryConfig()
        self.circuit_breakers: Dict[str, CircuitBreaker] = {}
        self.retry_history: List[Dict] = []
    
    def get_circuit_breaker(self, key: str) -> CircuitBreaker:
        """获取或创建熔断器"""
        if key not in self.circuit_breakers:
            self.circuit_breakers[key] = CircuitBreaker(
                threshold=self.config.circuit_breaker_threshold,
                timeout=self.config.circuit_breaker_timeout
            )
        return self.circuit_breakers[key]
    
    def classify_error(self, error: Exception) -> ErrorType:
        """分类错误类型"""
        error_msg = str(error).lower()
        
        if "timeout" in error_msg or "timed out" in error_msg:
            return ErrorType.TIMEOUT
        elif "element" in error_msg and ("not found" in error_msg or "could not" in error_msg):
            return ErrorType.ELEMENT_NOT_FOUND
        elif "captcha" in error_msg or "verify" in error_msg or "security" in error_msg:
            return ErrorType.CAPTCHA_DETECTED
        elif "network" in error_msg or "connection" in error_msg or "refused" in error_msg:
            return ErrorType.NETWORK_ERROR
        elif "selector" in error_msg or "invalid" in error_msg:
            return ErrorType.SELECTOR_INVALID
        elif "page" in error_msg or "navigation" in error_msg:
            return ErrorType.PAGE_ERROR
        else:
            return ErrorType.UNKNOWN
    
    def calculate_delay(self, retry_count: int, error_type: ErrorType) -> float:
        """计算重试延迟（指数退避 + 随机抖动）"""
        # 基础延迟：指数退避
        base_delay = self.config.error_delays.get(error_type, self.config.base_delay)
        exponential_delay = base_delay * (self.config.exponential_base ** min(retry_count, 5))
        
        # 限制最大延迟
        delay = min(exponential_delay, self.config.max_delay)
        
        # 添加随机抖动
        jitter = random.uniform(0, self.config.jitter_range * delay)
        final_delay = delay + jitter
        
        return final_delay
    
    async def execute_with_retry(
        self,
        operation_name: str,
        operation: Callable,
        *args,
        **kwargs
    ) -> RetryResult:
        """
        执行操作并自动重试
        
        Args:
            operation_name: 操作名称（用于日志和熔断器）
            operation: 要执行的可调用对象
            *args, **kwargs: 操作参数
            
        Returns:
            RetryResult: 重试结果
        """
        circuit_breaker = self.get_circuit_breaker(operation_name)
        start_time = time.time()
        
        for retry_count in range(self.config.max_retries + 1):
            # 检查熔断器
            if not circuit_breaker.can_execute():
                logger.warning(f"操作 {operation_name} 处于熔断状态，跳过重试")
                return RetryResult(
                    success=False,
                    error=Exception("熔断器触发，操作被阻止"),
                    retry_count=retry_count,
                    total_time=time.time() - start_time
                )
            
            try:
                # 执行操作
                if asyncio.iscoroutinefunction(operation):
                    result = await operation(*args, **kwargs)
                else:
                    result = operation(*args, **kwargs)
                
                # 成功
                circuit_breaker.record_success()
                total_time = time.time() - start_time
                
                logger.debug(f"操作 {operation_name} 成功（重试 {retry_count} 次，耗时 {total_time:.2f}s）")
                
                # 记录历史
                self.retry_history.append({
                    "operation": operation_name,
                    "retry_count": retry_count,
                    "success": True,
                    "time": total_time,
                    "timestamp": time.time()
                })
                
                return RetryResult(
                    success=True,
                    result=result,
                    retry_count=retry_count,
                    total_time=total_time
                )
                
            except Exception as e:
                error_type = self.classify_error(e)
                
                # 记录失败
                circuit_breaker.record_failure()
                
                # 如果是最后一次重试，直接返回失败
                if retry_count >= self.config.max_retries:
                    total_time = time.time() - start_time
                    logger.error(f"操作 {operation_name} 失败（重试 {retry_count} 次）: {e}")
                    
                    self.retry_history.append({
                        "operation": operation_name,
                        "retry_count": retry_count,
                        "success": False,
                        "error_type": error_type.value,
                        "error": str(e),
                        "time": total_time,
                        "timestamp": time.time()
                    })
                    
                    return RetryResult(
                        success=False,
                        error=e,
                        error_type=error_type,
                        retry_count=retry_count,
                        total_time=total_time
                    )
                
                # 计算延迟并重试
                delay = self.calculate_delay(retry_count, error_type)
                logger.warning(
                    f"操作 {operation_name} 失败（第 {retry_count + 1} 次尝试）: {e}，"
                    f"{delay:.2f}s 后重试"
                )
                
                await asyncio.sleep(delay)
        
        # 理论上不会到达这里
        return RetryResult(
            success=False,
            error=Exception("重试次数耗尽"),
            retry_count=self.config.max_retries,
            total_time=time.time() - start_time
        )
    
    def get_stats(self) -> Dict[str, Any]:
        """获取重试统计"""
        if not self.retry_history:
            return {
                "total_operations": 0,
                "success_rate": 0.0,
                "avg_retries": 0.0,
                "avg_time": 0.0,
                "error_distribution": {}
            }
        
        total = len(self.retry_history)
        success_count = sum(1 for r in self.retry_history if r["success"])
        error_types: Dict[str, int] = {}
        
        for r in self.retry_history:
            if not r["success"] and "error_type" in r:
                error_types[r["error_type"]] = error_types.get(r["error_type"], 0) + 1
        
        return {
            "total_operations": total,
            "success_count": success_count,
            "failure_count": total - success_count,
            "success_rate": success_count / total if total > 0 else 0.0,
            "avg_retries": sum(r.get("retry_count", 0) for r in self.retry_history) / total,
            "avg_time": sum(r.get("time", 0) for r in self.retry_history) / total,
            "error_distribution": error_types
        }


# 全局单例
_retry_strategy: Optional[EnhancedRetryStrategy] = None


def get_retry_strategy() -> EnhancedRetryStrategy:
    """获取全局重试策略单例"""
    global _retry_strategy
    if _retry_strategy is None:
        _retry_strategy = EnhancedRetryStrategy()
    return _retry_strategy


def set_retry_strategy(strategy: EnhancedRetryStrategy):
    """设置全局重试策略"""
    global _retry_strategy
    _retry_strategy = strategy


def reset_retry_strategy():
    """重置全局重试策略"""
    global _retry_strategy
    _retry_strategy = None


# 便捷函数
async def retry_operation(
    operation_name: str,
    operation: Callable,
    *args,
    config: Optional[RetryConfig] = None,
    **kwargs
) -> RetryResult:
    """
    便捷函数：执行操作并自动重试
    
    Args:
        operation_name: 操作名称
        operation: 要执行的操作
        config: 重试配置（可选，默认使用全局配置）
        *args, **kwargs: 操作参数
        
    Returns:
        RetryResult: 重试结果
    """
    strategy = EnhancedRetryStrategy(config)
    return await strategy.execute_with_retry(operation_name, operation, *args, **kwargs)
