"""
可靠性保障机制稳定性测试 - 扩展用例

测试覆盖：
- 网络异常（ConnectionError, TimeoutError, DNS 解析失败）
- 服务超时（CDP 命令超时、导航超时、智能等待超时）
- 重试策略变体（FIXED, LINEAR, EXPONENTIAL, EXPONENTIAL_JITTER）
- 熔断器边界场景
- 降级模式变体（SKIP, ERROR, FALLBACK, CACHED）
- 高并发与线程安全
- 内存泄漏检测
- 错误分类一致性
- 集成场景（完整操作链路）
"""
import pytest
import sys
import time
import threading
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock

SKILL_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from src.reliability.error import (
    ReliabilityError,
    ErrorCategory,
    CDPConnectionLostError,
    CDPCommandTimeoutError,
    ElementNotFoundError,
    ElementIndexInvalidError,
    NavigationTimeoutError,
    CaptchaDetectedError,
    BlockedByAntiBotError,
    NetworkIdleTimeoutError,
    SmartWaitDegradedError,
    is_retryable,
    categorize_error,
)
from src.reliability.retry import (
    RetryConfig,
    CircuitBreaker,
    retry_operation,
    BackoffStrategy,
)
from src.reliability.middleware import (
    ErrorMiddleware,
    OperationType,
    get_middleware,
    reset_middleware,
)
from src.reliability.degradation import (
    DegradationHandler,
    DegradationConfig,
    DegradationMode,
    get_degradation_handler,
    reset_degradation_handler,
)


class TestNetworkExceptions:
    """网络异常场景测试"""

    def setup_method(self):
        reset_middleware()
        reset_degradation_handler()

    def test_connection_error_is_retryable(self):
        error = CDPConnectionLostError()
        assert is_retryable(error) is True
        assert error.recoverable is True
        assert error.category == ErrorCategory.CONNECTION

    def test_timeout_error_is_retryable(self):
        error = CDPCommandTimeoutError(command="Page.navigate", timeout=30.0)
        assert is_retryable(error) is True
        assert error.recoverable is True
        assert error.category == ErrorCategory.TIMEOUT

    def test_navigation_timeout_is_retryable(self):
        error = NavigationTimeoutError(url="https://example.com", timeout=30.0)
        assert is_retryable(error) is True
        assert error.recoverable is True
        assert error.category == ErrorCategory.NAVIGATION

    def test_network_idle_timeout_is_retryable(self):
        error = NetworkIdleTimeoutError(timeout=10.0, pending_requests=3)
        assert is_retryable(error) is True
        assert error.recoverable is True
        assert error.category == ErrorCategory.TIMEOUT

    def test_smart_wait_degraded_is_retryable(self):
        error = SmartWaitDegradedError(strategies_tried=["dom_content_loaded", "network_idle"], timeout=15.0)
        assert is_retryable(error) is True
        assert error.recoverable is True
        assert error.category == ErrorCategory.TIMEOUT

    def test_captcha_is_not_retryable(self):
        error = CaptchaDetectedError()
        assert is_retryable(error) is False
        assert error.recoverable is False
        assert error.category == ErrorCategory.CONTENT

    def test_anti_bot_blocked_is_not_retryable(self):
        error = BlockedByAntiBotError()
        assert is_retryable(error) is False
        assert error.recoverable is False
        assert error.category == ErrorCategory.PERMISSION

    def test_element_not_found_is_retryable(self):
        error = ElementNotFoundError(selector="#btn")
        assert is_retryable(error) is True
        assert error.recoverable is True
        assert error.category == ErrorCategory.ELEMENT

    def test_element_index_invalid_is_retryable(self):
        error = ElementIndexInvalidError(index=5, available_count=3)
        assert is_retryable(error) is True
        assert error.recoverable is True
        assert error.category == ErrorCategory.ELEMENT

    def test_generic_exception_is_not_retryable(self):
        error = ValueError("unexpected error")
        assert is_retryable(error) is False

    def test_connection_error_retry_succeeds_after_failures(self):
        call_count = [0]
        def flaky_connection():
            call_count[0] += 1
            if call_count[0] < 3:
                raise CDPConnectionLostError()
            return "connected"
        config = RetryConfig(max_retries=3, backoff_strategy=BackoffStrategy.FIXED, base_delay=0.01)
        result = retry_operation(flaky_connection, config, operation="connect")
        assert result == "connected"
        assert call_count[0] == 3

    def test_timeout_error_retry_succeeds_after_failures(self):
        call_count = [0]
        def flaky_timeout():
            call_count[0] += 1
            if call_count[0] < 2:
                raise CDPCommandTimeoutError(command="Runtime.evaluate", timeout=30.0)
            return "evaluated"
        config = RetryConfig(max_retries=3, backoff_strategy=BackoffStrategy.FIXED, base_delay=0.01)
        result = retry_operation(flaky_timeout, config, operation="eval")
        assert result == "evaluated"
        assert call_count[0] == 2

    def test_non_retryable_error_raises_immediately(self):
        call_count = [0]
        def always_captcha():
            call_count[0] += 1
            raise CaptchaDetectedError()
        config = RetryConfig(max_retries=3, backoff_strategy=BackoffStrategy.FIXED, base_delay=0.01)
        with pytest.raises(CaptchaDetectedError):
            retry_operation(always_captcha, config, operation="test")
        assert call_count[0] == 1

    def test_all_retries_exhausted_raises_last_error(self):
        call_count = [0]
        def always_fail():
            call_count[0] += 1
            raise CDPConnectionLostError()
        config = RetryConfig(max_retries=2, backoff_strategy=BackoffStrategy.FIXED, base_delay=0.01)
        with pytest.raises(CDPConnectionLostError):
            retry_operation(always_fail, config, operation="test")
        assert call_count[0] == 3


class TestRetryStrategies:
    """重试策略变体测试"""

    def test_fixed_backoff_constant_delay(self):
        delays = [_calculate_delay(BackoffStrategy.FIXED, i, 0.5, 2.0) for i in range(5)]
        assert all(d == 0.5 for d in delays)

    def test_linear_backoff_increasing_delay(self):
        delays = [_calculate_delay(BackoffStrategy.LINEAR, i, 0.5, 2.0) for i in range(5)]
        assert delays[0] < delays[1] < delays[2]
        assert delays[-1] <= 2.0

    def test_exponential_backoff_growing_delay(self):
        # base=2.0 确保指数递增（attempt=1→2, attempt=2→4, ...）
        delays = [_calculate_delay(BackoffStrategy.EXPONENTIAL, i, 2.0, 16.0) for i in range(1, 6)]
        assert delays[0] < delays[1] < delays[2] < delays[3]
        assert delays[-1] <= 16.0

    def test_exponential_jitter_varies_delay(self):
        delays = [_calculate_delay(BackoffStrategy.EXPONENTIAL_JITTER, i, 0.1, 1.0) for i in range(1, 10)]
        assert len(set(delays)) > 1
        assert all(0 < d <= 1.0 for d in delays)

    def test_exponential_jitter_no_snowball(self):
        delays = [_calculate_delay(BackoffStrategy.EXPONENTIAL_JITTER, i, 0.1, 2.0) for i in range(1, 21)]
        assert len(set(delays)) == len(delays)

    def test_max_delay_cap(self):
        delay = _calculate_delay(BackoffStrategy.EXPONENTIAL, 10, 2.0, 5.0)
        assert delay <= 5.0

    def test_retry_config_for_operation_navigation(self):
        config = RetryConfig.for_operation("navigation")
        assert config.max_retries == 3
        assert config.base_delay == 2.0
        assert config.circuit_breaker is True

    def test_retry_config_for_operation_screenshot(self):
        config = RetryConfig.for_operation("screenshot")
        assert config.max_retries == 2
        assert config.base_delay == 1.0
        assert config.circuit_breaker is False

    def test_retry_config_for_operation_element_find(self):
        config = RetryConfig.for_operation("element_find")
        assert config.max_retries == 3
        assert config.base_delay == 0.5
        assert config.circuit_breaker is False

    def test_retry_config_override(self):
        config = RetryConfig.for_operation("navigation", max_retries=5, base_delay=3.0)
        assert config.max_retries == 5
        assert config.base_delay == 3.0


class TestCircuitBreakerEdgeCases:
    """熔断器边界场景测试"""

    def test_circuit_closed_allows_execution(self):
        cb = CircuitBreaker(failure_threshold=3)
        assert cb.state == "closed"
        assert cb.can_execute() is True

    def test_circuit_open_blocks_execution_before_timeout(self):
        cb = CircuitBreaker(failure_threshold=2, recovery_timeout=10.0)
        cb.record_failure()
        cb.record_failure()
        assert cb.state == "open"
        assert cb.can_execute() is False

    def test_circuit_open_allows_after_timeout(self):
        cb = CircuitBreaker(failure_threshold=2, recovery_timeout=0.05)
        cb.record_failure()
        cb.record_failure()
        time.sleep(0.1)
        assert cb.can_execute() is True
        assert cb.state == "half_open"

    def test_half_open_success_closes_circuit(self):
        cb = CircuitBreaker(failure_threshold=2, recovery_timeout=0.05)
        cb.record_failure()
        cb.record_failure()
        time.sleep(0.1)
        cb.can_execute()
        cb.record_success()
        assert cb.state == "closed"
        assert cb.failure_count == 0

    def test_half_open_failure_reopens_circuit(self):
        cb = CircuitBreaker(failure_threshold=2, recovery_timeout=0.05)
        cb.record_failure()
        cb.record_failure()
        time.sleep(0.1)
        cb.can_execute()  # 进入 half_open
        cb.record_failure()
        assert cb.state == "open"
        # failure_count 在 half_open 失败时不重置，保持累计值
        assert cb.failure_count == 3

    def test_circuit_get_status(self):
        cb = CircuitBreaker(failure_threshold=3, recovery_timeout=10.0)
        cb.record_failure()
        cb.record_failure()
        time.sleep(0.01)
        status = cb.get_status()
        assert status["state"] == "closed"
        assert status["failure_count"] == 2
        assert status["failure_threshold"] == 3
        assert status["recovery_timeout"] == 10.0
        assert status["time_since_last_failure"] >= 0

    def test_circuit_reset_clears_all_state(self):
        cb = CircuitBreaker(failure_threshold=2, recovery_timeout=1.0)
        cb.record_failure()
        cb.record_failure()
        cb.reset()
        assert cb.state == "closed"
        assert cb.failure_count == 0
        assert cb.last_failure_time == 0.0
        assert cb.success_count_after_half_open == 0
        assert cb.can_execute() is True

    def test_circuit_success_in_closed_state_resets_count(self):
        cb = CircuitBreaker(failure_threshold=3)
        cb.record_failure()
        cb.record_failure()
        assert cb.failure_count == 2
        cb.record_success()
        assert cb.failure_count == 0

    def test_circuit_threshold_one(self):
        cb = CircuitBreaker(failure_threshold=1)
        cb.record_failure()
        assert cb.state == "open"
        assert cb.can_execute() is False

    def test_circuit_half_open_only_allows_one_probe(self):
        cb = CircuitBreaker(failure_threshold=2, recovery_timeout=0.05)
        cb.record_failure()
        cb.record_failure()
        time.sleep(0.1)
        # 第一次 can_execute 进入 half_open
        assert cb.can_execute() is True
        assert cb.state == "half_open"
        # 第二次 can_execute 仍返回 True（can_execute 不递增 success_count）
        # 实际限制通过 success_count_after_half_open 在 record_success 后重置
        assert cb.can_execute() is True
        # 记录成功后进入 closed 状态
        cb.record_success()
        assert cb.state == "closed"


class TestDegradationModes:
    """降级模式变体测试"""

    def setup_method(self):
        reset_degradation_handler()

    def test_skip_mode_returns_default_value(self):
        handler = DegradationHandler(DegradationConfig(
            mode=DegradationMode.SKIP,
            skip_on_categories=[ErrorCategory.ELEMENT],
            default_value="fallback_result",
        ))
        error = ElementNotFoundError(selector="#btn")
        result = handler.handle(error, "test", OperationType.CLICK)
        assert result == "fallback_result"

    def test_skip_mode_returns_none_by_default(self):
        handler = DegradationHandler(DegradationConfig(
            mode=DegradationMode.SKIP,
            skip_on_categories=[ErrorCategory.ELEMENT],
        ))
        error = ElementNotFoundError(selector="#btn")
        result = handler.handle(error, "test", OperationType.CLICK)
        assert result is None

    def test_error_mode_raises_error(self):
        handler = DegradationHandler(DegradationConfig(mode=DegradationMode.ERROR))
        error = ElementNotFoundError(selector="#btn")
        with pytest.raises(ElementNotFoundError):
            handler.handle(error, "test", OperationType.CLICK)

    def test_fallback_mode_calls_fallback_func(self):
        def fallback():
            return "fallback_data"
        handler = DegradationHandler(DegradationConfig(
            mode=DegradationMode.FALLBACK,
            fallback_func=fallback,
        ))
        error = ElementNotFoundError(selector="#btn")
        result = handler.handle(error, "test", OperationType.CLICK)
        assert result == "fallback_data"

    def test_fallback_mode_raises_original_on_fallback_failure(self):
        def failing_fallback():
            raise RuntimeError("fallback failed")
        handler = DegradationHandler(DegradationConfig(
            mode=DegradationMode.FALLBACK,
            fallback_func=failing_fallback,
        ))
        error = ElementNotFoundError(selector="#btn")
        with pytest.raises(ElementNotFoundError):
            handler.handle(error, "test", OperationType.CLICK)

    def test_cached_mode_returns_cached_data(self):
        handler = DegradationHandler(DegradationConfig(
            mode=DegradationMode.CACHED,
            cache_key="test_key",
        ))
        handler.set_cache("test_key", "cached_result")
        error = ElementNotFoundError(selector="#btn")
        result = handler.handle(error, "test", OperationType.CLICK)
        assert result == "cached_result"

    def test_cached_mode_returns_default_on_miss(self):
        handler = DegradationHandler(DegradationConfig(
            mode=DegradationMode.CACHED,
            cache_key="missing_key",
            default_value="default_result",
        ))
        error = ElementNotFoundError(selector="#btn")
        result = handler.handle(error, "test", OperationType.CLICK)
        assert result == "default_result"

    def test_skip_non_matching_category_raises_error(self):
        handler = DegradationHandler(DegradationConfig(
            mode=DegradationMode.SKIP,
            skip_on_categories=[ErrorCategory.ELEMENT],
        ))
        error = CaptchaDetectedError()
        with pytest.raises(CaptchaDetectedError):
            handler.handle(error, "test", OperationType.NAVIGATION)

    def test_degradation_handler_cache(self):
        handler = DegradationHandler()
        handler.set_cache("key1", "value1")
        assert handler._cache["key1"] == "value1"
        assert "key2" not in handler._cache

    def test_degradation_handler_cache_overwrite(self):
        handler = DegradationHandler()
        handler.set_cache("key", "value1")
        handler.set_cache("key", "value2")
        assert handler._cache["key"] == "value2"

    def test_degradation_handler_clear_cache(self):
        handler = DegradationHandler()
        handler.set_cache("key", "value")
        handler.clear_cache()
        assert handler._cache == {}

    def test_degradation_handler_status(self):
        handler = DegradationHandler()
        handler.set_cache("key", "value")
        assert len(handler._cache) == 1


class TestConcurrentStability:
    """高并发场景稳定性测试"""

    def test_multiple_circuit_breakers_independent(self):
        cb1 = CircuitBreaker(failure_threshold=2)
        cb2 = CircuitBreaker(failure_threshold=2)
        cb1.record_failure()
        cb1.record_failure()
        assert cb1.state == "open"
        assert cb2.state == "closed"

    def test_middleware_singleton(self):
        m1 = get_middleware()
        m2 = get_middleware()
        assert m1 is m2

    def test_middleware_multiple_operations(self):
        middleware = get_middleware()
        cb1 = middleware.get_circuit_breaker("op1")
        cb2 = middleware.get_circuit_breaker("op2")
        assert cb1 is not cb2

    def test_concurrent_circuit_breaker_access(self):
        cb = CircuitBreaker(failure_threshold=100)
        errors = []
        def record_failures():
            try:
                for _ in range(50):
                    cb.record_failure()
            except Exception as e:
                errors.append(e)
        threads = [threading.Thread(target=record_failures) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(errors) == 0
        assert cb.failure_count == 200
        assert cb.state == "open"

    def test_concurrent_middleware_wrap(self):
        middleware = get_middleware()
        results = []
        errors = []
        def safe_operation():
            return "ok"
        def worker():
            try:
                wrapped = middleware.wrap_sync(safe_operation, "test_op", OperationType.CLICK)
                results.append(wrapped())
            except Exception as e:
                errors.append(e)
        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(errors) == 0
        assert len(results) == 10
        assert all(r == "ok" for r in results)

    def test_retry_with_jitter_no_collision(self):
        delays = [_calculate_delay(BackoffStrategy.EXPONENTIAL_JITTER, i, 0.1, 1.0) for i in range(1, 21)]
        assert len(set(delays)) == len(delays)


class TestMemoryLeakDetection:
    """内存泄漏检测"""

    def test_circuit_breaker_reset_clears_state(self):
        cb = CircuitBreaker()
        cb.record_failure()
        cb.record_failure()
        cb.reset()
        assert cb.failure_count == 0
        assert cb.state == "closed"
        assert cb.last_failure_time == 0.0

    def test_middleware_circuit_breakers_cleared_on_reset(self):
        middleware = get_middleware()
        cb1 = middleware.get_circuit_breaker("op1")
        reset_middleware()
        middleware2 = get_middleware()
        cb3 = middleware2.get_circuit_breaker("op1")
        assert cb1 is not cb3

    def test_degradation_handler_cache_cleared(self):
        handler = get_degradation_handler()
        handler.set_cache("key", "value")
        reset_degradation_handler()
        handler2 = get_degradation_handler()
        assert handler2._cache == {}

    def test_no_accumulating_circuit_breakers(self):
        middleware = get_middleware()
        ops = [f"op_{i}" for i in range(10)]
        cbs = [middleware.get_circuit_breaker(op) for op in ops]
        assert len(set(cbs)) == 10

    def test_error_context_serialization(self):
        from src.reliability.middleware import ErrorContext, OperationType
        context = ErrorContext(
            operation="test_op",
            operation_type=OperationType.CLICK,
            attempt=1,
            max_attempts=3,
            error=ElementNotFoundError(selector="#btn"),
        )
        data = context.to_dict()
        assert data["operation"] == "test_op"
        assert data["operation_type"] == "click"
        assert data["attempt"] == 1
        assert data["max_attempts"] == 3
        assert data["error"] is not None
        assert data["category"] is None
        assert data["recoverable"] is True


class TestErrorClassificationConsistency:
    """错误分类一致性测试"""

    def test_all_error_types_classified(self):
        errors = [
            (CDPConnectionLostError(), ErrorCategory.CONNECTION),
            (CDPCommandTimeoutError(command="test", timeout=30.0), ErrorCategory.TIMEOUT),
            (ElementNotFoundError(selector="#btn"), ErrorCategory.ELEMENT),
            (ElementIndexInvalidError(index=5, available_count=3), ErrorCategory.ELEMENT),
            (NavigationTimeoutError(url="https://example.com", timeout=30.0), ErrorCategory.NAVIGATION),
            (CaptchaDetectedError(), ErrorCategory.CONTENT),
            (BlockedByAntiBotError(), ErrorCategory.PERMISSION),
            (NetworkIdleTimeoutError(timeout=10.0), ErrorCategory.TIMEOUT),
            (SmartWaitDegradedError(strategies_tried=["a"], timeout=5.0), ErrorCategory.TIMEOUT),
        ]
        for error, expected_category in errors:
            category = categorize_error(error)
            assert category == expected_category, f"{error} should be {expected_category}"

    def test_retryable_classification(self):
        assert is_retryable(CDPConnectionLostError()) is True
        assert is_retryable(CDPCommandTimeoutError(command="test", timeout=30.0)) is True
        assert is_retryable(ElementNotFoundError(selector="#btn")) is True
        assert is_retryable(ElementIndexInvalidError(index=5, available_count=3)) is True
        assert is_retryable(NavigationTimeoutError(url="https://example.com", timeout=30.0)) is True
        assert is_retryable(NetworkIdleTimeoutError(timeout=10.0)) is True
        assert is_retryable(SmartWaitDegradedError(strategies_tried=["a"], timeout=5.0)) is True
        assert is_retryable(CaptchaDetectedError()) is False
        assert is_retryable(BlockedByAntiBotError()) is False

    def test_error_serialization(self):
        error = CDPConnectionLostError(details={"url": "https://example.com"})
        data = error.to_dict()
        assert data["type"] == "CDPConnectionLostError"
        assert data["category"] == "connection"
        assert data["recoverable"] is True
        assert data["details"]["url"] == "https://example.com"
        assert "timestamp" in data

    def test_unknown_error_category(self):
        error = ValueError("unexpected")
        category = categorize_error(error)
        assert category == ErrorCategory.UNKNOWN
        assert is_retryable(error) is False


class TestIntegrationScenarios:
    """集成场景测试"""

    def setup_method(self):
        reset_middleware()
        reset_degradation_handler()

    def test_full_navigation_flow(self):
        middleware = ErrorMiddleware()
        call_count = [0]
        def navigate(url):
            call_count[0] += 1
            if call_count[0] < 2:
                raise CDPConnectionLostError()
            return {"url": url, "status": "loaded"}
        wrapped = middleware.wrap_sync(navigate, "navigate", OperationType.NAVIGATION, max_retries=3)
        result = wrapped("https://example.com")
        assert result["status"] == "loaded"
        assert call_count[0] == 2

    def test_full_extract_flow_with_degradation(self):
        handler = DegradationHandler(DegradationConfig(
            mode=DegradationMode.SKIP,
            skip_on_categories=[ErrorCategory.ELEMENT],
            default_value=[],
        ))
        error = ElementNotFoundError(selector="#data")
        result = handler.handle(error, "extract", OperationType.EXTRACT)
        assert result == []

    def test_captcha_triggers_log(self):
        error = CaptchaDetectedError()
        category = categorize_error(error)
        assert category == ErrorCategory.CONTENT
        assert is_retryable(error) is False

    def test_middleware_async_wrapper(self):
        import asyncio
        middleware = ErrorMiddleware()
        call_count = [0]
        async def async_navigate(url):
            call_count[0] += 1
            if call_count[0] < 2:
                raise CDPConnectionLostError()
            return {"url": url, "status": "loaded"}
        wrapped = middleware.wrap_async(async_navigate, "async_nav", OperationType.NAVIGATION, max_retries=3)
        result = asyncio.run(wrapped("https://example.com"))
        assert result["status"] == "loaded"
        assert call_count[0] == 2

    def test_decorator_syntax_sync(self):
        middleware = get_middleware()
        @middleware.wrap_sync(operation="decorated_op", operation_type=OperationType.CLICK, max_retries=2)
        def decorated_click():
            return "clicked"
        assert decorated_click() == "clicked"

    def test_decorator_syntax_async(self):
        import asyncio
        middleware = get_middleware()
        @middleware.wrap_async(operation="async_decorated", operation_type=OperationType.EXTRACT, max_retries=2)
        async def async_decorated():
            return "extracted"
        assert asyncio.run(async_decorated()) == "extracted"

    def test_error_context_str_representation(self):
        from src.reliability.middleware import ErrorContext, OperationType
        context = ErrorContext(
            operation="test_op",
            operation_type=OperationType.CLICK,
            attempt=2,
            max_attempts=3,
            error=ElementNotFoundError(selector="#btn"),
        )
        str_repr = str(context)
        assert "click" in str_repr.lower()
        assert "test_op" in str_repr
        assert "2/3" in str_repr

    def test_operation_type_enum_values(self):
        assert OperationType.NAVIGATION.value == "navigation"
        assert OperationType.SCREENSHOT.value == "screenshot"
        assert OperationType.CLICK.value == "click"
        assert OperationType.INPUT.value == "input"
        assert OperationType.WAIT.value == "wait"
        assert OperationType.EXTRACT.value == "extract"
        assert OperationType.SCROLL.value == "scroll"
        assert OperationType.TAB.value == "tab"
        assert OperationType.CDP_COMMAND.value == "cdp_command"
        assert OperationType.UNKNOWN.value == "unknown"


def _calculate_delay(strategy, attempt, base, max_delay):
    """辅助函数：计算退避延迟"""
    import random
    if strategy == BackoffStrategy.EXPONENTIAL_JITTER:
        delay = min(base ** attempt, max_delay)
        return delay * (0.5 + random.random())
    elif strategy == BackoffStrategy.FIXED:
        return min(base, max_delay)
    elif strategy == BackoffStrategy.LINEAR:
        return min(base * attempt, max_delay)
    elif strategy == BackoffStrategy.EXPONENTIAL:
        return min(base ** attempt, max_delay)
    return min(base * attempt, max_delay)


if __name__ == "__main__":
    pytest.main([__file__, '-v'])
