# -*- coding: utf-8 -*-
"""
告警聚合模块单元测试

测试覆盖：
- Alert 数据类
- AlertAggregator 聚合逻辑
- AlertSuppressionRule 抑制规则
- AlertNotifier 通知器
- 分级阈值配置
"""
import pytest
import sys
import time
from pathlib import Path
from unittest.mock import Mock, patch

# 添加 skill 目录到路径
SKILL_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from src.reliability.alert_aggregator import (
    Alert,
    AlertSeverity,
    AlertAggregator,
    AlertSuppressionRule,
    AlertNotifier,
    ALERT_THRESHOLDS,
    get_alert_aggregator,
    get_alert_notifier,
    reset_alert_system,
)


class TestAlert:
    """Alert 数据类测试"""
    
    def test_create_alert(self):
        """测试创建告警"""
        alert = Alert(
            metric_name="success_rate",
            severity=AlertSeverity.ERROR,
            message="Success rate below threshold",
            source="baidu_search",
            details={"current": 0.75},
        )
        assert alert.metric_name == "success_rate"
        assert alert.severity == AlertSeverity.ERROR
        assert alert.source == "baidu_search"
        assert alert.details["current"] == 0.75
        assert isinstance(alert.timestamp, float)
    
    def test_alert_to_dict(self):
        """测试告警序列化"""
        alert = Alert(
            metric_name="test",
            severity=AlertSeverity.WARNING,
            message="Test message",
        )
        data = alert.to_dict()
        assert data["metric_name"] == "test"
        assert data["severity"] == "warning"
        assert data["message"] == "Test message"
        assert "timestamp" in data
    
    def test_alert_repr(self):
        """测试告警字符串表示"""
        alert = Alert(
            metric_name="test",
            severity=AlertSeverity.CRITICAL,
            message="Critical alert",
        )
        repr_str = repr(alert)
        assert "Alert" in repr_str
        assert "critical" in repr_str


class TestAlertAggregator:
    """AlertAggregator 聚合逻辑测试"""
    
    def setup_method(self):
        """每个测试前重置聚合器"""
        reset_alert_system()
    
    def test_initial_state(self):
        """测试初始状态"""
        agg = AlertAggregator(window_seconds=300)
        assert agg.get_alert_count() == 0
        status = agg.get_status()
        assert status["total_alerts"] == 0
        assert status["window_seconds"] == 300
    
    def test_add_single_alert(self):
        """测试添加单个告警"""
        agg = AlertAggregator(window_seconds=300)
        alert = Alert(
            metric_name="success_rate",
            severity=AlertSeverity.ERROR,
            message="Rate below 76%",
        )
        agg.add_alert(alert)
        assert agg.get_alert_count() == 1
    
    def test_aggregation_threshold(self):
        """测试聚合阈值（3次触发聚合）"""
        agg = AlertAggregator(window_seconds=300)
        handler_called = []
        
        def mock_handler(alert):
            handler_called.append(alert)
        
        agg.register_handler(mock_handler)
        
        # 添加3条相同类型的告警
        for i in range(3):
            agg.add_alert(Alert(
                metric_name="success_rate",
                severity=AlertSeverity.ERROR,
                message=f"Rate {i}",
            ))
        
        # 应该触发聚合
        assert len(handler_called) == 1
    
    def test_different_metrics_not_aggregated(self):
        """测试不同指标不聚合"""
        agg = AlertAggregator(window_seconds=300)
        
        agg.add_alert(Alert(
            metric_name="success_rate",
            severity=AlertSeverity.ERROR,
            message="Rate low",
        ))
        agg.add_alert(Alert(
            metric_name="response_time",
            severity=AlertSeverity.WARNING,
            message="Time high",
        ))
        
        assert agg.get_alert_count() == 2
    
    def test_suppression_rule(self):
        """测试抑制规则"""
        agg = AlertAggregator(window_seconds=60)  # 短窗口用于测试
        
        # 添加第一条告警
        alert1 = Alert(
            metric_name="test",
            severity=AlertSeverity.WARNING,
            message="First",
        )
        agg.add_alert(alert1)
        
        # 短时间内添加相同告警应该被抑制
        alert2 = Alert(
            metric_name="test",
            severity=AlertSeverity.WARNING,
            message="Second",
        )
        agg.add_alert(alert2)
        
        # 第二条应该被抑制（不增加计数）
        assert agg.get_alert_count() == 1
    
    def test_expired_alerts_cleaned(self):
        """测试过期告警清理"""
        agg = AlertAggregator(window_seconds=1)  # 1秒窗口
        
        alert = Alert(
            metric_name="test",
            severity=AlertSeverity.INFO,
            message="Old alert",
        )
        agg.add_alert(alert)
        
        # 等待过期
        time.sleep(1.1)
        
        # 清理后应该为空
        agg._cleanup_alerts("test:info")
        assert agg.get_alert_count() == 0
    
    def test_get_alert_count_by_metric(self):
        """测试按指标获取告警数量"""
        agg = AlertAggregator(window_seconds=300)
        
        for i in range(3):
            agg.add_alert(Alert(
                metric_name="success_rate",
                severity=AlertSeverity.ERROR,
                message=f"Rate {i}",
            ))
        
        assert agg.get_alert_count("success_rate") == 3
        assert agg.get_alert_count("nonexistent") == 0


class TestAlertSuppressionRule:
    """AlertSuppressionRule 抑制规则测试"""
    
    def test_suppress_after_threshold(self):
        """测试超过阈值后抑制"""
        rule = AlertSuppressionRule(
            metric_name="test",
            window_seconds=300,
            max_count=2,
        )
        
        # 前2次不抑制
        assert rule.should_suppress("source1") == False
        assert rule.should_suppress("source1") == False
        
        # 第3次开始抑制
        assert rule.should_suppress("source1") == True
    
    def test_different_sources_independent(self):
        """测试不同源独立计数"""
        rule = AlertSuppressionRule(
            metric_name="test",
            window_seconds=300,
            max_count=1,
        )
        
        # source1 触发抑制
        rule.should_suppress("source1")
        assert rule.should_suppress("source1") == True
        
        # source2 不受影响
        assert rule.should_suppress("source2") == False
    
    def test_window_reset(self):
        """测试窗口重置"""
        rule = AlertSuppressionRule(
            metric_name="test",
            window_seconds=1,  # 1秒窗口
            max_count=1,
        )
        
        rule.should_suppress("source1")
        assert rule.should_suppress("source1") == True
        
        # 等待窗口过期
        time.sleep(1.1)
        
        # 应该重置，不再抑制
        assert rule.should_suppress("source1") == False


class TestAlertNotifier:
    """AlertNotifier 通知器测试"""
    
    def test_register_and_notify(self):
        """测试注册和通知"""
        notifier = AlertNotifier()
        received = []
        
        def mock_handler(alert):
            received.append(alert)
        
        notifier.register_channel("test", mock_handler)
        
        alert = Alert(
            metric_name="test",
            severity=AlertSeverity.ERROR,
            message="Test",
        )
        notifier.notify(alert)
        
        assert len(received) == 1
        assert received[0].metric_name == "test"
    
    def test_multiple_channels(self):
        """测试多通道通知"""
        notifier = AlertNotifier()
        received = []
        
        notifier.register_channel("channel1", lambda a: received.append("c1"))
        notifier.register_channel("channel2", lambda a: received.append("c2"))
        
        alert = Alert(
            metric_name="test",
            severity=AlertSeverity.WARNING,
            message="Test",
        )
        notifier.notify(alert)
        
        assert "c1" in received
        assert "c2" in received
    
    def test_handler_error_does_not_crash(self):
        """测试处理器错误不崩溃"""
        notifier = AlertNotifier()
        
        def bad_handler(alert):
            raise ValueError("Handler error")
        
        notifier.register_channel("bad", bad_handler)
        
        # 不应该抛出异常
        alert = Alert(
            metric_name="test",
            severity=AlertSeverity.INFO,
            message="Test",
        )
        notifier.notify(alert)


class TestAlertThresholds:
    """分级告警阈值配置测试"""
    
    def test_critical_thresholds(self):
        """测试严重级别阈值"""
        thresholds = ALERT_THRESHOLDS[AlertSeverity.CRITICAL]
        assert thresholds["success_rate"] == 0.50
        assert thresholds["retry_failure_rate"] == 0.50
        assert thresholds["response_time"] == 60.0
    
    def test_error_thresholds(self):
        """测试错误级别阈值"""
        thresholds = ALERT_THRESHOLDS[AlertSeverity.ERROR]
        assert thresholds["success_rate"] == 0.76
        assert thresholds["retry_failure_rate"] == 0.30
    
    def test_warning_thresholds(self):
        """测试警告级别阈值"""
        thresholds = ALERT_THRESHOLDS[AlertSeverity.WARNING]
        assert thresholds["success_rate"] == 0.90
        assert thresholds["retry_failure_rate"] == 0.20
    
    def test_all_severities_present(self):
        """测试所有级别都存在"""
        for severity in AlertSeverity:
            assert severity in ALERT_THRESHOLDS


class TestGlobalInstances:
    """全局实例测试"""
    
    def test_get_aggregator_creates_instance(self):
        """测试获取聚合器实例"""
        reset_alert_system()
        agg = get_alert_aggregator()
        assert isinstance(agg, AlertAggregator)
    
    def test_get_notifier_creates_instance(self):
        """测试获取通知器实例"""
        reset_alert_system()
        notifier = get_alert_notifier()
        assert isinstance(notifier, AlertNotifier)
    
    def test_singleton_behavior(self):
        """测试单例行为"""
        reset_alert_system()
        agg1 = get_alert_aggregator()
        agg2 = get_alert_aggregator()
        assert agg1 is agg2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
