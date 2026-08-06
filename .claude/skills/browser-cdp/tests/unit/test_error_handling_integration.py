"""
test_error_handling_integration.py - 错误处理和重试机制集成测试

测试覆盖：
- browser_browse.py 中的错误处理装饰器
- browser_interaction.py 中的错误处理装饰器
- 重试逻辑验证
- 错误分类验证
"""
import pytest
import sys
import asyncio
import time
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock, patch

# 添加 skill 目录到路径
SKILL_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from src.reliability.error import (
    ReliabilityError,
    CDPConnectionLostError,
    ElementNotFoundError,
    NavigationTimeoutError,
    ErrorCategory,
    is_retryable,
    categorize_error,
)
from src.reliability.retry import (
    RetryConfig,
    CircuitBreaker,
    retry_operation,
    retry_operation_async,
    BackoffStrategy,
)
from src.reliability.middleware import (
    ErrorMiddleware,
    ErrorContext,
    OperationType,
    with_error_handling,
    with_error_handling_async,
)


class TestErrorClassification:
    """错误分类测试"""
    
    def test_is_retryable_connection_error(self):
        """测试连接错误可重试"""
        error = CDPConnectionLostError()
        assert is_retryable(error) is True
    
    def test_is_retryable_element_not_found(self):
        """测试元素未找到可重试"""
        error = ElementNotFoundError(selector="#btn")
        assert is_retryable(error) is True
    
    def test_is_retryable_navigation_timeout(self):
        """测试导航超时可重试"""
        error = NavigationTimeoutError(url="http://test.com", timeout=10.0)
        assert is_retryable(error) is True
    
    def test_is_retryable_non_retryable(self):
        """测试不可重试错误"""
        error = ValueError("Unexpected error")
        assert is_retryable(error) is False
    
    def test_categorize_error(self):
        """测试错误分类"""
        error = CDPConnectionLostError()
        category = categorize_error(error)
        assert category == ErrorCategory.CONNECTION
        
        error = ElementNotFoundError(selector="#btn")
        category = categorize_error(error)
        assert category == ErrorCategory.ELEMENT


class TestRetryIntegration:
    """重试集成测试"""
    
    def test_sync_retry_success_after_failures(self):
        """测试同步重试成功后返回结果"""
        call_count = [0]
        
        def failing_func():
            call_count[0] += 1
            if call_count[0] < 3:
                raise ElementNotFoundError(selector="#btn")
            return "success"
        
        config = RetryConfig(max_retries=3, base_delay=0.01)
        result = retry_operation(failing_func, config=config, operation="test")
        assert result == "success"
        assert call_count[0] == 3
    
    def test_sync_retry_exhausted(self):
        """测试同步重试耗尽后抛出异常"""
        call_count = [0]
        
        def always_fails():
            call_count[0] += 1
            raise ElementNotFoundError(selector="#missing")
        
        config = RetryConfig(max_retries=2, base_delay=0.01)
        with pytest.raises(ElementNotFoundError):
            retry_operation(always_fails, config=config, operation="test")
        assert call_count[0] == 3
    
    @pytest.mark.asyncio
    async def test_async_retry_success_after_failures(self):
        """测试异步重试成功后返回结果"""
        call_count = [0]
        
        async def failing_func():
            call_count[0] += 1
            if call_count[0] < 3:
                raise ElementNotFoundError(selector="#btn")
            return "success"
        
        config = RetryConfig(max_retries=3, base_delay=0.01)
        result = await retry_operation_async(failing_func, config=config, operation="test")
        assert result == "success"
        assert call_count[0] == 3
    
    @pytest.mark.asyncio
    async def test_async_retry_exhausted(self):
        """测试异步重试耗尽后抛出异常"""
        call_count = [0]
        
        async def always_fails():
            call_count[0] += 1
            raise ElementNotFoundError(selector="#missing")
        
        config = RetryConfig(max_retries=2, base_delay=0.01)
        with pytest.raises(ElementNotFoundError):
            await retry_operation_async(always_fails, config=config, operation="test")
        assert call_count[0] == 3


class TestMiddlewareIntegration:
    """中间件集成测试"""
    
    def test_wrap_sync_decorator(self):
        """测试同步装饰器"""
        middleware = ErrorMiddleware()
        
        @middleware.wrap_sync("test_op", OperationType.CLICK, max_retries=2)
        def test_func():
            return "result"
        
        result = test_func()
        assert result == "result"
    
    def test_wrap_sync_decorator_with_retry(self):
        """测试同步装饰器带重试"""
        middleware = ErrorMiddleware()
        call_count = [0]
        
        @middleware.wrap_sync("test_op", OperationType.CLICK, max_retries=3)
        def failing_func():
            call_count[0] += 1
            if call_count[0] < 2:
                raise ElementNotFoundError(selector="#btn")
            return "success"
        
        result = failing_func()
        assert result == "success"
        assert call_count[0] == 2
    
    @pytest.mark.asyncio
    async def test_wrap_async_decorator(self):
        """测试异步装饰器"""
        middleware = ErrorMiddleware()
        
        @middleware.wrap_async("test_op", OperationType.CLICK, max_retries=2)
        async def test_func():
            return "result"
        
        result = await test_func()
        assert result == "result"
    
    @pytest.mark.asyncio
    async def test_wrap_async_decorator_with_retry(self):
        """测试异步装饰器带重试"""
        middleware = ErrorMiddleware()
        call_count = [0]
        
        @middleware.wrap_async("test_op", OperationType.CLICK, max_retries=3)
        async def failing_func():
            call_count[0] += 1
            if call_count[0] < 2:
                raise ElementNotFoundError(selector="#btn")
            return "success"
        
        result = await failing_func()
        assert result == "success"
        assert call_count[0] == 2


class TestErrorContext:
    """错误上下文测试"""
    
    def test_create_context(self):
        """测试创建错误上下文"""
        context = ErrorContext(
            operation="test_op",
            operation_type=OperationType.CLICK,
            attempt=1,
            max_attempts=3,
        )
        assert context.operation == "test_op"
        assert context.attempt == 1
        assert context.max_attempts == 3
    
    def test_context_to_dict(self):
        """测试上下文转字典"""
        context = ErrorContext(
            operation="test_op",
            operation_type=OperationType.CLICK,
            attempt=1,
            max_attempts=3,
            error=ElementNotFoundError(selector="#btn"),
            category=ErrorCategory.ELEMENT,
        )
        d = context.to_dict()
        assert d["operation"] == "test_op"
        assert d["attempt"] == 1
        assert d["category"] == "element"


class TestCircuitBreakerIntegration:
    """熔断器集成测试"""
    
    def test_circuit_breaker_integration(self):
        """测试熔断器集成"""
        cb = CircuitBreaker(failure_threshold=2, recovery_timeout=0.1)
        
        # 记录两次失败
        cb.record_failure()
        cb.record_failure()
        assert cb.state == "open"
        
        # 等待恢复
        time.sleep(0.15)
        assert cb.can_execute() is True
        assert cb.state == "half_open"
        
        # 成功恢复
        cb.record_success()
        assert cb.state == "closed"


if __name__ == "__main__":
    pytest.main([__file__, '-v'])
