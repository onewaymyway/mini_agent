"""
test_request_policy.py - 请求策略单元测试

测试 RequestPolicy 的超时控制、重试机制、熔断器等功能。
"""
import asyncio
import pytest
import time
from unittest.mock import AsyncMock, MagicMock, patch

from src.core.request_policy import (
    RequestPolicy,
    OperationConfig,
    OperationType,
    CircuitBreaker,
    RequestStats,
    get_request_policy,
    reset_request_policy,
)


class TestCircuitBreaker:
    """测试熔断器"""

    def test_initial_state(self):
        cb = CircuitBreaker()
        assert cb.state == "closed"
        assert cb.can_execute() is True

    def test_open_after_threshold(self):
        cb = CircuitBreaker(threshold=3, timeout=1.0)
        cb.record_failure()
        cb.record_failure()
        cb.record_failure()
        assert cb.state == "open"
        assert cb.can_execute() is False

    def test_half_open_after_timeout(self):
        cb = CircuitBreaker(threshold=2, timeout=0.1)
        cb.record_failure()
        cb.record_failure()
        assert cb.state == "open"
        time.sleep(0.15)
        assert cb.can_execute() is True
        assert cb.state == "half_open"

    def test_close_after_success_in_half_open(self):
        cb = CircuitBreaker(threshold=2, timeout=0.1)
        cb.record_failure()
        cb.record_failure()
        time.sleep(0.15)
        cb.can_execute()  # transition to half_open
        cb.record_success()
        assert cb.state == "closed"

    def test_reset(self):
        cb = CircuitBreaker(threshold=2)
        cb.record_failure()
        cb.record_failure()
        cb.reset()
        assert cb.state == "closed"
        assert cb.failure_count == 0


class TestRequestStats:
    """测试请求统计"""

    def test_initial_state(self):
        stats = RequestStats()
        assert stats.total_requests == 0
        assert stats.success_rate == 0.0

    def test_record_success(self):
        stats = RequestStats()
        stats.record_success("search")
        assert stats.success_requests == 1
        assert stats.total_requests == 1
        assert stats.success_rate == 1.0

    def test_record_failure(self):
        stats = RequestStats()
        stats.record_failure("search", "timeout")
        assert stats.failed_requests == 1
        assert stats.timeout_requests == 1
        assert stats.success_rate == 0.0

    def test_record_retry(self):
        stats = RequestStats()
        stats.record_retry("search")
        assert stats.retry_requests == 1

    def test_get_summary(self):
        stats = RequestStats()
        stats.record_success("search")
        stats.record_success("search")
        stats.record_failure("search", "timeout")
        stats.record_retry("search")

        summary = stats.get_summary()
        assert summary["total_requests"] == 3
        assert summary["success_requests"] == 2
        assert summary["failed_requests"] == 1
        assert summary["retry_requests"] == 1
        assert summary["success_rate"] == 66.67


class TestRequestPolicy:
    """测试请求策略"""

    @pytest.fixture(autouse=True)
    def setup(self):
        reset_request_policy()
        yield
        reset_request_policy()

    def test_get_config(self):
        policy = RequestPolicy()
        config = policy.get_config(OperationType.SEARCH)
        assert config.timeout == 20.0
        assert config.max_retries == 3

    def test_get_config_by_name(self):
        policy = RequestPolicy()
        config = policy.get_config_by_name("search")
        assert config.timeout == 20.0

    def test_get_config_unknown_operation(self):
        policy = RequestPolicy()
        config = policy.get_config_by_name("unknown_operation")
        assert config.timeout == 30.0  # default

    @pytest.mark.asyncio
    async def test_execute_success(self):
        policy = RequestPolicy()
        mock_func = AsyncMock(return_value={"result": "ok"})

        result = await policy.execute_with_policy(
            "search", mock_func, "test_query"
        )

        assert result == {"result": "ok"}
        mock_func.assert_called_once_with("test_query")

    @pytest.mark.asyncio
    async def test_execute_with_retry(self):
        policy = RequestPolicy()
        call_count = 0

        async def flaky_func():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ConnectionError("connection lost")
            return {"result": "ok"}

        result = await policy.execute_with_policy(
            "navigation", flaky_func, timeout=5.0
        )

        assert result == {"result": "ok"}
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_execute_timeout(self):
        policy = RequestPolicy()

        async def slow_func():
            await asyncio.sleep(10)
            return {"result": "ok"}

        with pytest.raises(asyncio.TimeoutError):
            await policy.execute_with_policy(
                "search", slow_func, timeout=0.1
            )

    @pytest.mark.asyncio
    async def test_execute_circuit_breaker(self):
        policy = RequestPolicy()

        async def failing_func():
            raise ConnectionError("connection lost")

        # 触发熔断器
        for _ in range(5):
            try:
                await policy.execute_with_policy(
                    "navigation", failing_func, timeout=0.1
                )
            except (ConnectionError, TimeoutError):
                pass

        # 熔断器开启后应该拒绝执行
        with pytest.raises(TimeoutError):
            await policy.execute_with_policy(
                "navigation", failing_func, timeout=0.1
            )

    @pytest.mark.asyncio
    async def test_execute_selector_error_no_retry(self):
        policy = RequestPolicy()
        call_count = 0

        async def selector_error_func():
            nonlocal call_count
            call_count += 1
            raise Exception("selector not found: 未找到搜索框")

        with pytest.raises(Exception):
            await policy.execute_with_policy(
                "search", selector_error_func, timeout=1.0
            )

        # 选择器错误应该只重试一次
        assert call_count == 1

    def test_get_operation_stats(self):
        policy = RequestPolicy()
        stats = policy.get_operation_stats("search")
        assert stats == {}

    def test_reset_stats(self):
        policy = RequestPolicy()
        policy.stats.record_success("search")
        policy.reset_stats()
        assert policy.stats.total_requests == 0

    def test_reset_circuit_breakers(self):
        policy = RequestPolicy()
        cb = policy.get_circuit_breaker("search")
        cb.record_failure()
        cb.record_failure()
        policy.reset_circuit_breakers()
        assert cb.state == "closed"


class TestAdaptiveAdjustment:
    """测试自适应调整"""

    def test_adjust_on_low_success_rate(self):
        policy = RequestPolicy()
        policy.adjust_config_adaptive("search", 0.3)

        adjustments = policy._adaptive_adjustments.get("search", {})
        assert adjustments.get("timeout_multiplier", 1.0) > 1.0
        assert adjustments.get("retry_multiplier", 1.0) > 1.0

    def test_adjust_on_high_success_rate(self):
        policy = RequestPolicy()
        policy.adjust_config_adaptive("search", 0.95)

        adjustments = policy._adaptive_adjustments.get("search", {})
        # 成功率高时减少超时
        assert adjustments.get("timeout_multiplier", 1.0) <= 1.0


class TestGlobalFunctions:
    """测试全局函数"""

    def test_get_request_policy(self):
        reset_request_policy()
        policy1 = get_request_policy()
        policy2 = get_request_policy()
        assert policy1 is policy2

    def test_reset_request_policy(self):
        reset_request_policy()
        policy1 = get_request_policy()
        reset_request_policy()
        policy2 = get_request_policy()
        assert policy1 is not policy2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
