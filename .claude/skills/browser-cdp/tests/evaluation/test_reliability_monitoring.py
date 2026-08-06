"""
可靠性监控体系测试

测试告警系统、日志查询、监控面板等功能。
"""

import json
import time
import pytest
from datetime import datetime, timedelta
from pathlib import Path

from src.reliability.metrics import ReliabilityMetrics
from src.reliability.alert import (
    AlertRule,
    AlertSeverity,
    AlertManager,
    AlertNotification,
    get_alert_manager,
    reset_alert_manager,
)
from src.reliability.log_query import LogQuery, get_log_query, reset_log_query
from src.reliability.dashboard import ReliabilityDashboard, get_dashboard, reset_dashboard
from src.reliability.logging import OperationLogger


class TestAlertRule:
    """告警规则测试"""
    
    def test_should_trigger_gt(self):
        rule = AlertRule(
            rule_id="test_gt",
            name="测试大于",
            condition="gt",
            threshold=10.0,
            severity=AlertSeverity.WARNING,
        )
        assert rule.should_trigger(15.0) is True
        assert rule.should_trigger(10.0) is False
        assert rule.should_trigger(5.0) is False
    
    def test_should_trigger_lt(self):
        rule = AlertRule(
            rule_id="test_lt",
            name="测试小于",
            condition="lt",
            threshold=10.0,
            severity=AlertSeverity.WARNING,
        )
        assert rule.should_trigger(5.0) is True
        assert rule.should_trigger(10.0) is False
        assert rule.should_trigger(15.0) is False
    
    def test_cooldown(self):
        rule = AlertRule(
            rule_id="test_cooldown",
            name="测试冷却",
            condition="gt",
            threshold=10.0,
            severity=AlertSeverity.WARNING,
            cooldown_seconds=60.0,
        )
        # 第一次触发
        assert rule.should_trigger(15.0) is True
        rule.trigger()
        # 冷却期内不应再次触发
        assert rule.should_trigger(15.0) is False
    
    def test_disabled_rule(self):
        rule = AlertRule(
            rule_id="test_disabled",
            name="测试禁用",
            condition="gt",
            threshold=10.0,
            severity=AlertSeverity.WARNING,
            enabled=False,
        )
        assert rule.should_trigger(15.0) is False
    
    def test_to_dict(self):
        rule = AlertRule(
            rule_id="test_dict",
            name="测试序列化",
            condition="gt",
            threshold=10.0,
            severity=AlertSeverity.ERROR,
        )
        d = rule.to_dict()
        assert d["rule_id"] == "test_dict"
        assert d["threshold"] == 10.0
        assert d["severity"] == "error"


class TestAlertManager:
    """告警管理器测试"""
    
    def setup_method(self):
        reset_alert_manager()
    
    def teardown_method(self):
        reset_alert_manager()
    
    def test_default_rules(self):
        manager = get_alert_manager()
        rules = manager.get_rules()
        assert len(rules) > 0
        assert "retry_failure_rate" in rules
    
    def test_add_and_remove_rule(self):
        manager = get_alert_manager()
        rule = AlertRule(
            rule_id="custom_rule",
            name="自定义规则",
            condition="gt",
            threshold=100.0,
            severity=AlertSeverity.CRITICAL,
        )
        manager.add_rule(rule)
        assert "custom_rule" in manager.get_rules()
        
        manager.remove_rule("custom_rule")
        assert "custom_rule" not in manager.get_rules()
    
    def test_check_alerts_no_trigger(self):
        manager = get_alert_manager()
        alerts = manager.check_alerts()
        # 初始状态不应触发告警
        assert len(alerts) == 0
    
    def test_check_alerts_with_metrics(self):
        metrics = ReliabilityMetrics()
        # 模拟高重试失败率
        for _ in range(10):
            metrics.record_retry(success=False)
        
        manager = AlertManager(metrics=metrics)
        alerts = manager.check_alerts()
        
        # 重试失败率 100% 应触发告警
        assert len(alerts) > 0
        assert any(a["rule_id"] == "retry_failure_rate" for a in alerts)
    
    def test_alert_history(self):
        metrics = ReliabilityMetrics()
        manager = AlertManager(metrics=metrics)
        
        # 触发告警
        for _ in range(10):
            metrics.record_retry(success=False)
        manager.check_alerts()
        
        # 检查历史
        history = manager.get_alert_history()
        assert len(history) > 0
    
    def test_alert_stats(self):
        metrics = ReliabilityMetrics()
        manager = AlertManager(metrics=metrics)
        
        # 触发多个告警
        for _ in range(5):
            metrics.record_retry(success=False)
        manager.check_alerts()
        
        stats = manager.get_alert_stats()
        assert stats["total_alerts"] > 0
        assert "by_severity" in stats
        assert "by_rule" in stats


class TestLogQuery:
    """日志查询测试"""
    
    def setup_method(self):
        reset_log_query()
    
    def teardown_method(self):
        reset_log_query()
    
    def test_query_empty(self):
        query = get_log_query()
        results = query.query(limit=10)
        assert isinstance(results, list)
    
    def test_query_with_time_range(self):
        query = get_log_query()
        start_time = datetime.now() - timedelta(hours=1)
        end_time = datetime.now()
        
        results = query.query(
            start_time=start_time,
            end_time=end_time,
            limit=10,
        )
        assert isinstance(results, list)
    
    def test_aggregate(self):
        query = get_log_query()
        stats = query.aggregate(group_by="hour")
        assert "total_entries" in stats
        assert "by_level" in stats
        assert "time_series" in stats
    
    def test_get_error_summary(self):
        query = get_log_query()
        summary = query.get_error_summary(hours=24, limit=50)
        assert "total_errors" in summary
        assert "errors" in summary
        assert "by_type" in summary
    
    def test_get_operation_stats(self):
        query = get_log_query()
        stats = query.get_operation_stats(hours=24)
        assert "total_operations" in stats
        assert "success_count" in stats
        assert "failure_count" in stats


class TestDashboard:
    """监控面板测试"""
    
    def setup_method(self):
        reset_dashboard()
        reset_alert_manager()
    
    def teardown_method(self):
        reset_dashboard()
        reset_alert_manager()
    
    def test_text_panel(self):
        dashboard = ReliabilityDashboard()
        panel = dashboard.get_text_panel()
        assert "Browser-CDP" in panel
        assert "重试统计" in panel
        assert "告警状态" in panel
    
    def test_html_panel(self):
        dashboard = ReliabilityDashboard()
        html = dashboard.get_html_panel()
        assert "<!DOCTYPE html>" in html
        assert "Browser-CDP" in html
    
    def test_json_panel(self):
        dashboard = ReliabilityDashboard()
        json_str = dashboard.get_json_panel()
        data = json.loads(json_str)
        assert "retry" in data
        assert "connection" in data
    
    def test_panel_with_alert_manager(self):
        metrics = ReliabilityMetrics()
        alert_manager = AlertManager(metrics=metrics)
        dashboard = ReliabilityDashboard(
            metrics=metrics,
            alert_manager=alert_manager,
        )
        
        panel = dashboard.get_text_panel()
        assert "告警状态" in panel
    
    def test_save_html(self, tmp_path):
        dashboard = ReliabilityDashboard()
        output_file = tmp_path / "dashboard.html"
        dashboard.save_html(str(output_file))
        assert output_file.exists()
        content = output_file.read_text(encoding="utf-8")
        assert "<!DOCTYPE html>" in content


class TestIntegration:
    """集成测试"""
    
    def test_full_workflow(self):
        """测试完整的监控工作流"""
        # 1. 创建指标收集器
        metrics = ReliabilityMetrics()
        
        # 2. 模拟一些操作
        for i in range(5):
            metrics.record_operation_duration("search", 2.5)
        
        for i in range(3):
            metrics.record_retry(success=True)
        
        for i in range(2):
            metrics.record_retry(success=False)
        
        metrics.record_error("timeout", "CDPCommandTimeoutError", "Test error")
        
        # 3. 创建告警管理器
        alert_manager = AlertManager(metrics=metrics)
        
        # 4. 检查告警
        alerts = alert_manager.check_alerts()
        
        # 5. 创建面板
        dashboard = ReliabilityDashboard(
            metrics=metrics,
            alert_manager=alert_manager,
        )
        
        # 6. 验证面板输出
        text_panel = dashboard.get_text_panel()
        assert len(text_panel) > 0
        
        html_panel = dashboard.get_html_panel()
        assert len(html_panel) > 0
        
        json_panel = dashboard.get_json_panel()
        data = json.loads(json_panel)
        assert data["retry"]["total"] == 5


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
