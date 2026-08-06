"""
可靠性指标体系单元测试
"""

import pytest
import time
import json
from unittest.mock import MagicMock, patch

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from src.reliability.metrics import ReliabilityMetrics
from src.reliability.error import (
    ErrorCategory,
    CDPConnectionLostError,
    CDPCommandTimeoutError,
    ElementNotFoundError,
)


class TestReliabilityMetrics:
    """ReliabilityMetrics 类测试"""
    
    def test_init_default(self):
        """测试默认初始化"""
        metrics = ReliabilityMetrics()
        assert metrics.retry_count == 0
        assert metrics.retry_success_count == 0
        assert metrics.retry_failure_count == 0
        assert metrics.circuit_breaker_trips == 0
        assert metrics.connection_losses == 0
        assert metrics.connection_recovered == 0
        assert len(metrics.error_by_category) == 0
        assert len(metrics.error_by_type) == 0
    
    def test_record_retry_success(self):
        """测试记录重试成功"""
        metrics = ReliabilityMetrics()
        metrics.record_retry(success=True, operation="test_op")
        assert metrics.retry_count == 1
        assert metrics.retry_success_count == 1
        assert metrics.retry_failure_count == 0
    
    def test_record_retry_failure(self):
        """测试记录重试失败"""
        metrics = ReliabilityMetrics()
        metrics.record_retry(success=False, operation="test_op")
        assert metrics.retry_count == 1
        assert metrics.retry_success_count == 0
        assert metrics.retry_failure_count == 1
    
    def test_record_retry_multiple(self):
        """测试多次重试记录"""
        metrics = ReliabilityMetrics()
        for _ in range(5):
            metrics.record_retry(success=True)
        for _ in range(2):
            metrics.record_retry(success=False)
        
        assert metrics.retry_count == 7
        assert metrics.retry_success_count == 5
        assert metrics.retry_failure_count == 2
    
    def test_record_circuit_breaker(self):
        """测试熔断器记录"""
        metrics = ReliabilityMetrics()
        metrics.record_circuit_breaker_trip()
        metrics.record_circuit_breaker_trip()
        metrics.record_circuit_breaker_reset()
        
        assert metrics.circuit_breaker_trips == 2
        assert metrics.circuit_breaker_resets == 1
    
    def test_record_connection_events(self):
        """测试连接事件记录"""
        metrics = ReliabilityMetrics()
        metrics.record_connection_loss()
        metrics.record_connection_loss()
        metrics.record_connection_recovered()
        
        assert metrics.connection_losses == 2
        assert metrics.connection_recovered == 1
    
    def test_record_error(self):
        """测试错误记录"""
        metrics = ReliabilityMetrics()
        metrics.record_error(
            category=ErrorCategory.CONNECTION.value,
            error_type="CDPConnectionLostError",
            message="Connection lost",
        )
        
        assert metrics.error_by_category[ErrorCategory.CONNECTION.value] == 1
        assert metrics.error_by_type["CDPConnectionLostError"] == 1
        assert len(metrics._error_log) == 1
        assert metrics._error_log[0]["category"] == ErrorCategory.CONNECTION.value
        assert metrics._error_log[0]["error_type"] == "CDPConnectionLostError"
    
    def test_record_wait_strategy(self):
        """测试等待策略记录"""
        metrics = ReliabilityMetrics()
        metrics.record_wait_strategy("selector_visible", True)
        metrics.record_wait_strategy("selector_visible", True)
        metrics.record_wait_strategy("selector_visible", False)
        metrics.record_wait_strategy("network_idle", True)
        
        assert metrics.wait_strategy_success["selector_visible"] == 2
        assert metrics.wait_strategy_failure["selector_visible"] == 1
        assert metrics.wait_strategy_success["network_idle"] == 1
    
    def test_record_operation_duration(self):
        """测试操作耗时记录"""
        metrics = ReliabilityMetrics()
        metrics.record_operation_duration("search", 1.5)
        metrics.record_operation_duration("search", 2.0)
        metrics.record_operation_duration("navigate", 3.0)
        
        assert len(metrics.operation_durations["search"]) == 2
        assert metrics.operation_durations["search"][0] == 1.5
        assert metrics.operation_durations["search"][1] == 2.0
        assert metrics.operation_durations["navigate"][0] == 3.0
    
    def test_get_metrics(self):
        """测试获取指标快照"""
        metrics = ReliabilityMetrics()
        metrics.record_retry(success=True)
        metrics.record_retry(success=False)
        metrics.record_circuit_breaker_trip()
        metrics.record_connection_loss()
        metrics.record_connection_recovered()
        metrics.record_error(ErrorCategory.CONNECTION.value, "CDPConnectionLostError")
        metrics.record_wait_strategy("selector_visible", True)
        metrics.record_operation_duration("search", 1.5)
        
        result = metrics.get_metrics()
        
        assert result["retry"]["total"] == 2
        assert result["retry"]["success"] == 1
        assert result["retry"]["failure"] == 1
        assert result["retry"]["success_rate"] == 0.5
        assert result["circuit_breaker"]["trips"] == 1
        assert result["connection"]["losses"] == 1
        assert result["connection"]["recovered"] == 1
        assert result["connection"]["recovery_rate"] == 1.0
        assert result["errors_by_category"][ErrorCategory.CONNECTION.value] == 1
        assert "search" in result["operation_durations"]
        assert result["operation_durations"]["search"]["count"] == 1
        assert result["operation_durations"]["search"]["avg"] == 1.5
    
    def test_get_metrics_empty(self):
        """测试空指标快照"""
        metrics = ReliabilityMetrics()
        result = metrics.get_metrics()
        
        assert result["retry"]["total"] == 0
        assert result["retry"]["success_rate"] == 0.0
        assert result["connection"]["recovery_rate"] == 0.0
        assert len(result["errors_by_category"]) == 0
        assert len(result["operation_durations"]) == 0
    
    def test_to_json(self):
        """测试 JSON 序列化"""
        metrics = ReliabilityMetrics()
        metrics.record_retry(success=True)
        
        json_str = metrics.to_json(pretty=False)
        result = json.loads(json_str)
        
        assert result["retry"]["total"] == 1
        assert "timestamp" in result
        assert "uptime_seconds" in result
    
    def test_to_json_pretty(self):
        """测试格式化 JSON 输出"""
        metrics = ReliabilityMetrics()
        json_str = metrics.to_json(pretty=True)
        
        # 格式化 JSON 应该包含换行和缩进
        assert "\n" in json_str
        assert "  " in json_str
    
    def test_to_prometheus_format(self):
        """测试 Prometheus 格式输出"""
        metrics = ReliabilityMetrics()
        metrics.record_retry(success=True)
        metrics.record_retry(success=False)
        metrics.record_circuit_breaker_trip()
        metrics.record_error(ErrorCategory.CONNECTION.value, "CDPConnectionLostError")
        
        prom_str = metrics.to_prometheus_format()
        
        assert "browser_cdp_retry_total" in prom_str
        assert "browser_cdp_circuit_breaker_trips" in prom_str
        assert "browser_cdp_connection_losses" in prom_str
        assert "browser_cdp_errors_by_category" in prom_str
    
    def test_reset(self):
        """测试重置指标"""
        metrics = ReliabilityMetrics()
        metrics.record_retry(success=True)
        metrics.record_circuit_breaker_trip()
        metrics.record_connection_loss()
        metrics.record_error(ErrorCategory.CONNECTION.value, "CDPConnectionLostError")
        metrics.record_wait_strategy("selector_visible", True)
        metrics.record_operation_duration("search", 1.5)
        
        # 保存当前值
        prev_count = metrics.retry_count
        
        # 重置
        metrics.reset()
        
        # 验证重置
        assert metrics.retry_count == 0
        assert metrics.retry_success_count == 0
        assert metrics.retry_failure_count == 0
        assert metrics.circuit_breaker_trips == 0
        assert metrics.connection_losses == 0
        assert len(metrics.error_by_category) == 0
        assert len(metrics._error_log) == 0
    
    def test_error_log_rotation(self):
        """测试错误日志轮转"""
        metrics = ReliabilityMetrics()
        metrics._max_log_size = 5
        
        # 添加 10 条错误
        for i in range(10):
            metrics.record_error(
                category=ErrorCategory.TIMEOUT.value,
                error_type="TestError",
                message=f"Error {i}",
            )
        
        # 应该只保留最近 5 条
        assert len(metrics._error_log) == 5
        assert metrics._error_log[0]["message"] == "Error 5"
        assert metrics._error_log[-1]["message"] == "Error 9"
    
    def test_thread_safety(self):
        """测试线程安全性"""
        import threading
        
        metrics = ReliabilityMetrics()
        errors = []
        
        def record_errors():
            for i in range(100):
                try:
                    metrics.record_error(
                        category=ErrorCategory.CONNECTION.value,
                        error_type="TestError",
                        message=f"Error {i}",
                    )
                except Exception as e:
                    errors.append(e)
        
        threads = [threading.Thread(target=record_errors) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        
        # 应该没有错误，且记录了 1000 条
        assert len(errors) == 0
        assert metrics.retry_count == 0  # 没有记录重试
        assert metrics.error_by_category[ErrorCategory.CONNECTION.value] == 1000


class TestGetMetrics:
    """全局指标函数测试"""
    
    def test_get_metrics_returns_instance(self):
        """测试 get_metrics 返回实例"""
        from src.reliability.metrics import get_metrics
        
        metrics = get_metrics()
        assert isinstance(metrics, ReliabilityMetrics)
    
    def test_get_metrics_singleton(self):
        """测试 get_metrics 单例"""
        from src.reliability.metrics import get_metrics
        
        m1 = get_metrics()
        m2 = get_metrics()
        assert m1 is m2
    
    def test_reset_metrics(self):
        """测试 reset_metrics"""
        from src.reliability.metrics import get_metrics, reset_metrics
        
        metrics = get_metrics()
        metrics.record_retry(success=True)
        
        reset_metrics()
        
        # 重置后指标应归零（单例模式，同一实例）
        m2 = get_metrics()
        assert m2.retry_count == 0
        assert m2 is metrics  # 单例模式，同一实例


class TestMetricsIntegration:
    """指标集成测试"""
    
    def test_full_workflow(self):
        """测试完整工作流程"""
        metrics = ReliabilityMetrics()
        
        # 模拟一系列操作
        metrics.record_retry(success=True)
        metrics.record_retry(success=False)
        metrics.record_retry(success=True)
        metrics.record_circuit_breaker_trip()
        metrics.record_connection_loss()
        metrics.record_connection_recovered()
        metrics.record_error(ErrorCategory.CONNECTION.value, "CDPConnectionLostError")
        metrics.record_error(ErrorCategory.TIMEOUT.value, "CDPCommandTimeoutError")
        metrics.record_wait_strategy("selector_visible", True)
        metrics.record_wait_strategy("network_idle", False)
        metrics.record_operation_duration("search", 1.5)
        metrics.record_operation_duration("search", 2.0)
        
        # 获取指标
        result = metrics.get_metrics()
        
        # 验证所有指标
        assert result["retry"]["total"] == 3
        assert result["retry"]["success"] == 2
        assert result["retry"]["failure"] == 1
        assert result["retry"]["success_rate"] == round(2/3, 4)
        
        assert result["circuit_breaker"]["trips"] == 1
        
        assert result["connection"]["losses"] == 1
        assert result["connection"]["recovered"] == 1
        assert result["connection"]["recovery_rate"] == 1.0
        
        assert result["errors_by_category"][ErrorCategory.CONNECTION.value] == 1
        assert result["errors_by_category"][ErrorCategory.TIMEOUT.value] == 1
        
        assert result["wait_strategies"]["success"]["selector_visible"] == 1
        assert result["wait_strategies"]["failure"]["network_idle"] == 1
        
        assert "search" in result["operation_durations"]
        assert result["operation_durations"]["search"]["count"] == 2
        assert result["operation_durations"]["search"]["avg"] == 1.75
