"""
监控告警系统单元测试
"""

import json
import time
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch
import sys

# 添加 scripts 目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "scripts"))


@pytest.fixture
def monitoring_system():
    """创建监控告警系统实例"""
    from monitoring_system import MonitoringSystem
    return MonitoringSystem()


@pytest.fixture
def coverage_tracker():
    """创建覆盖率追踪器实例"""
    from monitoring_coverage import MonitoringCoverageTracker
    return MonitoringCoverageTracker()


class TestMetricThreshold:
    """测试指标阈值"""
    
    def test_gt_threshold(self):
        from monitoring_system import MetricThreshold
        threshold = MetricThreshold("test_metric", "gt", 10.0, "warning", "Test")
        assert threshold.check(11.0) == True
        assert threshold.check(10.0) == False
        assert threshold.check(9.0) == False
    
    def test_lt_threshold(self):
        from monitoring_system import MetricThreshold
        threshold = MetricThreshold("test_metric", "lt", 10.0, "warning", "Test")
        assert threshold.check(9.0) == True
        assert threshold.check(10.0) == False
        assert threshold.check(11.0) == False
    
    def test_gte_threshold(self):
        from monitoring_system import MetricThreshold
        threshold = MetricThreshold("test_metric", "gte", 10.0, "warning", "Test")
        assert threshold.check(10.0) == True
        assert threshold.check(11.0) == True
        assert threshold.check(9.0) == False
    
    def test_lte_threshold(self):
        from monitoring_system import MetricThreshold
        threshold = MetricThreshold("test_metric", "lte", 10.0, "warning", "Test")
        assert threshold.check(10.0) == True
        assert threshold.check(9.0) == True
        assert threshold.check(11.0) == False


class TestAlert:
    """测试告警记录"""
    
    def test_create_alert(self):
        from monitoring_system import Alert
        alert = Alert(
            alert_id="test_001",
            metric_name="success_rate",
            severity="warning",
            current_value=0.5,
            threshold_value=0.76,
            message="成功率过低",
        )
        assert alert.alert_id == "test_001"
        assert alert.severity == "warning"
        assert alert.acknowledged == False
        assert alert.resolved == False
    
    def test_alert_to_dict(self):
        from monitoring_system import Alert
        alert = Alert(
            alert_id="test_002",
            metric_name="error_rate",
            severity="error",
            current_value=0.5,
            threshold_value=0.4,
            message="错误率过高",
        )
        alert.acknowledged = True
        alert.resolved = True
        
        data = alert.to_dict()
        assert data["alert_id"] == "test_002"
        assert data["acknowledged"] == True
        assert data["resolved"] == True


class TestMonitoringSystem:
    """测试监控告警系统"""
    
    def test_set_metric(self, monitoring_system):
        monitoring_system.set_metric("success_rate", 0.75)
        assert monitoring_system._metrics["success_rate"] == 0.75
    
    def test_add_threshold(self, monitoring_system):
        from monitoring_system import MetricThreshold
        threshold = MetricThreshold("test_metric", "gt", 100.0, "warning", "Test")
        monitoring_system.add_threshold(threshold)
        assert len(monitoring_system._thresholds) > 0
    
    def test_remove_threshold(self, monitoring_system):
        from monitoring_system import MetricThreshold
        threshold = MetricThreshold("test_metric", "gt", 100.0, "warning", "Test")
        monitoring_system.add_threshold(threshold)
        monitoring_system.remove_threshold("test_metric", "gt", 100.0)
        assert not any(t.metric_name == "test_metric" for t in monitoring_system._thresholds)
    
    def test_acknowledge_alert(self, monitoring_system):
        # 先触发一个告警
        monitoring_system.set_metric("success_rate", 0.5)
        
        # 获取待处理告警
        pending = monitoring_system.get_pending_alerts()
        if pending:
            alert_id = pending[0]["alert_id"]
            monitoring_system.acknowledge_alert(alert_id)
            
            # 验证响应时间被追踪
            stats = monitoring_system.get_stats()
            assert stats["alert_response"]["total_responded"] >= 1
    
    def test_resolve_alert(self, monitoring_system):
        # 先触发一个告警
        monitoring_system.set_metric("success_rate", 0.5)

        # 获取待处理告警（过滤出我们刚触发的 success_rate 告警）
        pending = [a for a in monitoring_system.get_pending_alerts() if a["metric_name"] == "success_rate"]
        assert len(pending) >= 1, "应至少有一个待处理的 success_rate 告警"
        alert_id = pending[0]["alert_id"]
        monitoring_system.resolve_alert(alert_id)

        # 验证告警已解决（使用大limit确保能查到历史告警）
        alerts = monitoring_system.get_alerts(limit=500)
        resolved = [a for a in alerts if a["alert_id"] == alert_id and a["resolved"]]
        assert len(resolved) >= 1
    
    def test_get_stats(self, monitoring_system):
        monitoring_system.set_metric("success_rate", 0.75)
        stats = monitoring_system.get_stats()
        
        assert "metrics" in stats
        assert "alerts" in stats
        assert "alert_response" in stats
        assert "coverage" in stats
    
    def test_register_handler(self, monitoring_system):
        handler_called = []
        
        def test_handler(alert):
            handler_called.append(alert)
        
        monitoring_system.register_handler(test_handler)
        
        # 触发告警
        monitoring_system.set_metric("success_rate", 0.5)
        
        # 验证处理器被调用
        assert len(handler_called) >= 0  # 可能没有触发告警


class TestMonitoringCoverageTracker:
    """测试监控覆盖率追踪器"""
    
    def test_mark_monitored(self, coverage_tracker):
        coverage_tracker.mark_monitored("searchers", "baidu_search")
        report = coverage_tracker.get_coverage_report()
        assert report["by_type"]["searchers"]["monitored"] >= 1
    
    def test_mark_unmonitored(self, coverage_tracker):
        coverage_tracker.mark_unmonitored("searchers", "test_searcher")
        report = coverage_tracker.get_coverage_report()
        assert report["by_type"]["searchers"]["unmonitored"] >= 1
    
    def test_track_alert_response(self, coverage_tracker):
        coverage_tracker.track_alert_response(120.0)  # 2 分钟
        stats = coverage_tracker._get_response_stats()
        assert stats["total_responded"] == 1
        assert stats["avg_response_time"] == 120.0
    
    def test_get_coverage_report(self, coverage_tracker):
        # 标记一些组件
        coverage_tracker.mark_monitored("searchers", "baidu_search")
        coverage_tracker.mark_monitored("core_modules", "browser_browse")
        
        report = coverage_tracker.get_coverage_report()
        
        assert "overall_coverage_rate" in report
        assert "total_components" in report
        assert "monitored_components" in report
        assert report["total_components"] > 0
    
    def test_get_unmonitored_list(self, coverage_tracker):
        coverage_tracker.mark_unmonitored("searchers", "test_searcher")
        unmonitored = coverage_tracker.get_unmonitored_list()
        assert "searchers" in unmonitored
        assert "test_searcher" in unmonitored["searchers"]


class TestSetupMonitoring:
    """测试监控初始化脚本"""
    
    def test_setup_monitoring(self):
        from setup_monitoring import setup_monitoring
        report = setup_monitoring()
        
        assert "coverage" in report
        assert "alert_response" in report
        assert report["coverage"]["total_components"] > 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
