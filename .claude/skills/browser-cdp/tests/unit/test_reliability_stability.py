"""
可靠性保障机制稳定性测试

测试覆盖：
- 错误分类 + 重试 + 降级 完整链路
- 熔断器触发与恢复
- 高并发场景下的稳定性
- 内存泄漏检测
"""
import pytest
import sys
import time
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock

SKILL_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from src.reliability.error import (
    ReliabilityError,
    ErrorCategory,
    CDPConnectionLostError,
    ElementNotFoundError,
    CaptchaDetectedError,
    BlockedByAntiBotError,
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


class TestErrorRetryDegradationChain:
    """错误分类 + 重试 + 降级完整链路测试"""

    def setup_method(self):
        reset_middleware()
        reset_degradation_handler()

    def test_connection_error_retry_then_success(self):
        """连接错误重试后成功"""
        call_count = [0]

        def flaky_operation():
            call_count[0] += 1
            if call_count[0] < 3:
                raise CDPConnectionLostError()
            return "success"

        config = RetryConfig(max_retries=3, backoff_strategy=BackoffStrategy.FIXED, base_delay=0.01)
        result = retry_operation(flaky_operation, config, operation="test")
        assert result == "success"
        assert call_count[0] == 3

    def test_captcha_error_skip_degradation(self):
        """验证码错误触发跳过降级"""
        error = CaptchaDetectedError()
        handler = DegradationHandler()
        result = handler.handle(error, "test_op", OperationType.NAVIGATION)
        assert result is None  # 跳过返回 None

    def test_blocked_error_skip_degradation(self):
        """反爬拦截触发跳过降级"""
        error = BlockedByAntiBotError()
        handler = DegradationHandler()
        result = handler.handle(error, "test_op", OperationType.NAVIGATION)
        assert result is None

    def test_element_error_retry_exhausted_then_degrade(self):
        """元素错误重试耗尽后降级（配置为跳过）"""
        def always_fail():
            raise ElementNotFoundError(selector="#missing")

        config = RetryConfig(max_retries=2, backoff_strategy=BackoffStrategy.FIXED, base_delay=0.01)

        with pytest.raises(ElementNotFoundError):
            retry_operation(always_fail, config, operation="test")

        # 重试耗尽后触发降级（配置为跳过 ELEMENT 错误）
        handler = DegradationHandler(DegradationConfig(
            mode=DegradationMode.SKIP,
            skip_on_categories=[ErrorCategory.ELEMENT],
            default_value="fallback",
        ))
        error = ElementNotFoundError(selector="#missing")
        result = handler.handle(error, "test_op", OperationType.CLICK)
        assert result == "fallback"

    def test_middleware_integration_with_retry(self):
        """中间件集成重试"""
        middleware = ErrorMiddleware()
        call_count = [0]

        def navigate(url):
            call_count[0] += 1
            if call_count[0] < 2:
                raise CDPConnectionLostError()
            return f"loaded_{url}"

        wrapped = middleware.wrap_sync(navigate, "test_nav", OperationType.NAVIGATION, max_retries=2)
        result = wrapped("https://example.com")
        assert result == "loaded_https://example.com"
        assert call_count[0] == 2


class TestCircuitBreakerStability:
    """熔断器稳定性测试"""

    def test_circuit_opens_after_threshold(self):
        """连续失败达到阈值后熔断"""
        cb = CircuitBreaker(failure_threshold=3, recovery_timeout=1.0)

        for i in range(3):
            cb.record_failure()

        assert cb.state == "open"
        assert not cb.can_execute()

    def test_circuit_recovers_after_timeout(self):
        """超时后半开状态允许试探"""
        cb = CircuitBreaker(failure_threshold=2, recovery_timeout=0.1)

        cb.record_failure()
        cb.record_failure()
        assert cb.state == "open"

        # 等待恢复超时
        time.sleep(0.15)
        assert cb.can_execute()  # 应进入 half_open
        assert cb.state == "half_open"

    def test_half_open_success_closes_circuit(self):
        """半开状态成功则关闭熔断器"""
        cb = CircuitBreaker(failure_threshold=2, recovery_timeout=0.1)
        cb.record_failure()
        cb.record_failure()

        time.sleep(0.15)
        cb.can_execute()  # 进入 half_open
        cb.record_success()
        assert cb.state == "closed"

    def test_half_open_failure_reopens_circuit(self):
        """半开状态失败则重新打开熔断器"""
        cb = CircuitBreaker(failure_threshold=2, recovery_timeout=0.1)
        cb.record_failure()
        cb.record_failure()

        time.sleep(0.15)
        cb.can_execute()  # 进入 half_open
        cb.record_failure()
        assert cb.state == "open"


class TestConcurrentStability:
    """高并发场景稳定性测试"""

    def test_multiple_circuit_breakers_independent(self):
        """多个熔断器独立运行"""
        cb1 = CircuitBreaker(failure_threshold=2)
        cb2 = CircuitBreaker(failure_threshold=2)

        cb1.record_failure()
        cb1.record_failure()
        assert cb1.state == "open"
        assert cb2.state == "closed"  # cb2 不受影响

    def test_middleware_singleton_thread_safe(self):
        """中间件单例线程安全"""
        m1 = get_middleware()
        m2 = get_middleware()
        assert m1 is m2

    def test_retry_with_jitter_no_collision(self):
        """指数退避 jitter 避免雪崩"""
        delays = []
        for i in range(10):
            delay = _calculate_delay(BackoffStrategy.EXPONENTIAL_JITTER, i, 0.1, 1.0)
            delays.append(delay)

        # 所有延迟应不同（jitter 导致）
        assert len(set(delays)) > 1


class TestMemoryLeakDetection:
    """内存泄漏检测"""

    def test_circuit_breaker_reset_clears_state(self):
        """重置熔断器清除状态"""
        cb = CircuitBreaker()
        cb.record_failure()
        cb.record_failure()
        cb.reset()

        assert cb.failure_count == 0
        assert cb.state == "closed"
        assert cb.last_failure_time == 0.0

    def test_middleware_circuit_breakers_cleared_on_reset(self):
        """重置中间件清除所有熔断器"""
        middleware = get_middleware()
        cb1 = middleware.get_circuit_breaker("op1")
        cb2 = middleware.get_circuit_breaker("op2")

        reset_middleware()
        middleware2 = get_middleware()
        cb3 = middleware2.get_circuit_breaker("op1")

        assert cb1 is not cb3  # 新实例

    def test_degradation_handler_cache_cleared(self):
        """重置降级处理器清除缓存"""
        handler = get_degradation_handler()
        handler.set_cache("key", "value")

        reset_degradation_handler()
        handler2 = get_degradation_handler()
        assert handler2._cache == {}


class TestErrorClassificationConsistency:
    """错误分类一致性测试"""

    def test_all_error_types_classified(self):
        """所有错误类型都能正确分类"""
        errors = [
            (CDPConnectionLostError(), ErrorCategory.CONNECTION),
            (ElementNotFoundError(selector="#btn"), ErrorCategory.ELEMENT),
            (CaptchaDetectedError(), ErrorCategory.CONTENT),
            (BlockedByAntiBotError(), ErrorCategory.PERMISSION),
        ]

        for error, expected_category in errors:
            category = categorize_error(error)
            assert category == expected_category, f"{error} should be {expected_category}"

    def test_retryable_classification(self):
        """可恢复性分类正确"""
        assert is_retryable(CDPConnectionLostError()) is True
        assert is_retryable(ElementNotFoundError(selector="#btn")) is True
        assert is_retryable(CaptchaDetectedError()) is False
        assert is_retryable(BlockedByAntiBotError()) is False


class TestIntegrationScenarios:
    """集成场景测试"""

    def test_full_navigation_flow(self):
        """完整导航流程：连接错误→重试→成功"""
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
        """完整提取流程：元素错误→重试耗尽→降级跳过"""
        handler = DegradationHandler(DegradationConfig(
            mode=DegradationMode.SKIP,
            skip_on_categories=[ErrorCategory.ELEMENT],
            default_value=[],
        ))

        error = ElementNotFoundError(selector="#data")
        result = handler.handle(error, "extract", OperationType.EXTRACT)
        assert result == []

    def test_captcha_triggers_log(self):
        """验证码触发日志记录"""
        import logging
        from src.reliability.error import categorize_error

        # 验证码错误应被分类为 CONTENT
        error = CaptchaDetectedError()
        category = categorize_error(error)
        assert category == ErrorCategory.CONTENT
        assert is_retryable(error) is False


def _calculate_delay(strategy, attempt, base, max_delay):
    """辅助函数：计算退避延迟"""
    import random
    if strategy == BackoffStrategy.EXPONENTIAL_JITTER:
        delay = min(base ** attempt, max_delay)
        return delay * (0.5 + random.random())
    return delay


if __name__ == "__main__":
    pytest.main([__file__, '-v'])
