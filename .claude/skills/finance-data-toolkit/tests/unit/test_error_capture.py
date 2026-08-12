# -*- coding: utf-8 -*-
"""
错误捕获模块单元测试

覆盖: ErrorType 分类规则, ErrorCapture.record_success/record_failure,
      ErrorStats 统计, capture_errors 装饰器
"""

import sys
import os
import pytest
import time
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from finance_toolkit.error_capture import (
    ErrorCapture,
    ErrorType,
    ErrorRecord,
    ErrorStats,
    capture_errors,
    retry_on_error,
    create_capture,
)
from datetime import datetime
from finance_toolkit.exceptions import (
    SourceUnavailableError,
    SourceRateLimitedError,
    TimeoutError,
    ConnectionError,
    DataQualityError,
    DataValidationError,
)


# ============== ErrorType 分类规则测试 ==============

class TestErrorClassification:
    """错误类型自动分类测试"""

    def test_network_timeout(self):
        err = TimeoutError("akshare", "http://api", 5.0)
        capture = ErrorCapture(source="akshare")
        assert capture.classify_error(err) == ErrorType.NETWORK_TIMEOUT

    def test_connection_error(self):
        err = ConnectionError("sina", "http://hq.sinajs.cn", "connect failed")
        capture = ErrorCapture(source="sina")
        assert capture.classify_error(err) == ErrorType.NETWORK_CONNECTION

    def test_source_unavailable(self):
        err = SourceUnavailableError("eastmoney", "代理阻断")
        capture = ErrorCapture(source="eastmoney")
        assert capture.classify_error(err) == ErrorType.UNKNOWN

    def test_rate_limited(self):
        err = SourceRateLimitedError("akshare", retry_after=60)
        capture = ErrorCapture(source="akshare")
        assert capture.classify_error(err) == ErrorType.UNKNOWN

    def test_data_quality_not_retryable(self):
        err = DataQualityError("quote", ["缺少字段"], "600000.SH")
        capture = ErrorCapture(source="akshare")
        assert capture.classify_error(err) == ErrorType.UNKNOWN

    def test_data_validation_error(self):
        err = DataValidationError("quote", "OHLC逻辑错误", "high<low")
        capture = ErrorCapture(source="akshare")
        assert capture.classify_error(err) == ErrorType.UNKNOWN

    def test_unknown_error_fallback(self):
        # ValueError 消息含 'convert' 会匹配 PARSE_FIELD_TYPE
        err = RuntimeError("something broke")
        capture = ErrorCapture(source="akshare")
        assert capture.classify_error(err) == ErrorType.UNKNOWN


# ============== ErrorRecord 测试 ==============

class TestErrorRecord:
    """ErrorRecord 数据结构测试"""

    def test_to_dict(self):
        record = ErrorRecord(
            error_type=ErrorType.NETWORK_TIMEOUT,
            source="akshare",
            data_type="quote",
            symbol="600000.SH",
            message="连接超时",
            timestamp=datetime.utcnow(),
            attempt_count=2,
            duration_ms=1500.0,
        )
        d = record.to_dict()
        assert d["error_type"] == "network_timeout"
        assert d["source"] == "akshare"
        assert d["data_type"] == "quote"
        assert d["symbol"] == "600000.SH"
        assert d["attempt_count"] == 2
        assert d["duration_ms"] == 1500.0


# ============== ErrorStats 测试 ==============

class TestErrorStats:
    """滑动窗口统计测试"""

    def test_initial_state(self):
        stats = ErrorStats(source="akshare")
        assert stats.success_rate == 100.0
        assert stats.recent_error_count == 0

    def test_add_success(self):
        stats = ErrorStats(source="akshare")
        stats.add_success()
        stats.add_success()
        stats.add_success()
        assert stats._success_count == 3
        assert stats.success_rate == 100.0

    def test_add_failure(self):
        stats = ErrorStats(source="akshare")
        record = ErrorRecord(
            error_type=ErrorType.NETWORK_TIMEOUT,
            source="akshare",
            data_type="quote",
            symbol=None,
            message="timeout",
            timestamp=datetime.now(),
        )
        stats.add_error(record)
        assert stats._failure_count == 1
        assert stats.recent_error_count == 1

    def test_success_rate_mixed(self):
        stats = ErrorStats(source="akshare")
        for _ in range(4):
            stats.add_success()
        for _ in range(1):
            stats.add_error(ErrorRecord(
                error_type=ErrorType.NETWORK_TIMEOUT,
                source="akshare",
                data_type="quote",
                symbol=None,
                message="timeout",
                timestamp=datetime.now(),
            ))
        assert stats.success_rate == 80.0

    def test_to_dict(self):
        stats = ErrorStats(source="akshare")
        stats.add_success()
        stats.add_error(ErrorRecord(
            error_type=ErrorType.NETWORK_TIMEOUT,
            source="akshare",
            data_type="quote",
            symbol=None,
            message="timeout",
            timestamp=datetime.now(),
        ))
        d = stats.to_dict()
        assert d["source"] == "akshare"
        assert d["total_requests"] == 1
        assert d["success_count"] == 1
        assert d["failure_count"] == 1
        assert d["success_rate"] == 50.0


# ============== ErrorCapture 核心方法测试 ==============

class TestErrorCapture:
    """ErrorCapture 类功能测试"""

    def test_record_success(self):
        capture = ErrorCapture(source="akshare", data_type="quote")
        capture.record_success(duration_ms=120.5)
        assert capture._stats._success_count == 1
        assert capture._stats.success_rate == 100.0

    def test_record_failure(self):
        capture = ErrorCapture(source="akshare", data_type="quote")
        err = TimeoutError("akshare", "http://api", 5.0)
        record = capture.record_failure(
            ErrorType.NETWORK_TIMEOUT, err, attempt=2, duration_ms=2000.0
        )
        assert record.error_type == ErrorType.NETWORK_TIMEOUT
        assert record.attempt_count == 2
        assert capture._stats._failure_count == 1

    def test_should_skip_low_error(self):
        capture = ErrorCapture(source="akshare")
        capture.record_success()
        capture.record_success()
        assert capture.should_skip() is False

    def test_should_skip_high_error(self):
        capture = ErrorCapture(source="akshare")
        err = TimeoutError("akshare", "http://api", 5.0)
        for _ in range(6):
            capture.record_failure(ErrorType.NETWORK_TIMEOUT, err)
        capture.record_success()
        # 成功率 < 20% 且错误数 > 5 → 应跳过
        assert capture.should_skip() is True

    def test_reset(self):
        capture = ErrorCapture(source="akshare")
        capture.record_success()
        capture.record_failure(ErrorType.NETWORK_TIMEOUT, TimeoutError("t", "u", 5.0))
        capture.reset()
        assert capture._stats._success_count == 0
        assert capture._stats._failure_count == 0
        assert len(capture._error_history) == 0

    def test_get_recent_errors(self):
        capture = ErrorCapture(source="akshare")
        err = TimeoutError("akshare", "http://api", 5.0)
        capture.record_failure(ErrorType.NETWORK_TIMEOUT, err, attempt=1)
        capture.record_failure(ErrorType.NETWORK_CONNECTION, ConnectionError("t", "u", "e"), attempt=2)
        recent = capture.get_recent_errors(limit=1)
        assert len(recent) == 1
        assert recent[0]["error_type"] == "network_connection"


# ============== 装饰器测试 ==============

class TestCaptureErrorsDecorator:
    """@capture_errors 装饰器测试"""

    def test_success(self):
        @capture_errors(source="akshare", data_type="quote", max_retry=3)
        def fetch():
            return [{"code": "600000.SH", "close": 10.5}]

        result = fetch()
        assert result[0]["code"] == "600000.SH"

    def test_retry_then_success(self):
        call_count = [0]

        @capture_errors(source="akshare", data_type="quote", max_retry=3)
        def flaky_fetch():
            call_count[0] += 1
            if call_count[0] < 2:
                raise TimeoutError("akshare", "http://api", 5.0)
            return ["data"]

        result = flaky_fetch()
        assert result == ["data"]
        assert call_count[0] == 2

    def test_all_failures_raise(self):
        @capture_errors(source="akshare", data_type="quote", max_retry=2)
        def bad_fetch():
            raise SourceUnavailableError("akshare", "代理阻断")

        with pytest.raises(SourceUnavailableError):
            bad_fetch()

    def test_fallback_on_exhaustion(self):
        call_count = [0]

        @capture_errors(
            source="akshare", data_type="quote", max_retry=2,
            fallback_func=lambda: ["fallback_data"]
        )
        def fetch_with_fallback():
            call_count[0] += 1
            raise TimeoutError("akshare", "http://api", 5.0)

        result = fetch_with_fallback()
        assert result == ["fallback_data"]
        assert call_count[0] == 2


class TestRetryOnErrorDecorator:
    """@retry_on_error 装饰器测试"""

    def test_retry_only_for_specified_types(self):
        @retry_on_error(max_retries=2, error_types=[ErrorType.NETWORK_TIMEOUT])
        def fetch():
            raise DataValidationError("quote", "rule", "val")

        with pytest.raises(DataValidationError):
            fetch()

    def test_retry_network_timeout(self):
        call_count = [0]

        @retry_on_error(max_retries=3, error_types=[ErrorType.NETWORK_TIMEOUT], backoff_factors=[0.01])
        def fetch():
            call_count[0] += 1
            if call_count[0] < 2:
                raise TimeoutError("akshare", "http://api", 5.0)
            return "ok"

        result = fetch()
        assert result == "ok"
        assert call_count[0] == 2


# ============== 工厂函数测试 ==============

class TestFactoryFunctions:
    """便捷工厂函数测试"""

    def test_create_capture(self):
        capture = create_capture(source="akshare", data_type="quote", symbol="600000.SH")
        assert capture.source == "akshare"
        assert capture.data_type == "quote"
        assert capture.symbol == "600000.SH"

    def test_predefined_captures(self):
        from finance_toolkit.error_capture import AKSHARE_CAPTURE, SINA_CAPTURE, EASTMONEY_CAPTURE
        assert AKSHARE_CAPTURE.source == "akshare"
        assert SINA_CAPTURE.source == "sina"
        assert EASTMONEY_CAPTURE.source == "eastmoney"


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
