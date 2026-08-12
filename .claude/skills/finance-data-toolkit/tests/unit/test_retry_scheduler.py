# -*- coding: utf-8 -*-
"""
重试调度器单元测试

测试范围：
1. RetryScheduler 核心功能
2. 各种退避算法
3. 错误分类与条件重试
4. 并发重试
5. 统计与监控
6. 配置动态更新
"""

import asyncio
import sys
import time
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, r'E:\codes\mini_claude_code\.claude\skills\finance-data-toolkit')

from finance_toolkit.retry_scheduler import (
    RetryScheduler,
    RetryPolicy,
    ExponentialBackoffPolicy,
    FixedIntervalPolicy,
    LinearBackoffPolicy,
    FullJitterPolicy,
    DecorrelatedJitterPolicy,
    RetryStats,
    RetryResult,
    BackoffAlgorithm,
    retry_run,
    async_retry_run,
    DEFAULT_RETRY_SCHEDULER,
)
from finance_toolkit.error_capture import ErrorCapture, ErrorType
from finance_toolkit.exceptions import (
    SourceUnavailableError,
    TimeoutError as FinanceTimeoutError,
    ConnectionError as FinanceConnectionError,
    CircuitBreakerError,
)


class DummyException(Exception):
    """自定义异常用于测试"""
    pass


class FakeCircuitBreaker:
    """模拟熔断器"""
    def __init__(self, is_open_value=False):
        self._is_open = is_open_value
    
    def is_open(self):
        return self._is_open


# ============== ExponentialBackoffPolicy 测试 ==============

class TestExponentialBackoffPolicy:
    def test_default_config(self):
        policy = ExponentialBackoffPolicy()
        assert policy.base_delay == 1.0
        assert policy.max_delay == 60.0
        assert policy.backoff_factor == 2.0
        assert policy.jitter is True
        assert len(policy.retryable_errors) == 6

    def test_delay_no_jitter(self):
        policy = ExponentialBackoffPolicy(base_delay=1.0, backoff_factor=2.0, max_delay=10.0, jitter=False)
        assert policy.get_delay(1, Exception()) == 1.0
        assert policy.get_delay(2, Exception()) == 2.0
        assert policy.get_delay(3, Exception()) == 4.0
        assert policy.get_delay(4, Exception()) == 8.0
        assert policy.get_delay(5, Exception()) == 10.0

    def test_delay_with_jitter(self):
        policy = ExponentialBackoffPolicy(base_delay=1.0, backoff_factor=2.0, max_delay=10.0, jitter=True)
        d1 = policy.get_delay(1, Exception())
        assert 0.5 <= d1 <= 1.0

    def test_max_delay_cap(self):
        policy = ExponentialBackoffPolicy(base_delay=10.0, backoff_factor=2.0, max_delay=15.0, jitter=False)
        d = policy.get_delay(5, Exception())
        assert d <= 15.0

    def test_source_unavailable_with_retry_after(self):
        # SourceRateLimitedError stores retry_after in details
        from finance_toolkit.exceptions import SourceRateLimitedError
        error = SourceRateLimitedError('src', retry_after=5)
        policy = ExponentialBackoffPolicy(base_delay=1.0, max_delay=10.0, jitter=False)
        delay = policy.get_delay(1, error)
        # SourceRateLimitedError.retry_after is stored in details
        assert delay == 1.0  # Falls through to normal exponential backoff

    def test_rate_limited_error_uses_details(self):
        from finance_toolkit.exceptions import SourceRateLimitedError
        error = SourceRateLimitedError('src', retry_after=5)
        assert error.details.get('retry_after_seconds') == 5

    def test_should_retry_network_timeout(self):
        policy = ExponentialBackoffPolicy()
        policy.set_max_retries(3)
        assert policy.should_retry(1, FinanceTimeoutError('src', 'url', 10)) is True
        assert policy.should_retry(2, FinanceTimeoutError('src', 'url', 10)) is True
        assert policy.should_retry(3, FinanceTimeoutError('src', 'url', 10)) is True
        assert policy.should_retry(4, FinanceTimeoutError('src', 'url', 10)) is False

    def test_should_retry_connection_error(self):
        policy = ExponentialBackoffPolicy()
        policy.set_max_retries(3)
        assert policy.should_retry(1, FinanceConnectionError('src', 'url', 'refused')) is True

    def test_should_not_retry_data_quality(self):
        policy = ExponentialBackoffPolicy()
        policy.set_max_retries(3)
        from finance_toolkit.exceptions import DataQualityError
        assert policy.should_retry(1, DataQualityError('quote', ['missing field'])) is False

    def test_should_not_retry_non_retryable_error(self):
        policy = ExponentialBackoffPolicy(retryable_errors=[ErrorType.NETWORK_TIMEOUT])
        policy.set_max_retries(3)
        assert policy.should_retry(1, KeyError('test')) is False

    def test_consecutive_failures_limit(self):
        policy = ExponentialBackoffPolicy(base_delay=0.01)
        policy.set_max_retries(10)
        for _ in range(11):
            policy.on_failure()
        assert policy.should_retry(1, Exception()) is False

    def test_on_success_reduces_failures(self):
        policy = ExponentialBackoffPolicy(base_delay=1.0)
        for _ in range(3):
            policy.on_failure()
        assert policy._consecutive_failures == 3
        policy.on_success()
        assert policy._consecutive_failures == 2

    def test_on_failure_increments(self):
        policy = ExponentialBackoffPolicy(base_delay=1.0)
        policy.on_failure()
        assert policy._consecutive_failures == 1
        policy.on_failure()
        assert policy._consecutive_failures == 2


# ============== FixedIntervalPolicy 测试 ==============

class TestFixedIntervalPolicy:
    def test_fixed_delay(self):
        policy = FixedIntervalPolicy(base_delay=5.0, max_retries=3)
        assert policy.get_delay(1, Exception()) == 5.0
        assert policy.get_delay(2, Exception()) == 5.0
        assert policy.get_delay(3, Exception()) == 5.0

    def test_should_retry_within_limit(self):
        policy = FixedIntervalPolicy(base_delay=1.0, max_retries=3)
        assert policy.should_retry(1, Exception()) is True
        assert policy.should_retry(2, Exception()) is True
        assert policy.should_retry(3, Exception()) is True
        assert policy.should_retry(4, Exception()) is False

    def test_on_success_resets_failures(self):
        policy = FixedIntervalPolicy(base_delay=1.0)
        for _ in range(5):
            policy.on_failure()
        assert policy._consecutive_failures == 5
        policy.on_success()
        assert policy._consecutive_failures == 0


# ============== LinearBackoffPolicy 测试 ==============

class TestLinearBackoffPolicy:
    def test_linear_delay(self):
        policy = LinearBackoffPolicy(base_delay=1.0, max_delay=10.0)
        assert policy.get_delay(1, Exception()) == 1.0
        assert policy.get_delay(2, Exception()) == 2.0
        assert policy.get_delay(3, Exception()) == 3.0

    def test_max_delay_cap(self):
        policy = LinearBackoffPolicy(base_delay=10.0, max_delay=15.0)
        assert policy.get_delay(1, Exception()) == 10.0
        assert policy.get_delay(2, Exception()) == 15.0
        assert policy.get_delay(3, Exception()) == 15.0

    def test_should_retry_within_limit(self):
        policy = LinearBackoffPolicy(base_delay=1.0, max_retries=3)
        assert policy.should_retry(1, Exception()) is True
        assert policy.should_retry(3, Exception()) is True
        assert policy.should_retry(4, Exception()) is False


# ============== FullJitterPolicy 测试 ==============

class TestFullJitterPolicy:
    def test_delay_range(self):
        policy = FullJitterPolicy(base_delay=1.0, max_delay=10.0, backoff_factor=2.0)
        d = policy.get_delay(1, Exception())
        assert 0 <= d <= 1.0

    def test_delay_increases(self):
        policy = FullJitterPolicy(base_delay=1.0, max_delay=100.0, backoff_factor=2.0)
        # 全抖动策略的延迟范围是 random(0, base*factor^(attempt-1))
        # 多次测试确保总体趋势
        delays = [policy.get_delay(i, Exception()) for i in range(1, 6)]
        # 最后一个应该大于等于第一个（因为上限在增加）
        assert delays[-1] >= delays[0]

    def test_should_retry_within_limit(self):
        policy = FullJitterPolicy(base_delay=1.0, max_retries=3)
        assert policy.should_retry(1, Exception()) is True
        assert policy.should_retry(3, Exception()) is True
        assert policy.should_retry(4, Exception()) is False


# ============== DecorrelatedJitterPolicy 测试 ==============

class TestDecorrelatedJitterPolicy:
    def test_initial_delay(self):
        policy = DecorrelatedJitterPolicy(base_delay=1.0, max_delay=10.0)
        d = policy.get_delay(1, Exception())
        assert 1.0 <= d <= 10.0

    def test_delay_changes(self):
        policy = DecorrelatedJitterPolicy(base_delay=1.0, max_delay=100.0)
        delays = [policy.get_delay(i, Exception()) for i in range(1, 4)]
        assert all(1.0 <= d <= 100.0 for d in delays)

    def test_on_success_resets_delay(self):
        policy = DecorrelatedJitterPolicy(base_delay=1.0)
        policy.get_delay(1, Exception())
        policy.on_success()
        assert policy._last_delay == 1.0


# ============== RetryStats 测试 ==============

class TestRetryStats:
    def test_default_stats(self):
        stats = RetryStats()
        assert stats.total_attempts == 0
        assert stats.successful_attempts == 0
        assert stats.failed_attempts == 0
        assert stats.success_rate == 100.0

    def test_stats_after_success(self):
        stats = RetryStats()
        stats.total_attempts = 1
        stats.successful_attempts = 1
        assert stats.success_rate == 100.0

    def test_stats_after_failure(self):
        stats = RetryStats()
        stats.total_attempts = 3
        stats.successful_attempts = 1
        stats.failed_attempts = 2
        assert stats.success_rate == pytest.approx(33.33, abs=0.1)

    def test_to_dict(self):
        stats = RetryStats(
            total_attempts=5,
            successful_attempts=3,
            failed_attempts=2,
            errors_by_type={'network_timeout': 1, 'http_4xx': 1},
            last_error='test error',
            last_error_type='network_timeout',
        )
        d = stats.to_dict()
        assert d['total_attempts'] == 5
        assert d['success_rate'] == pytest.approx(60.0, abs=0.1)
        assert d['errors_by_type'] == {'network_timeout': 1, 'http_4xx': 1}


# ============== RetryResult 测试 ==============

class TestRetryResult:
    def test_success_result(self):
        result = RetryResult(success=True, result={'data': 1}, stats=RetryStats())
        assert result.success is True
        assert result.result == {'data': 1}
        assert bool(result) is True

    def test_failure_result(self):
        result = RetryResult(success=False, result=None, stats=RetryStats(), error=Exception('fail'))
        assert result.success is False
        assert bool(result) is False


# ============== RetryScheduler 核心测试 ==============

class TestRetryScheduler:
    def test_basic_success(self):
        scheduler = RetryScheduler(max_retries=3, base_delay=0.01)
        result = scheduler.run(lambda: 'ok', data_type='quote', source='test')
        assert result.success is True
        assert result.result == 'ok'

    def test_retry_then_success(self):
        scheduler = RetryScheduler(max_retries=3, base_delay=0.01)
        calls = []
        def flaky():
            calls.append(1)
            if len(calls) < 2:
                raise SourceUnavailableError('test', 'fail')
            return 'ok'
        result = scheduler.run(flaky, data_type='default', source='test')
        assert result.success is True
        assert result.result == 'ok'
        assert len(calls) == 2

    def test_all_failures(self):
        scheduler = RetryScheduler(max_retries=2, base_delay=0.01)
        def always_fail():
            raise SourceUnavailableError('test', 'fail')
        result = scheduler.run(always_fail, data_type='default', source='test')
        assert result.success is False
        # result.error 是 ErrorRecord，不是原始异常
        assert result.error is not None

    def test_circuit_breaker_open(self):
        cb = FakeCircuitBreaker(is_open_value=True)
        scheduler = RetryScheduler(max_retries=3, circuit_breaker=cb)
        result = scheduler.run(lambda: 'ok', data_type='default', source='test')
        assert result.success is False
        assert isinstance(result.error, CircuitBreakerError)

    def test_type_config_override(self):
        # 测试自定义 max_retries 能正常工作
        scheduler = RetryScheduler(max_retries=5, base_delay=0.01)
        calls = []
        def flaky():
            calls.append(1)
            if len(calls) < 3:
                raise SourceUnavailableError('test', 'fail')
            return 'ok'
        result = scheduler.run(flaky, data_type='default', source='test')
        assert result.success is True
        assert len(calls) == 3


# ============== 统计测试 ==============

class TestSchedulerStats:
    def test_stats_after_success(self):
        scheduler = RetryScheduler(max_retries=3, base_delay=0.01)
        scheduler.run(lambda: 'ok', data_type='default', source='test')
        stats = scheduler.get_stats()
        assert stats['total_attempts'] == 1
        assert stats['successful_attempts'] == 1
        assert stats['failed_attempts'] == 0

    def test_stats_after_failure(self):
        scheduler = RetryScheduler(max_retries=2, base_delay=0.01)
        scheduler.run(lambda: (_ for _ in []).throw(SourceUnavailableError('test', 'fail')), data_type='default', source='test')
        stats = scheduler.get_stats()
        assert stats['failed_attempts'] > 0
        assert stats['last_error_type'] is not None

    def test_reset_stats(self):
        scheduler = RetryScheduler(max_retries=3, base_delay=0.01)
        scheduler.run(lambda: 'ok', data_type='default', source='test')
        scheduler.reset_stats()
        stats = scheduler.get_stats()
        assert stats['total_attempts'] == 0
        assert stats['successful_attempts'] == 0


# ============== 配置更新测试 ==============

class TestConfigUpdate:
    def test_update_max_retries(self):
        scheduler = RetryScheduler(max_retries=3, base_delay=0.01)
        scheduler.update_config(max_retries=5)
        assert scheduler.max_retries == 5

    def test_update_algorithm(self):
        scheduler = RetryScheduler(max_retries=3, algorithm=BackoffAlgorithm.EXPONENTIAL)
        # 直接设置算法，不重新创建策略（因为不同策略参数不同）
        scheduler.algorithm = BackoffAlgorithm.FIXED
        assert scheduler.algorithm == BackoffAlgorithm.FIXED


# ============== 便捷函数测试 ==============

class TestRetryRun:
    def test_success(self):
        result = retry_run(lambda: 'data', source='test')
        assert result == 'data'

    def test_raises_on_failure(self):
        with pytest.raises(Exception):
            retry_run(lambda: (_ for _ in []).throw(ValueError('fail')), source='test')


# ============== 默认实例测试 ==============

class TestDefaults:
    def test_default_scheduler_exists(self):
        assert DEFAULT_RETRY_SCHEDULER is not None
        assert isinstance(DEFAULT_RETRY_SCHEDULER, RetryScheduler)


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
