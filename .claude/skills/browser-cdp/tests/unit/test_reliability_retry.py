"""
reliability/retry.py 单元测试

测试覆盖：
- BackoffStrategy 枚举
- RetryConfig 配置
- CircuitBreaker 熔断器
- retry_operation 同步重试
- retry_operation_async 异步重试
- with_retry 装饰器
- with_retry_async 异步装饰器
- get_retry_config 配置获取
"""
import pytest
import sys
import asyncio
import time
from pathlib import Path
from unittest.mock import patch, Mock, MagicMock, AsyncMock

# 添加 skill 目录到路径
SKILL_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from src.reliability.retry import (
    BackoffStrategy,
    RetryConfig,
    CircuitBreaker,
    retry_operation,
    retry_operation_async,
    with_retry,
    with_retry_async,
    get_retry_config,
    _calculate_delay,
)
from src.reliability.error import (
    ReliabilityError,
    CDPConnectionLostError,
    CircuitBreakerOpenError,
    ElementNotFoundError,
    ErrorCategory,
)


class TestBackoffStrategy:
    """BackoffStrategy 枚举测试"""
    
    def test_all_strategies_exist(self):
        """测试所有策略都存在"""
        assert hasattr(BackoffStrategy, 'FIXED')
        assert hasattr(BackoffStrategy, 'LINEAR')
        assert hasattr(BackoffStrategy, 'EXPONENTIAL')
        assert hasattr(BackoffStrategy, 'EXPONENTIAL_JITTER')
    
    def test_strategy_values(self):
        """测试策略值"""
        assert BackoffStrategy.FIXED.value == "fixed"
        assert BackoffStrategy.LINEAR.value == "linear"
        assert BackoffStrategy.EXPONENTIAL.value == "exponential"
        assert BackoffStrategy.EXPONENTIAL_JITTER.value == "exponential_jitter"


class TestRetryConfig:
    """RetryConfig 配置测试"""
    
    def test_default_config(self):
        """测试默认配置"""
        config = RetryConfig()
        assert config.max_retries == 3
        assert config.base_delay == 1.0
        assert config.max_delay == 30.0
        assert config.backoff_strategy == BackoffStrategy.EXPONENTIAL_JITTER
    
    def test_custom_config(self):
        """测试自定义配置"""
        config = RetryConfig(
            max_retries=5,
            backoff_strategy=BackoffStrategy.FIXED,
            base_delay=2.0,
            max_delay=60.0,
        )
        assert config.max_retries == 5
        assert config.base_delay == 2.0
        assert config.max_delay == 60.0
    
    def test_operation_defaults(self):
        """测试操作类型默认配置"""
        assert "cdp_command" in RetryConfig.OPERATION_DEFAULTS
        assert "element_find" in RetryConfig.OPERATION_DEFAULTS
        assert "navigation" in RetryConfig.OPERATION_DEFAULTS
        assert "screenshot" in RetryConfig.OPERATION_DEFAULTS
        assert "input_click" in RetryConfig.OPERATION_DEFAULTS
    
    def test_for_operation(self):
        """测试 for_operation 方法"""
        config = RetryConfig.for_operation("cdp_command")
        assert config.max_retries == 5
        assert config.circuit_breaker is True
        
        config = RetryConfig.for_operation("element_find")
        assert config.max_retries == 3
        assert config.circuit_breaker is False
    
    def test_for_operation_with_overrides(self):
        """测试 for_operation 带覆盖参数"""
        config = RetryConfig.for_operation("cdp_command", max_retries=10)
        assert config.max_retries == 10
    
    def test_for_operation_unknown(self):
        """测试未知操作类型"""
        config = RetryConfig.for_operation("unknown_operation")
        assert config.max_retries == 3  # 使用默认值


class TestCircuitBreaker:
    """CircuitBreaker 熔断器测试"""
    
    def test_initial_state(self):
        """测试初始状态"""
        cb = CircuitBreaker(failure_threshold=3, recovery_timeout=10.0)
        assert cb.state == "closed"
        assert cb.failure_count == 0
        assert cb.can_execute() is True
    
    def test_record_success_resets_count(self):
        """测试成功记录重置计数"""
        cb = CircuitBreaker(failure_threshold=3)
        cb.failure_count = 2
        cb.record_success()
        assert cb.failure_count == 0
    
    def test_record_failure_increases_count(self):
        """测试失败记录增加计数"""
        cb = CircuitBreaker(failure_threshold=3)
        cb.record_failure()
        assert cb.failure_count == 1
        assert cb.state == "closed"
    
    def test_circuit_opens_after_threshold(self):
        """测试达到阈值后熔断器打开"""
        cb = CircuitBreaker(failure_threshold=3)
        cb.record_failure()
        cb.record_failure()
        cb.record_failure()
        assert cb.state == "open"
        assert cb.can_execute() is False
    
    def test_half_open_after_recovery_timeout(self):
        """测试恢复超时后进入半开状态"""
        cb = CircuitBreaker(failure_threshold=2, recovery_timeout=0.1)
        cb.record_failure()
        cb.record_failure()
        assert cb.state == "open"
        
        # 等待恢复超时
        time.sleep(0.15)
        assert cb.can_execute() is True
        assert cb.state == "half_open"
    
    def test_half_open_to_closed_on_success(self):
        """测试半开状态成功转为关闭"""
        cb = CircuitBreaker(failure_threshold=2, recovery_timeout=0.1)
        cb.record_failure()
        cb.record_failure()
        time.sleep(0.15)
        cb.can_execute()  # 进入 half_open
        cb.record_success()
        assert cb.state == "closed"
    
    def test_half_open_to_open_on_failure(self):
        """测试半开状态失败转回打开"""
        cb = CircuitBreaker(failure_threshold=2, recovery_timeout=0.1)
        cb.record_failure()
        cb.record_failure()
        time.sleep(0.15)
        cb.can_execute()  # 进入 half_open
        cb.record_failure()
        assert cb.state == "open"
    
    def test_get_status(self):
        """测试状态获取"""
        cb = CircuitBreaker(failure_threshold=5, recovery_timeout=30.0)
        cb.record_failure()
        status = cb.get_status()
        assert status["state"] == "closed"
        assert status["failure_count"] == 1
        assert status["failure_threshold"] == 5
    
    def test_reset(self):
        """测试手动重置"""
        cb = CircuitBreaker(failure_threshold=3)
        cb.record_failure()
        cb.record_failure()
        cb.reset()
        assert cb.failure_count == 0
        assert cb.state == "closed"


class TestCalculateDelay:
    """_calculate_delay 函数测试"""
    
    def test_fixed_strategy(self):
        """测试固定退避"""
        delay = _calculate_delay(BackoffStrategy.FIXED, 5, 1.0, 10.0)
        assert delay == 1.0
    
    def test_linear_strategy(self):
        """测试线性退避"""
        delay = _calculate_delay(BackoffStrategy.LINEAR, 3, 1.0, 10.0)
        assert delay == 3.0
    
    def test_exponential_strategy(self):
        """测试指数退避"""
        # base=2.0, attempt=3: 2^3 = 8.0
        delay = _calculate_delay(BackoffStrategy.EXPONENTIAL, 3, 2.0, 10.0)
        assert delay == 8.0
    
    def test_exponential_jitter_strategy(self):
        """测试指数退避加抖动"""
        # base=2.0, attempt=2: 2^2 = 4.0, jitter 50%~150% → 2.0~6.0
        delay = _calculate_delay(BackoffStrategy.EXPONENTIAL_JITTER, 2, 2.0, 10.0)
        assert 2.0 <= delay <= 6.0
    
    def test_max_delay_cap(self):
        """测试最大延迟上限"""
        # base=2.0, attempt=10: 2^10 = 1024, capped at 5.0
        delay = _calculate_delay(BackoffStrategy.EXPONENTIAL, 10, 2.0, 5.0)
        assert delay == 5.0


class TestRetryOperation:
    """retry_operation 同步重试测试"""
    
    def test_success_on_first_try(self):
        """测试第一次尝试成功"""
        call_count = [0]
        
        def success_func():
            call_count[0] += 1
            return "result"
        
        result = retry_operation(success_func, operation="test")
        assert result == "result"
        assert call_count[0] == 1
    
    def test_retry_on_failure(self):
        """测试失败后重试"""
        call_count = [0]
        
        def failing_then_success():
            call_count[0] += 1
            if call_count[0] < 3:
                raise ElementNotFoundError(selector="#btn")
            return "success"
        
        config = RetryConfig(max_retries=3, base_delay=0.01)
        result = retry_operation(failing_then_success, config=config, operation="test")
        assert result == "success"
        assert call_count[0] == 3
    
    def test_exhaust_retries(self):
        """测试重试耗尽后抛出异常"""
        call_count = [0]
        
        def always_fails():
            call_count[0] += 1
            raise ElementNotFoundError(selector="#missing")
        
        config = RetryConfig(max_retries=2, base_delay=0.01)
        with pytest.raises(ElementNotFoundError):
            retry_operation(always_fails, config=config, operation="test")
        assert call_count[0] == 3  # 1 + max_retries
    
    def test_non_retryable_error(self):
        """测试非可重试错误直接抛出"""
        call_count = [0]
        
        def raises_value_error():
            call_count[0] += 1
            raise ValueError("unexpected error")
        
        with pytest.raises(ValueError):
            retry_operation(raises_value_error, operation="test")
        assert call_count[0] == 1  # 不重试
    
    def test_circuit_breaker_blocks(self):
        """测试熔断器阻止执行"""
        cb = CircuitBreaker(failure_threshold=2, recovery_timeout=30.0)
        cb.record_failure()
        cb.record_failure()
        assert cb.state == "open"
        
        def should_not_run():
            return "result"
        
        config = RetryConfig(circuit_breaker=True)
        with pytest.raises(CircuitBreakerOpenError):
            retry_operation(should_not_run, config=config, circuit_breaker=cb, operation="test")
    
    def test_on_retry_callback(self):
        """测试 on_retry 回调"""
        retry_calls = []
        
        def failing_func():
            raise ElementNotFoundError(selector="#btn")
        
        def on_retry(attempt, error, delay):
            retry_calls.append((attempt, str(error)))
        
        config = RetryConfig(max_retries=2, base_delay=0.01, on_retry=on_retry)
        with pytest.raises(ElementNotFoundError):
            retry_operation(failing_func, config=config, operation="test")
        
        assert len(retry_calls) == 2
        assert retry_calls[0][0] == 1
    
    def test_on_exhausted_callback(self):
        """测试 on_exhausted 回调"""
        exhausted_calls = []
        
        def always_fails():
            raise ElementNotFoundError(selector="#btn")
        
        def on_exhausted(error):
            exhausted_calls.append(str(error))
        
        config = RetryConfig(max_retries=1, base_delay=0.01, on_exhausted=on_exhausted)
        with pytest.raises(ElementNotFoundError):
            retry_operation(always_fails, config=config, operation="test")
        
        assert len(exhausted_calls) == 1


class TestRetryOperationAsync:
    """retry_operation_async 异步重试测试"""
    
    def test_success_on_first_try(self):
        """测试异步第一次成功"""
        async def success_func():
            return "result"
        
        result = asyncio.run(retry_operation_async(success_func, operation="test"))
        assert result == "result"
    
    def test_retry_on_failure(self):
        """测试异步失败后重试"""
        call_count = [0]
        
        async def failing_then_success():
            call_count[0] += 1
            if call_count[0] < 3:
                raise ElementNotFoundError(selector="#btn")
            return "success"
        
        config = RetryConfig(max_retries=3, base_delay=0.01)
        result = asyncio.run(retry_operation_async(failing_then_success, config=config, operation="test"))
        assert result == "success"
        assert call_count[0] == 3
    
    def test_exhaust_retries_async(self):
        """测试异步重试耗尽"""
        call_count = [0]
        
        async def always_fails():
            call_count[0] += 1
            raise ElementNotFoundError(selector="#missing")
        
        config = RetryConfig(max_retries=2, base_delay=0.01)
        with pytest.raises(ElementNotFoundError):
            asyncio.run(retry_operation_async(always_fails, config=config, operation="test"))
        assert call_count[0] == 3
    
    def test_circuit_breaker_async(self):
        """测试异步熔断器"""
        cb = CircuitBreaker(failure_threshold=2, recovery_timeout=30.0)
        cb.record_failure()
        cb.record_failure()
        
        async def should_not_run():
            return "result"
        
        config = RetryConfig(circuit_breaker=True)
        with pytest.raises(CircuitBreakerOpenError):
            asyncio.run(retry_operation_async(should_not_run, config=config, circuit_breaker=cb, operation="test"))


class TestWithRetryDecorator:
    """with_retry 装饰器测试"""
    
    def test_decorator_success(self):
        """测试装饰器成功场景"""
        @with_retry(max_retries=2, base_delay=0.01, operation="test")
        def my_func():
            return "result"
        
        result = my_func()
        assert result == "result"
    
    def test_decorator_retry(self):
        """测试装饰器重试场景"""
        call_count = [0]
        
        @with_retry(max_retries=3, base_delay=0.01, operation="test")
        def failing_func():
            call_count[0] += 1
            if call_count[0] < 2:
                raise ElementNotFoundError(selector="#btn")
            return "success"
        
        result = failing_func()
        assert result == "success"
        assert call_count[0] == 2


class TestWithRetryAsyncDecorator:
    """with_retry_async 装饰器测试"""
    
    def test_async_decorator_success(self):
        """测试异步装饰器成功"""
        @with_retry_async(max_retries=2, base_delay=0.01, operation="test")
        async def my_func():
            return "result"
        
        result = asyncio.run(my_func())
        assert result == "result"
    
    def test_async_decorator_retry(self):
        """测试异步装饰器重试"""
        call_count = [0]
        
        @with_retry_async(max_retries=3, base_delay=0.01, operation="test")
        async def failing_func():
            call_count[0] += 1
            if call_count[0] < 2:
                raise ElementNotFoundError(selector="#btn")
            return "success"
        
        result = asyncio.run(failing_func())
        assert result == "success"
        assert call_count[0] == 2


class TestGetRetryConfig:
    """get_retry_config 函数测试"""
    
    def test_known_operation(self):
        """测试已知操作类型"""
        config = get_retry_config("cdp_command")
        assert config.max_retries == 5
    
    def test_unknown_operation(self):
        """测试未知操作类型返回默认配置"""
        config = get_retry_config("unknown_op")
        assert config.max_retries == 3


if __name__ == "__main__":
    pytest.main([__file__, '-v'])
