"""
ErrorMiddleware 单元测试

测试覆盖：
- ErrorContext 数据类
- OperationType 枚举
- ErrorMiddleware 核心功能
- with_error_handling 装饰器
- with_error_handling_async 装饰器
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

from src.reliability.middleware import (
    ErrorMiddleware,
    ErrorContext,
    OperationType,
    get_middleware,
    reset_middleware,
    with_error_handling,
    with_error_handling_async,
)
from src.reliability.error import (
    ReliabilityError,
    CDPConnectionLostError,
    ElementNotFoundError,
    ErrorCategory,
    CaptchaDetectedError,
)


class TestErrorContext:
    """ErrorContext 数据类测试"""
    
    def test_create_context(self):
        """测试创建错误上下文"""
        context = ErrorContext(
            operation="test_op",
            operation_type=OperationType.NAVIGATION,
            attempt=1,
            max_attempts=3,
        )
        assert context.operation == "test_op"
        assert context.operation_type == OperationType.NAVIGATION
        assert context.attempt == 1
        assert context.max_attempts == 3
        assert context.recoverable is True
    
    def test_to_dict(self):
        """测试序列化"""
        context = ErrorContext(
            operation="test_op",
            operation_type=OperationType.CLICK,
            attempt=2,
            max_attempts=3,
            error=ElementNotFoundError(selector="#btn"),
            category=ErrorCategory.ELEMENT,
            details={"selector": "#btn"},
        )
        d = context.to_dict()
        assert d["operation"] == "test_op"
        assert d["operation_type"] == "click"
        assert d["attempt"] == 2
        assert d["category"] == "element"
        assert d["recoverable"] is True
        assert "selector" in d["details"]
    
    def test_str_representation(self):
        """测试字符串表示"""
        context = ErrorContext(
            operation="navigate",
            operation_type=OperationType.NAVIGATION,
            attempt=1,
            max_attempts=3,
        )
        s = str(context)
        assert "navigate" in s
        assert "1/3" in s


class TestOperationType:
    """OperationType 枚举测试"""
    
    def test_all_types_exist(self):
        """测试所有操作类型存在"""
        assert hasattr(OperationType, 'NAVIGATION')
        assert hasattr(OperationType, 'SCREENSHOT')
        assert hasattr(OperationType, 'CLICK')
        assert hasattr(OperationType, 'INPUT')
        assert hasattr(OperationType, 'WAIT')
        assert hasattr(OperationType, 'EXTRACT')
        assert hasattr(OperationType, 'SCROLL')
        assert hasattr(OperationType, 'TAB')
        assert hasattr(OperationType, 'UNKNOWN')
    
    def test_type_values(self):
        """测试类型值"""
        assert OperationType.NAVIGATION.value == "navigation"
        assert OperationType.SCREENSHOT.value == "screenshot"
        assert OperationType.CLICK.value == "click"


class TestErrorMiddleware:
    """ErrorMiddleware 核心功能测试"""
    
    def setup_method(self):
        """每个测试前重置中间件"""
        reset_middleware()
    
    def test_get_middleware_singleton(self):
        """测试单例模式"""
        m1 = get_middleware()
        m2 = get_middleware()
        assert m1 is m2
    
    def test_wrap_sync_success(self):
        """测试同步函数包装 - 成功场景"""
        middleware = get_middleware()

        def navigate(url):
            return f"result_{url}"

        wrapped = middleware.wrap_sync(navigate, "test_nav", OperationType.NAVIGATION)
        result = wrapped("https://example.com")
        assert result == "result_https://example.com"

    def test_wrap_sync_retry_on_error(self):
        """测试同步函数包装 - 重试场景"""
        middleware = get_middleware()
        call_count = [0]

        def navigate(url):
            call_count[0] += 1
            if call_count[0] < 2:
                raise ElementNotFoundError(selector="#btn")
            return f"result_{url}"

        wrapped = middleware.wrap_sync(
            navigate,
            "test_nav",
            OperationType.NAVIGATION,
            max_retries=3
        )
        result = wrapped("https://example.com")
        assert result == "result_https://example.com"
        assert call_count[0] == 2

    def test_wrap_sync_exhaust_retries(self):
        """测试同步函数包装 - 重试耗尽"""
        middleware = get_middleware()

        def navigate(url):
            raise ElementNotFoundError(selector="#missing")

        wrapped = middleware.wrap_sync(
            navigate,
            "test_nav",
            OperationType.NAVIGATION,
            max_retries=2
        )

        with pytest.raises(ElementNotFoundError):
            wrapped("https://example.com")

    def test_wrap_async_success(self):
        """测试异步函数包装 - 成功场景"""
        middleware = get_middleware()

        async def navigate(url):
            return f"result_{url}"

        wrapped = middleware.wrap_async(
            navigate,
            "test_nav",
            OperationType.NAVIGATION
        )
        result = asyncio.run(wrapped("https://example.com"))
        assert result == "result_https://example.com"

    def test_wrap_async_retry(self):
        """测试异步函数包装 - 重试场景"""
        middleware = get_middleware()
        call_count = [0]

        async def navigate(url):
            call_count[0] += 1
            if call_count[0] < 2:
                raise ElementNotFoundError(selector="#btn")
            return f"result_{url}"

        wrapped = middleware.wrap_async(
            navigate,
            "test_nav",
            OperationType.NAVIGATION,
            max_retries=3
        )
        result = asyncio.run(wrapped("https://example.com"))
        assert result == "result_https://example.com"
        assert call_count[0] == 2
    
    def test_handle_error_recoverable(self):
        """测试可恢复错误处理"""
        middleware = get_middleware()
        error = ElementNotFoundError(selector="#btn")
        
        context = middleware.handle_error(error, "test_op", OperationType.CLICK)
        
        assert context.recoverable is True
        assert context.category == ErrorCategory.ELEMENT
        assert context.error is error
    
    def test_handle_error_unrecoverable(self):
        """测试不可恢复错误处理"""
        middleware = get_middleware()
        error = CaptchaDetectedError()
        
        context = middleware.handle_error(error, "test_op", OperationType.NAVIGATION)
        
        assert context.recoverable is False
        assert context.category == ErrorCategory.CONTENT
    
    def test_handle_error_unknown(self):
        """测试未知错误处理"""
        middleware = get_middleware()
        error = ValueError("unexpected error")
        
        context = middleware.handle_error(error, "test_op", OperationType.EXTRACT)
        
        assert context.recoverable is False
        assert context.category == ErrorCategory.UNKNOWN
    
    def test_circuit_breaker_integration(self):
        """测试熔断器集成"""
        middleware = get_middleware()
        
        # 获取熔断器
        cb1 = middleware.get_circuit_breaker("op1")
        cb2 = middleware.get_circuit_breaker("op1")
        
        # 同一个操作应该返回同一个熔断器实例
        assert cb1 is cb2
        
        # 不同操作应该返回不同熔断器实例
        cb3 = middleware.get_circuit_breaker("op2")
        assert cb1 is not cb3


class TestDecorators:
    """装饰器测试"""
    
    def setup_method(self):
        reset_middleware()
    
    def test_with_error_handling_decorator(self):
        """测试同步装饰器"""
        call_count = [0]
        
        @with_error_handling("test_op", OperationType.CLICK, max_retries=2)
        def click_btn():
            call_count[0] += 1
            if call_count[0] < 2:
                raise ElementNotFoundError(selector="#btn")
            return "clicked"
        
        result = click_btn()
        assert result == "clicked"
        assert call_count[0] == 2
    
    def test_with_error_handling_async_decorator(self):
        """测试异步装饰器"""
        call_count = [0]
        
        @with_error_handling_async("test_op", OperationType.EXTRACT, max_retries=2)
        async def extract_data():
            call_count[0] += 1
            if call_count[0] < 2:
                raise ElementNotFoundError(selector="#data")
            return "extracted"
        
        result = asyncio.run(extract_data())
        assert result == "extracted"
        assert call_count[0] == 2


if __name__ == "__main__":
    pytest.main([__file__, '-v'])
