# -*- coding: utf-8 -*-
"""
CDP 异常处理模块单元测试 - 简化版

测试覆盖：
- CDPExceptionHandler 类
- CDPExceptionContext 数据类
- CDPOperationType 枚举
- 装饰器：with_cdp_exception_handling
- 上下文管理器：cdp_operation_context
- 包装函数：wrap_cdp_call, CDPTimedOperation
"""

import asyncio
import pytest
import time
from typing import Optional

import sys
from pathlib import Path

# 添加 skill 目录到路径
SKILL_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from src.reliability.cdp.cdp_exception_handler import (
    CDPExceptionHandler,
    CDPExceptionContext,
    CDPOperationType,
    get_cdp_exception_handler,
    reset_cdp_exception_handler,
    with_cdp_exception_handling,
    async_with_cdp_exception_handling,
    cdp_operation_context,
    wrap_cdp_call,
    CDPTimedOperation,
    connect_cdp,
    navigate_cdp,
    eval_js_cdp,
    query_selector_cdp,
)
from src.reliability.error import (
    ErrorCategory,
    ReliabilityError,
    CDPConnectionLostError,
    CDPCommandTimeoutError,
    ElementNotFoundError,
    NavigationTimeoutError,
    CaptchaDetectedError,
    is_retryable,
    categorize_error,
)


class TestCDPExceptionContext:
    """CDPExceptionContext 数据类测试"""
    
    def test_init_default(self):
        ctx = CDPExceptionContext()
        assert ctx.operation == ""
        assert ctx.operation_type == CDPOperationType.CUSTOM
        assert ctx.target_url == ""
        assert ctx.selector == ""
        assert ctx.method == ""
        assert ctx.start_time == 0.0
        assert ctx.end_time == 0.0
        assert ctx.attempt == 0
        assert ctx.max_attempts == 3
        assert ctx.error_history == []
    
    def test_init_with_params(self):
        ctx = CDPExceptionContext(
            operation="search",
            operation_type=CDPOperationType.QUERY_SELECTOR,
            target_url="https://example.com",
            selector="#input",
            method="Runtime.evaluate",
            params={"expression": "document.title"},
            max_attempts=5,
        )
        assert ctx.operation == "search"
        assert ctx.operation_type == CDPOperationType.QUERY_SELECTOR
        assert ctx.target_url == "https://example.com"
        assert ctx.selector == "#input"
    
    def test_record_error(self):
        ctx = CDPExceptionContext()
        error = CDPConnectionLostError()
        ctx.record_error(error, ErrorCategory.CONNECTION)
        assert len(ctx.error_history) == 1
        assert ctx.error_history[0]["error_type"] == "CDPConnectionLostError"
        assert ctx.error_history[0]["category"] == "connection"
        assert "timestamp" in ctx.error_history[0]
    
    def test_to_dict(self):
        ctx = CDPExceptionContext(
            operation="test_op",
            operation_type=CDPOperationType.NAVIGATE,
            target_url="https://test.com",
            start_time=1000.0,
            end_time=1005.0,
            attempt=2,
            max_attempts=3,
        )
        ctx.record_error(ValueError("test"), ErrorCategory.UNKNOWN)
        result = ctx.to_dict()
        assert result["operation"] == "test_op"
        assert result["operation_type"] == "navigate"
        assert result["target_url"] == "https://test.com"
        assert result["duration"] == 5.0
        assert result["attempt"] == 2
        assert result["error_count"] == 1
        assert result["last_error"]["error_type"] == "ValueError"


class TestCDPOperationType:
    """CDPOperationType 枚举测试"""
    
    def test_all_types_exist(self):
        assert hasattr(CDPOperationType, "CONNECT")
        assert hasattr(CDPOperationType, "DISCONNECT")
        assert hasattr(CDPOperationType, "NAVIGATE")
        assert hasattr(CDPOperationType, "EVAL_JS")
        assert hasattr(CDPOperationType, "QUERY_SELECTOR")
        assert hasattr(CDPOperationType, "CLICK")
        assert hasattr(CDPOperationType, "TYPE")
        assert hasattr(CDPOperationType, "SCROLL")
        assert hasattr(CDPOperationType, "SCREENSHOT")
    
    def test_values(self):
        assert CDPOperationType.CONNECT.value == "connect"
        assert CDPOperationType.NAVIGATE.value == "navigate"
        assert CDPOperationType.EVAL_JS.value == "eval_js"


class TestCDPExceptionHandler:
    """CDPExceptionHandler 类测试"""
    
    def setup_method(self):
        reset_cdp_exception_handler()
    
    def test_init_defaults(self):
        handler = CDPExceptionHandler()
        assert handler.default_max_retries == 3
        assert handler.default_timeout == 30.0
    
    def test_handle_success(self):
        handler = CDPExceptionHandler()
        
        def test_func():
            return "success"
        
        decorated = handler.handle(operation="test_op")(test_func)
        result = decorated()
        assert result == "success"
    
    def test_handle_with_exception(self):
        handler = CDPExceptionHandler(default_max_retries=3)
        call_count = 0
        
        def test_func():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise CDPConnectionLostError()
            return "success_after_retry"
        
        decorated = handler.handle(operation="test_op")(test_func)
        result = decorated()
        assert result == "success_after_retry"
        assert call_count == 3
    
    def test_handle_max_retries_exceeded(self):
        handler = CDPExceptionHandler(default_max_retries=2)
        call_count = 0

        def test_func():
            nonlocal call_count
            call_count += 1
            raise CDPConnectionLostError()  # 可重试，会重试 max_retries 次

        decorated = handler.handle(operation="test_op")(test_func)
        with pytest.raises(CDPConnectionLostError):
            decorated()
        assert call_count == 2

    def test_handle_all_retries_exhausted_then_raised(self):
        """测试所有重试耗尽后正确 re-raise 异常"""
        handler = CDPExceptionHandler(default_max_retries=2)
        call_count = 0

        def failing_func():
            nonlocal call_count
            call_count += 1
            raise ElementNotFoundError("#missing")

        decorated = handler.handle(operation="exhaust_op")(failing_func)
        with pytest.raises(ElementNotFoundError):
            decorated()
        assert call_count == 2
        # 确认错误历史已记录
        stats = handler.get_stats("exhaust_op")
        assert any(s["type"] == "failure" for s in stats)
    
    def test_handle_non_retryable_error(self):
        handler = CDPExceptionHandler(default_max_retries=3)
        call_count = 0
        
        def test_func():
            nonlocal call_count
            call_count += 1
            raise CaptchaDetectedError()
        
        decorated = handler.handle(operation="test_op")(test_func)
        with pytest.raises(CaptchaDetectedError):
            decorated()
        assert call_count == 1
    
    def test_get_stats(self):
        handler = CDPExceptionHandler()
        
        def test_func():
            return "success"
        
        decorated = handler.handle(operation="test_op")(test_func)
        decorated()
        stats = handler.get_stats("test_op")
        assert len(stats) > 0
        assert stats[0]["type"] == "success"
    
    def test_reset_stats(self):
        handler = CDPExceptionHandler()
        
        def test_func():
            return "success"
        
        decorated = handler.handle(operation="test_op")(test_func)
        decorated()
        assert len(handler.get_stats("test_op")) > 0
        handler.reset_stats("test_op")
        assert handler.get_stats("test_op") == []
    
    def test_calculate_wait_time(self):
        handler = CDPExceptionHandler()
        wait_time = handler._calculate_wait_time(1, ErrorCategory.CONNECTION)
        assert wait_time >= 1.0
        wait_time_2 = handler._calculate_wait_time(2, ErrorCategory.CONNECTION)
        assert wait_time_2 > wait_time
    
    def test_global_get_handler(self):
        handler1 = get_cdp_exception_handler()
        handler2 = get_cdp_exception_handler()
        assert handler1 is handler2
        reset_cdp_exception_handler()


class TestDecorators:
    """装饰器测试"""
    
    def setup_method(self):
        reset_cdp_exception_handler()
    
    def test_with_cdp_exception_handling_decorator(self):
        @with_cdp_exception_handling("search", CDPOperationType.QUERY_SELECTOR, selector="#input")
        def search_func(query: str) -> str:
            return f"results for {query}"
        
        result = search_func("test")
        assert result == "results for test"
        assert search_func.__name__ == "search_func"
    
    def test_decorator_with_retry(self):
        call_count = 0
        
        @with_cdp_exception_handling("retry_test", max_retries=3)
        def retry_func():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise CDPConnectionLostError()
            return "success"
        
        result = retry_func()
        assert result == "success"
        assert call_count == 3
    
    def test_decorator_custom_timeout(self):
        @with_cdp_exception_handling("timeout_test", timeout=60.0)
        def timeout_func():
            return "ok"
        
        result = timeout_func()
        assert result == "ok"


class TestAsyncDecorators:
    """异步装饰器测试"""
    
    def setup_method(self):
        reset_cdp_exception_handler()
    
    def test_async_decorator_success(self):
        @async_with_cdp_exception_handling("async_search")
        async def async_search_func(query: str) -> str:
            return f"async results for {query}"
        
        result = asyncio.run(async_search_func("test"))
        assert result == "async results for test"
    
    def test_async_decorator_with_retry(self):
        call_count = 0

        @async_with_cdp_exception_handling("async_retry", max_retries=3)
        async def async_retry_func():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise CDPConnectionLostError()
            return "async_success"

        result = asyncio.run(async_retry_func())
        assert result == "async_success"
        assert call_count == 3

    def test_async_decorator_non_retryable(self):
        """测试异步装饰器不可重试错误立即抛出"""
        call_count = 0

        @async_with_cdp_exception_handling("async_noretry", max_retries=3)
        async def async_noretry_func():
            nonlocal call_count
            call_count += 1
            raise CaptchaDetectedError()

        with pytest.raises(CaptchaDetectedError):
            asyncio.run(async_noretry_func())
        assert call_count == 1

    def test_async_decorator_max_retries_exhausted(self):
        """测试异步装饰器重试耗尽后抛出"""
        call_count = 0

        @async_with_cdp_exception_handling("async_exhaust", max_retries=2)
        async def async_exhaust_func():
            nonlocal call_count
            call_count += 1
            raise CDPCommandTimeoutError("Runtime.evaluate", 15.0)

        with pytest.raises(CDPCommandTimeoutError):
            asyncio.run(async_exhaust_func())
        assert call_count == 2


class TestCDPOperationContext:
    """cdp_operation_context 上下文管理器测试"""
    
    def setup_method(self):
        reset_cdp_exception_handler()
    
    def test_context_success(self):
        with cdp_operation_context("test_op", CDPOperationType.NAVIGATE) as ctx:
            assert ctx.operation == "test_op"
            assert ctx.operation_type == CDPOperationType.NAVIGATE
            assert ctx.start_time > 0
            time.sleep(0.01)
        assert ctx.end_time > 0
    
    def test_context_with_exception(self):
        ctx = None
        try:
            with cdp_operation_context("error_op") as ctx:
                raise ValueError("test error")
        except ValueError:
            pass
        assert ctx is not None
        assert len(ctx.error_history) == 1
        assert ctx.error_history[0]["error_type"] == "ValueError"
    
    def test_context_with_kwargs(self):
        with cdp_operation_context(
            "search",
            CDPOperationType.QUERY_SELECTOR,
            target_url="https://example.com",
            selector="#input",
        ) as ctx:
            assert ctx.target_url == "https://example.com"
            assert ctx.selector == "#input"


class TestWrapCDPCall:
    """wrap_cdp_call 函数测试"""
    
    def setup_method(self):
        reset_cdp_exception_handler()
    
    def test_wrap_success(self):
        def mock_cdp_call():
            return {"result": "ok"}
        
        wrapped = wrap_cdp_call(mock_cdp_call, "test_op", CDPOperationType.CUSTOM)
        result = wrapped()
        assert result == {"result": "ok"}
    
    def test_wrap_with_retry(self):
        call_count = 0
        
        def mock_cdp_call():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise CDPConnectionLostError()
            return {"result": "after_retry"}
        
        wrapped = wrap_cdp_call(
            mock_cdp_call,
            "retry_op",
            max_retries=3,
        )
        result = wrapped()
        assert result == {"result": "after_retry"}
        assert call_count == 2


class TestCDPTimedOperation:
    """CDPTimedOperation 类测试"""
    
    def setup_method(self):
        reset_cdp_exception_handler()
    
    def test_execute_success(self):
        timed_op = CDPTimedOperation(timeout=5.0, max_retries=2)
        
        def mock_func():
            return "executed"
        
        result = timed_op.execute(mock_func, "test_op", CDPOperationType.CUSTOM)
        assert result == "executed"
    
    def test_execute_with_retry(self):
        timed_op = CDPTimedOperation(timeout=5.0, max_retries=3)
        call_count = 0
        
        def mock_func():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise CDPCommandTimeoutError("test", 5.0)
            return "success"
        
        result = timed_op.execute(mock_func, "retry_op")
        assert result == "success"
        assert call_count == 2
    
    def test_execute_timeout(self):
        timed_op = CDPTimedOperation(timeout=0.1, max_retries=1)
        
        def slow_func():
            time.sleep(0.2)
            return "done"
        
        with pytest.raises(TimeoutError):
            timed_op.execute(slow_func, "slow_op")
    
    @pytest.mark.asyncio
    async def test_execute_async_success(self):
        timed_op = CDPTimedOperation(timeout=5.0, max_retries=2)
        
        async def mock_async_func():
            return "async_executed"
        
        result = await timed_op.execute_async(mock_async_func, "async_op")
        assert result == "async_executed"
    
    @pytest.mark.asyncio
    async def test_execute_async_with_retry(self):
        timed_op = CDPTimedOperation(timeout=5.0, max_retries=3)
        call_count = 0
        
        async def mock_async_func():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise CDPConnectionLostError()
            return "async_success"
        
        result = await timed_op.execute_async(mock_async_func, "async_retry_op")
        assert result == "async_success"
        assert call_count == 2


class TestEdgeCases:
    """边缘情况测试"""
    
    def setup_method(self):
        reset_cdp_exception_handler()
    
    def test_handle_none_result(self):
        handler = CDPExceptionHandler()
        
        def none_func():
            return None
        none_func = handler.handle(operation="none_op")(none_func)
        result = none_func()
        assert result is None
    
    def test_handle_exception_with_details(self):
        handler = CDPExceptionHandler(default_max_retries=1)
        
        def details_func():
            raise CDPCommandTimeoutError("Runtime.evaluate", 30.0, details={"expr": "document.title"})
        
        details_func = handler.handle(operation="details_op")(details_func)
        with pytest.raises(CDPCommandTimeoutError) as exc_info:
            details_func()
        assert exc_info.value.command == "Runtime.evaluate"
        assert exc_info.value.timeout == 30.0
    
    def test_concurrent_operations(self):
        handler = CDPExceptionHandler()
        
        def concurrent_func():
            return "ok"
        concurrent_func = handler.handle(operation="concurrent_op")(concurrent_func)
        
        results = [concurrent_func() for _ in range(5)]
        assert all(r == "ok" for r in results)
        
        stats = handler.get_stats("concurrent_op")
        assert len(stats) == 5
    
    def test_retry_count_tracking(self):
        handler = CDPExceptionHandler(default_max_retries=5)
        call_count = 0
        
        def tracking_func():
            nonlocal call_count
            call_count += 1
            if call_count < 4:
                raise CDPConnectionLostError()
            return "recovered"
        
        tracking_func = handler.handle(operation="tracking_op")(tracking_func)
        result = tracking_func()
        assert result == "recovered"
        assert call_count == 4


class TestIntegration:
    """集成测试"""
    
    def setup_method(self):
        reset_cdp_exception_handler()
    
    def test_full_workflow_with_retry(self):
        handler = CDPExceptionHandler(default_max_retries=3)
        
        sequence = [
            CDPConnectionLostError(),
            ElementNotFoundError("#btn"),
            None,
        ]
        index = [0]
        
        def workflow_func():
            error = sequence[index[0]]
            index[0] += 1
            if error:
                raise error
            return "workflow_complete"
        
        workflow_func = handler.handle(operation="workflow_test")(workflow_func)
        result = workflow_func()
        assert result == "workflow_complete"
        assert index[0] == 3
    
    def test_error_history_accumulation(self):
        handler = CDPExceptionHandler(default_max_retries=3)
        
        errors = [
            CDPConnectionLostError(),
            CDPCommandTimeoutError("test", 30.0),
        ]
        
        call_count = 0
        
        def history_func():
            nonlocal call_count
            if call_count < len(errors):
                raise errors[call_count]
            call_count += 1
            return "done"
        
        history_func = handler.handle(operation="history_test")(history_func)
        
        # 第一次调用会失败2次并累积错误历史
        with pytest.raises(Exception):
            history_func()
        
        stats = handler.get_stats("history_test")
        assert len(stats) > 0


class TestPredefinedWrappers:
    """预定义 CDP 包装函数测试"""

    def setup_method(self):
        reset_cdp_exception_handler()

    def test_connect_cdp_signature(self):
        """测试 connect_cdp 包装器签名正确（pass 占位）"""
        # connect_cdp 是 pass 占位，仅验证装饰器不会报错
        import inspect
        sig = inspect.signature(connect_cdp)
        assert "ws_url" in sig.parameters
        assert "timeout" in sig.parameters

    def test_navigate_cdp_signature(self):
        """测试 navigate_cdp 包装器签名"""
        import inspect
        sig = inspect.signature(navigate_cdp)
        assert "session" in sig.parameters
        assert "url" in sig.parameters

    def test_eval_js_cdp_signature(self):
        """测试 eval_js_cdp 包装器签名"""
        import inspect
        sig = inspect.signature(eval_js_cdp)
        assert "expression" in sig.parameters

    def test_query_selector_cdp_signature(self):
        """测试 query_selector_cdp 包装器签名"""
        import inspect
        sig = inspect.signature(query_selector_cdp)
        assert "selector" in sig.parameters


class TestIsRetryableAndCategorize:
    """is_retryable 和 categorize_error 辅助函数测试"""

    def test_is_retryable_reliability_error_recoverable(self):
        assert is_retryable(CDPConnectionLostError()) is True
        assert is_retryable(ElementNotFoundError("#btn")) is True
        assert is_retryable(NavigationTimeoutError("https://x.com", 30.0)) is True

    def test_is_retryable_reliability_error_not_recoverable(self):
        assert is_retryable(CaptchaDetectedError()) is False

    def test_is_retryable_cdp_name_heuristic(self):
        class FakeCDPError(Exception):
            pass
        assert is_retryable(FakeCDPError()) is True

    def test_is_retryable_unknown_returns_false(self):
        assert is_retryable(ValueError("random")) is False

    def test_categorize_connection(self):
        assert categorize_error(CDPConnectionLostError()) == ErrorCategory.CONNECTION
        assert categorize_error(ElementNotFoundError("#btn")) == ErrorCategory.ELEMENT
        assert categorize_error(NavigationTimeoutError("http://x", 10)) == ErrorCategory.NAVIGATION
        assert categorize_error(CaptchaDetectedError()) == ErrorCategory.CONTENT
        assert categorize_error(ValueError("unknown")) == ErrorCategory.UNKNOWN

    def test_categorize_by_name_heuristics(self):
        class PageLoadTimeoutError(Exception):
            pass
        class PageLoadError(Exception):
            pass
        class PageNavigationError(Exception):
            pass
        assert categorize_error(PageLoadTimeoutError()) == ErrorCategory.TIMEOUT
        assert categorize_error(PageLoadError()) == ErrorCategory.NAVIGATION
        assert categorize_error(PageNavigationError()) == ErrorCategory.NAVIGATION


class TestStatsTracking:
    """统计信息追踪测试"""

    def setup_method(self):
        reset_cdp_exception_handler()

    def test_success_recorded_with_duration(self):
        handler = CDPExceptionHandler()

        def quick_func():
            return 42

        decorated = handler.handle(operation="quick_op")(quick_func)
        decorated()
        stats = handler.get_stats("quick_op")
        assert stats[0]["type"] == "success"
        assert "duration" in stats[0]
        assert stats[0]["duration"] >= 0

    def test_failure_recorded_with_error(self):
        handler = CDPExceptionHandler(default_max_retries=1)

        def fail_func():
            raise CDPConnectionLostError()

        decorated = handler.handle(operation="fail_op")(fail_func)
        with pytest.raises(CDPConnectionLostError):
            decorated()
        stats = handler.get_stats("fail_op")
        assert any(s["type"] == "failure" for s in stats)

    def test_mixed_success_and_failure_stats(self):
        handler = CDPExceptionHandler(default_max_retries=1)

        def alternating():
            return "ok"

        decorated = handler.handle(operation="mixed_stats")(alternating)
        decorated()
        decorated()
        with pytest.raises(CDPConnectionLostError):
            def bad():
                raise CDPConnectionLostError()
            bad = handler.handle(operation="bad_op")(bad)
            bad()

        all_stats = handler.get_stats()
        assert "mixed_stats" in all_stats
        assert "bad_op" in all_stats


if __name__ == "__main__":
    pytest.main([__file__, '-v', '--tb=short'])
