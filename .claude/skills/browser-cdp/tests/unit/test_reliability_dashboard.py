"""
监控面板单元测试
"""

import pytest
import json
from unittest.mock import MagicMock, patch
from pathlib import Path

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from src.reliability.dashboard import ReliabilityDashboard
from src.reliability.metrics import ReliabilityMetrics
from src.reliability.error import ErrorCategory


class TestReliabilityDashboard:
    """ReliabilityDashboard 测试"""
    
    def test_init_default(self):
        """测试默认初始化"""
        dashboard = ReliabilityDashboard()
        assert isinstance(dashboard.metrics, ReliabilityMetrics)
    
    def test_init_with_custom_metrics(self):
        """测试自定义指标初始化"""
        metrics = ReliabilityMetrics()
        dashboard = ReliabilityDashboard(metrics=metrics)
        assert dashboard.metrics is metrics
    
    def test_get_text_panel_empty(self):
        """测试空指标文本面板"""
        dashboard = ReliabilityDashboard()
        panel = dashboard.get_text_panel()
        
        assert "Browser-CDP 可靠性监控面板" in panel
        assert "重试统计" in panel
        assert "熔断器" in panel
        assert "连接状态" in panel
        assert "错误分类" in panel
    
    def test_get_text_panel_with_data(self):
        """测试带数据的文本面板"""
        metrics = ReliabilityMetrics()
        metrics.record_retry(success=True)
        metrics.record_retry(success=False)
        metrics.record_circuit_breaker_trip()
        metrics.record_connection_loss()
        metrics.record_connection_recovered()
        metrics.record_error(ErrorCategory.CONNECTION.value, "CDPConnectionLostError")
        metrics.record_wait_strategy("selector_visible", True)
        metrics.record_operation_duration("search", 1.5)
        
        dashboard = ReliabilityDashboard(metrics=metrics)
        panel = dashboard.get_text_panel()
        
        assert "总重试次数: 2" in panel
        assert "成功次数:   1" in panel
        assert "失败次数:   1" in panel
        assert "触发次数:   1" in panel
        assert "丢失次数:   1" in panel
        assert "恢复次数:   1" in panel
        assert "selector_visible" in panel
        assert "search" in panel
    
    def test_get_html_panel(self):
        """测试 HTML 面板生成"""
        dashboard = ReliabilityDashboard()
        html = dashboard.get_html_panel()
        
        assert "<!DOCTYPE html>" in html
        assert "Browser-CDP 可靠性监控面板" in html
        assert "重试统计" in html
        assert "熔断器" in html
        assert "连接状态" in html
    
    def test_get_html_panel_with_data(self):
        """测试带数据的 HTML 面板"""
        metrics = ReliabilityMetrics()
        metrics.record_retry(success=True)
        metrics.record_retry(success=False)
        metrics.record_circuit_breaker_trip()
        
        dashboard = ReliabilityDashboard(metrics=metrics)
        html = dashboard.get_html_panel()
        
        assert "总重试次数" in html
        assert "成功次数" in html
        assert "失败次数" in html
        assert "触发次数" in html
    
    def test_get_json_panel(self):
        """测试 JSON 面板生成"""
        dashboard = ReliabilityDashboard()
        json_str = dashboard.get_json_panel()
        
        data = json.loads(json_str)
        assert "retry" in data
        assert "circuit_breaker" in data
        assert "connection" in data
        assert "errors_by_category" in data
        assert "wait_strategies" in data
        assert "operation_durations" in data
    
    def test_get_json_panel_with_data(self):
        """测试带数据的 JSON 面板"""
        metrics = ReliabilityMetrics()
        metrics.record_retry(success=True)
        metrics.record_retry(success=False)
        metrics.record_circuit_breaker_trip()
        metrics.record_connection_loss()
        metrics.record_connection_recovered()
        metrics.record_error(ErrorCategory.CONNECTION.value, "CDPConnectionLostError")
        
        dashboard = ReliabilityDashboard(metrics=metrics)
        json_str = dashboard.get_json_panel()
        
        data = json.loads(json_str)
        assert data["retry"]["total"] == 2
        assert data["retry"]["success"] == 1
        assert data["retry"]["failure"] == 1
        assert data["circuit_breaker"]["trips"] == 1
        assert data["connection"]["losses"] == 1
        assert data["connection"]["recovered"] == 1
    
    def test_save_html(self, tmp_path):
        """测试保存 HTML 面板"""
        dashboard = ReliabilityDashboard()
        html_path = str(tmp_path / "dashboard.html")
        
        dashboard.save_html(html_path)
        
        assert Path(html_path).exists()
        with open(html_path, "r", encoding="utf-8") as f:
            content = f.read()
            assert "<!DOCTYPE html>" in content
            assert "Browser-CDP 可靠性监控面板" in content
    
    def test_print_panel_text(self, capsys):
        """测试打印文本面板"""
        dashboard = ReliabilityDashboard()
        dashboard.print_panel(format="text")
        
        captured = capsys.readouterr()
        assert "Browser-CDP 可靠性监控面板" in captured.out
    
    def test_print_panel_json(self, capsys):
        """测试打印 JSON 面板"""
        dashboard = ReliabilityDashboard()
        dashboard.print_panel(format="json")
        
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert "retry" in data
    
    def test_panel_with_all_metrics(self):
        """测试包含所有指标的面板"""
        metrics = ReliabilityMetrics()
        
        # 记录各种指标
        for _ in range(5):
            metrics.record_retry(success=True)
        for _ in range(2):
            metrics.record_retry(success=False)
        
        metrics.record_circuit_breaker_trip()
        metrics.record_circuit_breaker_trip()
        metrics.record_circuit_breaker_reset()
        
        metrics.record_connection_loss()
        metrics.record_connection_loss()
        metrics.record_connection_recovered()
        
        metrics.record_error(ErrorCategory.CONNECTION.value, "CDPConnectionLostError")
        metrics.record_error(ErrorCategory.TIMEOUT.value, "CDPCommandTimeoutError")
        metrics.record_error(ErrorCategory.ELEMENT.value, "ElementNotFoundError")
        
        metrics.record_wait_strategy("selector_visible", True)
        metrics.record_wait_strategy("selector_visible", False)
        metrics.record_wait_strategy("network_idle", True)
        
        metrics.record_operation_duration("search", 1.5)
        metrics.record_operation_duration("search", 2.0)
        metrics.record_operation_duration("navigate", 3.0)
        
        dashboard = ReliabilityDashboard(metrics=metrics)
        
        # 测试文本面板
        text_panel = dashboard.get_text_panel()
        assert "总重试次数: 7" in text_panel
        assert "成功率:     71.4%" in text_panel
        assert "触发次数:   2" in text_panel
        assert "丢失次数:   2" in text_panel
        assert "恢复率:     50.0%" in text_panel
        assert "selector_visible" in text_panel
        assert "network_idle" in text_panel
        assert "search" in text_panel
        assert "navigate" in text_panel
        
        # 测试 HTML 面板
        html_panel = dashboard.get_html_panel()
        assert "Browser-CDP 可靠性监控面板" in html_panel
        assert "重试统计" in html_panel
        
        # 测试 JSON 面板
        json_panel = dashboard.get_json_panel()
        data = json.loads(json_panel)
        assert data["retry"]["total"] == 7
        assert data["retry"]["success"] == 5
        assert data["retry"]["failure"] == 2
        assert data["circuit_breaker"]["trips"] == 2
        assert data["connection"]["losses"] == 2
        assert data["connection"]["recovered"] == 1
        assert data["errors_by_category"][ErrorCategory.CONNECTION.value] == 1
        assert data["errors_by_category"][ErrorCategory.TIMEOUT.value] == 1
        assert data["errors_by_category"][ErrorCategory.ELEMENT.value] == 1


class TestGetDashboard:
    """全局面板函数测试"""
    
    def test_get_dashboard_returns_instance(self):
        """测试 get_dashboard 返回实例"""
        from src.reliability.dashboard import get_dashboard
        
        dashboard = get_dashboard()
        assert isinstance(dashboard, ReliabilityDashboard)
    
    def test_get_dashboard_singleton(self):
        """测试 get_dashboard 单例"""
        from src.reliability.dashboard import get_dashboard
        
        d1 = get_dashboard()
        d2 = get_dashboard()
        assert d1 is d2
    
    def test_reset_dashboard(self):
        """测试 reset_dashboard"""
        from src.reliability.dashboard import get_dashboard, reset_dashboard
        
        d1 = get_dashboard()
        reset_dashboard()
        d2 = get_dashboard()
        
        assert d1 is not d2


class TestDashboardIntegration:
    """面板集成测试"""
    
    def test_dashboard_with_real_metrics(self):
        """测试使用真实指标的面板"""
        metrics = ReliabilityMetrics()
        
        # 模拟真实使用场景
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
        metrics.record_operation_duration("navigate", 3.0)
        
        dashboard = ReliabilityDashboard(metrics=metrics)
        
        # 验证所有面板格式都能正确生成
        text_panel = dashboard.get_text_panel()
        html_panel = dashboard.get_html_panel()
        json_panel = dashboard.get_json_panel()
        
        assert len(text_panel) > 100
        assert "<!DOCTYPE html>" in html_panel
        
        data = json.loads(json_panel)
        assert data["retry"]["total"] == 3
        assert data["retry"]["success"] == 2
        assert data["circuit_breaker"]["trips"] == 1
        assert data["connection"]["losses"] == 1
        assert data["connection"]["recovered"] == 1
