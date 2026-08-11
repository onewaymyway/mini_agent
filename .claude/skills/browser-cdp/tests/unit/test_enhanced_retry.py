# -*- coding: utf-8 -*-
"""
增强重试框架单元测试
"""

import pytest
import time
from unittest.mock import Mock, patch

from src.reliability.enhanced_retry import (
    RetryConfig,
    CircuitBreaker,
    RetryStats,
    BackoffStrategy,
    retry_operation,
    retry_operation_async,
    get_config_for_error,
)
from src.reliability.error import (
    CDPConnectionLostError,
    CDPCommandTimeoutError,
    ElementNotFoundError,
    CaptchaDetectedError,
    BlockedByAntiBotError,
    ErrorCategory,
)


class TestRetryConfig:
    """RetryConfig 测试"""
    
    def test_default_config(self):
        config = RetryConfig()
        assert config.max_retries == 3
        assert config.base_delay == 1.0
        assert config.max_delay == 30.0
        assert config.backoff_strategy == BackoffStrategy.EXPONENTIAL_JITTER
    
    def test_for_error_category_connection(self):
        config = RetryConfig.for_error_category(ErrorCategory.CONNECTION)
        assert config.max_retries == 5
        assert config.backoff_strategy == BackoffStrategy.EXPONENTIAL_JITTER
        assert config.circuit_breaker == True
    
    def test_for_error_category_timeout(self):
        config = RetryConfig.for_error_category(ErrorCategory.TIMEOUT)
        assert config.max_retries == 3
        assert config.backoff_strategy == BackoffStrategy.EXPONENTIAL
        assert config.circuit_breaker == True
    
    def test_for_error_category_element(self):
        config = RetryConfig.for_error_category(ErrorCategory.ELEMENT)
        assert config.max_retries == 3
        assert config.backoff_strategy == BackoffStrategy.LINEAR
        assert config.circuit_breaker == False
    
    def test_for_error_category_captcha(self):
        config = RetryConfig.for_error_category(ErrorCategory.CONTENT)
        assert config.max_retries == 0
        assert config.backoff_strategy == BackoffStrategy.FIXED
    
    def test_for_error_category_anti_bot(self):
        config = RetryConfig.for_error_category(ErrorCategory.PERMISSION)
        assert config.max_retries == 0
    
    def test_for_operation(self):
        config = RetryConfig.for_operation("navigation")
        assert config.max_retries == 3
        assert config.base_delay == 2.0
    
    def test_adaptive_config(self):
        error = CDPConnectionLostError()
        config = RetryConfig.adaptive(error)
        assert config.max_retries == 5
        assert config.error_category == ErrorCategory.CONNECTION


class TestCircuitBreaker:
    """CircuitBreaker 测试"""
    
    def test_initial_state(self):
        cb = CircuitBreaker()
        assert cb.state == "closed"
        assert cb.failure_count == 0
    
    def test_can_execute_when_closed(self):
        cb = CircuitBreaker()
        assert cb.can_execute() == True
    
    def test_trips_after_threshold(self):
        cb = CircuitBreaker(failure_threshold=3)
        cb.record_failure()
        cb.record_failure()
        assert cb.can_execute() == True  # 2 failures, not yet tripped
        cb.record_failure()
        assert cb.can_execute() == False  # 3 failures, tripped
        assert cb.state == "open"
    
    def test_recovers_after_timeout(self):
        cb = CircuitBreaker(failure_threshold=2, recovery_timeout=0.1)
        cb.record_failure()
        cb.record_failure()
        assert cb.state == "open"
        time.sleep(0.15)
        assert cb.can_execute() == True  # Should be half-open
        assert cb.state == "half_open"
    
    def test_closes_after_successful_probe(self):
        cb = CircuitBreaker(failure_threshold=2, recovery_timeout=0.1)
        cb.record_failure()
        cb.record_failure()
        time.sleep(0.15)
        cb.can_execute()  # Transition to half_open
        cb.record_success()
        assert cb.state == "closed"
    
    def test_reopens_after_failed_probe(self):
        cb = CircuitBreaker(failure_threshold=2, recovery_timeout=0.1)
        cb.record_failure()
        cb.record_failure()
        time.sleep(0.15)
        cb.can_execute()  # Transition to half_open
        cb.record_failure()
        assert cb.state == "open"
    
    def test_get_status(self):
        cb = CircuitBreaker(failure_threshold=5)
        cb.record_failure()
        status = cb.get_status()
        assert status["state"] == "closed"
        assert status["failure_count"] == 1
        assert status["trip_count"] == 0


class TestRetryStats:
    """RetryStats 测试"""
    
    def test_record_success(self):
        stats = RetryStats()
        stats.record_success(duration=1.5)
        assert stats.total_attempts == 1
        assert stats.success_attempts == 1
        assert stats.get_success_rate() == 1.0
    
    def test_record_failure(self):
        stats = RetryStats()
        stats.record_failure("timeout")
        assert stats.failure_attempts == 1
        assert stats.retry_counts["timeout"] == 1
    
    def test_get_avg_duration(self):
        stats = RetryStats()
        stats.record_success(duration=1.0)
        stats.record_success(duration=2.0)
        assert stats.get_avg_duration() == 1.5
    
    def test_to_dict(self):
        stats = RetryStats()
        stats.record_success(duration=1.0)
        stats.record_failure("timeout")
        d = stats.to_dict()
        assert d["total_attempts"] == 2
        assert d["success_rate"] == 0.5


class TestRetryOperation:
    """retry_operation 测试"""
    
    def test_success_on_first_attempt(self):
        mock_func = Mock(return_value="success")
        result = retry_operation(mock_func, operation="test")
        assert result == "success"
        mock_func.assert_called_once()
    
    def test_success_after_retries(self):
        mock_func = Mock(side_effect=[CDPConnectionLostError(), CDPConnectionLostError(), "success"])
        config = RetryConfig(max_retries=3)
        result = retry_operation(mock_func, config=config, operation="test")
        assert result == "success"
        assert mock_func.call_count == 3
    
    def test_exhausted_retries(self):
        mock_func = Mock(side_effect=CDPConnectionLostError())
        config = RetryConfig(max_retries=2)
        with pytest.raises(CDPConnectionLostError):
            retry_operation(mock_func, config=config, operation="test")
        assert mock_func.call_count == 3  # 1 initial + 2 retries
    
    def test_non_retryable_error_stops_immediately(self):
        mock_func = Mock(side_effect=CaptchaDetectedError())
        with pytest.raises(CaptchaDetectedError):
            retry_operation(mock_func, operation="test", max_retries=3)
        mock_func.assert_called_once()  # No retry for non-recoverable
    
    def test_circuit_breaker_blocks_execution(self):
        cb = CircuitBreaker(failure_threshold=2, recovery_timeout=10.0)
        cb.record_failure()
        cb.record_failure()
        
        mock_func = Mock(side_effect=CDPConnectionLostError())
        with pytest.raises(CDPConnectionLostError):
            retry_operation(
                mock_func,
                operation="test",
                circuit_breaker=cb,
                max_retries=3,
            )
    
    def test_custom_backoff_strategy(self):
        delays = []
        def tracking_func():
            raise ElementNotFoundError(selector="test")

        def on_retry(attempt, error, delay):
            delays.append(delay)

        config = RetryConfig(
            max_retries=3,
            backoff_strategy=BackoffStrategy.LINEAR,
            base_delay=0.5,
            on_retry=on_retry,
        )

        with patch('time.sleep'):
            with pytest.raises(ElementNotFoundError):
                retry_operation(tracking_func, config=config, operation="test")

        assert len(delays) == 3
        # LINEAR: delay = base_delay * attempt, so 0.5*0=0, 0.5*1=0.5, 0.5*2=1.0
        assert delays[0] == 0.0
        assert delays[1] == 0.5
        assert delays[2] == 1.0


class TestGetConfigForError:
    """get_config_for_error 测试"""
    
    def test_connection_error(self):
        config = get_config_for_error(CDPConnectionLostError())
        assert config.max_retries == 5
        assert config.error_category == ErrorCategory.CONNECTION
    
    def test_timeout_error(self):
        config = get_config_for_error(CDPCommandTimeoutError("navigate", 30.0))
        assert config.max_retries == 3
        assert config.error_category == ErrorCategory.TIMEOUT
    
    def test_element_error(self):
        config = get_config_for_error(ElementNotFoundError(selector="#btn"))
        assert config.max_retries == 3
        assert config.error_category == ErrorCategory.ELEMENT
    
    def test_captcha_error(self):
        config = get_config_for_error(CaptchaDetectedError())
        assert config.max_retries == 0
        assert config.error_category == ErrorCategory.CONTENT
    
    def test_anti_bot_error(self):
        config = get_config_for_error(BlockedByAntiBotError())
        assert config.max_retries == 0
        assert config.error_category == ErrorCategory.PERMISSION


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
