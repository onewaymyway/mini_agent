# -*- coding: utf-8 -*-
"""
超时控制模块

提供分级超时配置和智能等待策略，解决响应时间过长问题。
"""

import asyncio
import time
import logging
from functools import wraps
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)


class TimeoutConfig:
    """超时配置 - 按操作类型分级"""
    
    DEFAULT_TIMEOUTS = {
        "navigation": {"connect_timeout": 10.0, "read_timeout": 30.0, "total_timeout": 60.0},
        "search": {"connect_timeout": 5.0, "read_timeout": 20.0, "total_timeout": 30.0},
        "element_find": {"connect_timeout": 3.0, "read_timeout": 10.0, "total_timeout": 15.0},
        "screenshot": {"connect_timeout": 5.0, "read_timeout": 10.0, "total_timeout": 15.0},
        "input_click": {"connect_timeout": 3.0, "read_timeout": 10.0, "total_timeout": 15.0},
        "form_fill": {"connect_timeout": 3.0, "read_timeout": 10.0, "total_timeout": 15.0},
        "scroll": {"connect_timeout": 3.0, "read_timeout": 15.0, "total_timeout": 20.0},
        "download": {"connect_timeout": 10.0, "read_timeout": 60.0, "total_timeout": 120.0},
    }
    
    def __init__(self, connect_timeout: float = 5.0, read_timeout: float = 20.0, total_timeout: float = 30.0):
        self.connect_timeout = connect_timeout
        self.read_timeout = read_timeout
        self.total_timeout = total_timeout
    
    @classmethod
    def for_operation(cls, operation: str) -> "TimeoutConfig":
        """根据操作类型获取超时配置"""
        config_data = cls.DEFAULT_TIMEOUTS.get(operation, cls.DEFAULT_TIMEOUTS["search"])
        return cls(connect_timeout=config_data["connect_timeout"], read_timeout=config_data["read_timeout"], total_timeout=config_data["total_timeout"])
    
    def to_dict(self) -> Dict[str, float]:
        return {"connect": self.connect_timeout, "read": self.read_timeout, "total": self.total_timeout}


class TimeoutManager:
    """超时管理器 - 支持分级超时和熔断"""
    
    def __init__(self):
        self._timeouts: Dict[str, TimeoutConfig] = {}
        self._timeout_failures: Dict[str, int] = {}
        self._timeout_threshold = 3
    
    def register_timeout(self, operation: str, config: TimeoutConfig):
        """注册操作超时配置"""
        self._timeouts[operation] = config
        logger.info(f"Registered timeout config for {operation}: {config.to_dict()}")
    
    def get_timeout(self, operation: str) -> TimeoutConfig:
        """获取操作超时配置"""
        return self._timeouts.get(operation, TimeoutConfig.for_operation(operation))
    
    def check_timeout_circuit(self, operation: str) -> bool:
        """检查是否触发超时熔断"""
        failures = self._timeout_failures.get(operation, 0)
        return failures >= self._timeout_threshold
    
    def record_timeout(self, operation: str):
        """记录超时失败"""
        self._timeout_failures[operation] = self._timeout_failures.get(operation, 0) + 1
        logger.warning(f"Timeout recorded for {operation}: {self._timeout_failures[operation]}/{self._timeout_threshold}")
    
    def record_success(self, operation: str):
        """记录成功，重置计数器"""
        if operation in self._timeout_failures:
            del self._timeout_failures[operation]
    
    def get_status(self) -> Dict[str, Any]:
        return {"registered_operations": list(self._timeouts.keys()), "timeout_failures": dict(self._timeout_failures)}


_global_timeout_manager: Optional[TimeoutManager] = None


def get_timeout_manager() -> TimeoutManager:
    """获取全局超时管理器实例"""
    global _global_timeout_manager
    if _global_timeout_manager is None:
        _global_timeout_manager = TimeoutManager()
        for op, config in TimeoutConfig.DEFAULT_TIMEOUTS.items():
            _global_timeout_manager.register_timeout(op, TimeoutConfig(**config))
    return _global_timeout_manager


def with_timeout(operation: str = "unknown", connect_timeout: Optional[float] = None, read_timeout: Optional[float] = None, total_timeout: Optional[float] = None):
    """装饰器：为函数添加超时控制"""
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            manager = get_timeout_manager()
            config = manager.get_timeout(operation)
            if connect_timeout is not None:
                config.connect_timeout = connect_timeout
            if read_timeout is not None:
                config.read_timeout = read_timeout
            if total_timeout is not None:
                config.total_timeout = total_timeout
            if manager.check_timeout_circuit(operation):
                raise TimeoutError(f"Circuit breaker open for {operation}")
            start_time = time.time()
            try:
                result = func(*args, **kwargs)
                manager.record_success(operation)
                return result
            except TimeoutError:
                manager.record_timeout(operation)
                raise
            except Exception as e:
                duration = time.time() - start_time
                if duration > config.total_timeout:
                    manager.record_timeout(operation)
                    raise TimeoutError(f"Operation {operation} timed out after {duration:.1f}s (limit: {config.total_timeout}s)") from e
                raise
        return wrapper
    return decorator


async def async_with_timeout(func: Callable, operation: str = "unknown", timeout: Optional[float] = None, *args, **kwargs) -> Any:
    """异步超时包装器"""
    manager = get_timeout_manager()
    config = manager.get_timeout(operation)
    effective_timeout = timeout or config.total_timeout
    try:
        return await asyncio.wait_for(func(*args, **kwargs), timeout=effective_timeout)
    except asyncio.TimeoutError:
        manager.record_timeout(operation)
        raise TimeoutError(f"Operation {operation} timed out after {effective_timeout}s")
    except Exception as e:
        manager.record_success(operation)
        raise


class SmartWait:
    """智能等待策略 - 动态轮询替代固定等待"""
    
    def __init__(self, default_timeout: float = 10.0, poll_interval: float = 0.5):
        self.default_timeout = default_timeout
        self.poll_interval = poll_interval
    
    def wait_for(self, condition: Callable[[], bool], timeout: Optional[float] = None, interval: Optional[float] = None) -> bool:
        """等待条件满足"""
        timeout = timeout or self.default_timeout
        interval = interval or self.poll_interval
        start_time = time.time()
        while time.time() - start_time < timeout:
            if condition():
                return True
            time.sleep(interval)
        return False
    
    async def async_wait_for(self, condition: Callable[[], bool], timeout: Optional[float] = None, interval: Optional[float] = None) -> bool:
        """异步等待条件满足"""
        timeout = timeout or self.default_timeout
        interval = interval or self.poll_interval
        start_time = time.time()
        while time.time() - start_time < timeout:
            if condition():
                return True
            await asyncio.sleep(interval)
        return False
    
    def wait_for_network_idle(self, get_pending_requests: Callable[[], int], idle_time: float = 0.5, timeout: Optional[float] = None) -> bool:
        """等待网络空闲"""
        timeout = timeout or self.default_timeout
        start_time = time.time()
        last_idle_time = None
        while time.time() - start_time < timeout:
            pending = get_pending_requests()
            if pending == 0:
                if last_idle_time is None:
                    last_idle_time = time.time()
                elif time.time() - last_idle_time >= idle_time:
                    return True
            else:
                last_idle_time = None
            time.sleep(self.poll_interval)
        return False


_global_smart_wait = SmartWait()


def get_smart_wait() -> SmartWait:
    """获取全局智能等待实例"""
    return _global_smart_wait