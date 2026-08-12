# -*- coding: utf-8 -*-
"""
数据抓取错误重试机制 - 单元测试

覆盖: ExponentialBackoffRetry, FixedIntervalRetry, ConditionalRetry,
      retry_with_backoff 装饰器, 默认策略实例
"""

import sys
import os
import time
import pytest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from finance_toolkit.retry_strategy import (
    ExponentialBackoffRetry,
    FixedIntervalRetry,
    ConditionalRetry,
    retry_with_backoff,
    exponential_backoff_retry,
    DEFAULT_RETRY_STRATEGY,
    ASYNC_DEFAULT_RETRY_STRATEGY,
    AsyncExponentialBackoffRetry,
)
from finance_toolkit.exceptions import (
    SourceUnavailableError,
    SourceRateLimitedError,
    DataQualityError,
    DataValidationError,
    TimeoutError,
    ConnectionError,
)


class TestExponentialBackoffRetry:
    """指数退避重试策略测试"""

    def test_success_first_attempt(self):
        """首次尝试成功"""
        strategy = ExponentialBackoffRetry(max_retries=3)
        result = strategy.execute(lambda: "ok")
        assert result == "ok"

    def test_success_after_retries(self):
        """重试后成功"""
        strategy = ExponentialBackoffRetry(max_retries=3, base_delay=0.01, jitter=False)
        call_count = 0

        def flaky():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise SourceUnavailableError("akshare", "临时网络故障")
            return "success"

        with patch('time.sleep', return_value=None):
            result = strategy.execute(flaky)
        assert result == "success"
        assert call_count == 3

    def test_max_retries_exhausted(self):
        """超过最大重试次数后抛出异常"""
        strategy = ExponentialBackoffRetry(max_retries=2, base_delay=0.01)

        def always_fail():
            raise SourceUnavailableError("eastmoney", "持续代理阻断")

        with pytest.raises(SourceUnavailableError):
            strategy.execute(always_fail)

    def test_delay_calculation(self):
        """延迟时间计算正确"""
        strategy = ExponentialBackoffRetry(max_retries=3, base_delay=1.0, factor=2.0, jitter=False)
        assert strategy.get_delay(0, None) == 1.0
        assert strategy.get_delay(1, None) == 2.0
        assert strategy.get_delay(2, None) == 4.0
        # max_delay 限制
        strategy_max = ExponentialBackoffRetry(max_retries=3, base_delay=1.0, factor=2.0, max_delay=5.0, jitter=False)
        assert strategy_max.get_delay(3, None) == 5.0

    def test_rate_limit_uses_retry_after(self):
        """限流时使用 retry_after 延迟"""
        strategy = ExponentialBackoffRetry(max_retries=3, base_delay=1.0, jitter=False)
        error = SourceRateLimitedError("akshare", retry_after=3)
        delay = strategy.get_delay(0, error)
        assert delay == 3.0

    def test_data_quality_not_retryable(self):
        """数据质量问题不应重试"""
        strategy = ExponentialBackoffRetry(max_retries=3)
        error_q = DataQualityError("quote", ["缺少必填字段"], "600000.SH")
        error_v = DataValidationError("quote", "OHLC逻辑错误", "high<low")
        assert not strategy.should_retry(0, error_q)
        assert not strategy.should_retry(0, error_v)

    def test_temporary_errors_retryable(self):
        """临时故障应重试"""
        strategy = ExponentialBackoffRetry(max_retries=3)
        assert strategy.should_retry(0, SourceUnavailableError("t", "e"))
        assert strategy.should_retry(0, TimeoutError("t", "http://x", 5.0))
        assert strategy.should_retry(0, ConnectionError("t", "http://x", "err"))


class TestFixedIntervalRetry:
    """固定间隔重试策略测试"""

    def test_fixed_delay(self):
        """固定延迟"""
        strategy = FixedIntervalRetry(max_retries=3, interval=5.0)
        assert strategy.get_delay(0, None) == 5.0
        assert strategy.get_delay(2, None) == 5.0

    def test_success_after_retry(self):
        """重试后成功"""
        strategy = FixedIntervalRetry(max_retries=2, interval=0.01)
        call_count = 0

        def flaky():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise SourceUnavailableError("t", "e")
            return "ok"

        result = strategy.execute(flaky)
        assert result == "ok"
        assert call_count == 2

    def test_data_quality_not_retryable(self):
        """数据质量问题不应重试"""
        strategy = FixedIntervalRetry(max_retries=3)
        assert not strategy.should_retry(0, DataQualityError("quote", ["err"]))
        assert not strategy.should_retry(0, DataValidationError("quote", "rule", "val"))


class TestConditionalRetry:
    """自定义条件重试策略测试"""

    def test_custom_should_retry(self):
        """自定义重试条件"""
        called = False

        def custom_should_retry(attempt, exc):
            nonlocal called
            called = True
            return attempt < 1

        strategy = ConditionalRetry(max_retries=3, should_retry_func=custom_should_retry)
        assert strategy.should_retry(0, Exception()) is True
        assert strategy.should_retry(1, Exception()) is False
        assert called is True

    def test_custom_delay(self):
        """自定义延迟"""
        strategy = ConditionalRetry(
            max_retries=3,
            delay_func=lambda a, e: 10.0
        )
        assert strategy.get_delay(0, None) == 10.0
        assert strategy.get_delay(2, None) == 10.0


class TestRetryDecorator:
    """retry_with_backoff 装饰器测试"""

    def test_sync_decorator_success_after_retry(self):
        """同步装饰器：重试后成功"""
        call_count = 0

        @retry_with_backoff(max_retries=3, backoff_factors=[0.01, 0.01])
        def fetch():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise SourceUnavailableError("t", "e")
            return "data"

        result = fetch()
        assert result == "data"
        assert call_count == 3

    def test_sync_decorator_all_fail(self):
        """同步装饰器：全部失败"""
        @retry_with_backoff(max_retries=2, backoff_factors=[0.01])
        def fail():
            raise SourceUnavailableError("t", "e")

        with pytest.raises(SourceUnavailableError):
            fail()

    def test_decorator_default_backoff(self):
        """默认退避因子 [1, 2, 5]"""
        call_count = 0

        @retry_with_backoff(max_retries=3)
        def fetch():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise TimeoutError("t", "http://x", 5.0)
            return "ok"

        result = fetch()
        assert result == "ok"
        assert call_count == 2


class TestDefaultStrategies:
    """默认策略实例测试"""

    def test_default_strategy_config(self):
        """默认策略配置验证"""
        assert DEFAULT_RETRY_STRATEGY.max_retries == 3
        assert DEFAULT_RETRY_STRATEGY.base_delay == 1.0
        assert DEFAULT_RETRY_STRATEGY.factor == 2.0
        assert DEFAULT_RETRY_STRATEGY.max_delay == 30.0

    def test_async_default_strategy_config(self):
        """异步默认策略配置验证"""
        assert ASYNC_DEFAULT_RETRY_STRATEGY.max_retries == 3
        # AsyncExponentialBackoffRetry 委托给内部 strategy
        assert ASYNC_DEFAULT_RETRY_STRATEGY.strategy.max_retries == 3
        assert ASYNC_DEFAULT_RETRY_STRATEGY.strategy.base_delay == 1.0
        assert ASYNC_DEFAULT_RETRY_STRATEGY.strategy.max_delay == 30.0


class TestIntegration:
    """集成测试：模拟真实数据抓取场景"""

    def test_transient_network_failure_recovery(self):
        """模拟网络抖动后恢复"""
        strategy = ExponentialBackoffRetry(max_retries=3, base_delay=0.01, jitter=False)

        def fetch_data():
            if not hasattr(fetch_data, 'count'):
                fetch_data.count = 0
            fetch_data.count += 1
            if fetch_data.count <= 2:
                raise ConnectionError("akshare", "http://api", "连接超时")
            return [{'code': '600000.SH', 'close': 10.5}]

        result = strategy.execute(fetch_data)
        assert result[0]['code'] == '600000.SH'
        assert fetch_data.count == 3

    def test_permanent_failure_circuit_breaks(self):
        """永久故障：不应无限重试"""
        strategy = ExponentialBackoffRetry(max_retries=2, base_delay=0.01)

        def bad_source():
            raise SourceUnavailableError("eastmoney", "代理阻断")

        with pytest.raises(SourceUnavailableError):
            strategy.execute(bad_source)

    def test_data_quality_issue_halts_retry(self):
        """数据质量错误：立即停止重试"""
        strategy = ExponentialBackoffRetry(max_retries=3)

        def bad_data():
            raise DataValidationError("quote", "OHLC逻辑错误", "high<low")

        with pytest.raises(DataValidationError):
            strategy.execute(bad_data)

    def test_mixed_failure_types(self):
        """混合故障：临时故障重试，数据质量不重试"""
        strategy = ExponentialBackoffRetry(max_retries=3, base_delay=0.01, jitter=False)
        attempts = []

        def mixed():
            attempts.append(time.time())
            if len(attempts) <= 2:
                raise TimeoutError("akshare", "http://api", 5.0)
            raise DataValidationError("quote", "字段缺失", [])

        with pytest.raises(DataValidationError):
            strategy.execute(mixed)
        # 应在第3次尝试时遇到数据质量问题并立即停止
        assert len(attempts) == 3


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
