"""
评估器全面单元测试

测试覆盖所有评估器模块：
- data_quality_evaluator: DataQualityEvaluator, DataQualityMonitor
- element_evaluator: ElementEvaluator
- anti_detection_evaluator: AntiDetectionEvaluator
- stability_evaluator: StabilityEvaluator
- error_recovery_evaluator: ErrorRecoveryEvaluator
- performance_evaluator: PerformanceEvaluator
- report_generator: ReportGenerator
- website_evaluator: WebsiteEvaluator
"""
import pytest
import json
import tempfile
import os
from pathlib import Path
from unittest.mock import patch, Mock, MagicMock

# 添加 skill 目录到路径
SKILL_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(SKILL_DIR) not in __import__('sys').path:
    __import__('sys').path.insert(0, str(SKILL_DIR))

from src.evaluators.data_quality_evaluator import DataQualityEvaluator, DataQualityMonitor
from src.evaluators.element_evaluator import ElementEvaluator
from src.evaluators.anti_detection_evaluator import AntiDetectionEvaluator
from src.evaluators.stability_evaluator import StabilityEvaluator
from src.evaluators.error_recovery_evaluator import ErrorRecoveryEvaluator
from src.evaluators.performance_evaluator import PerformanceEvaluator
from src.evaluators.report_generator import ReportGenerator
from src.evaluators.website_evaluator import WebsiteEvaluator
from src.evaluators.base_evaluator import MetricResult, DimensionResult


class TestDataQualityEvaluator:
    """数据质量评估器测试"""

    def test_evaluate_high_quality(self):
        """测试高质量数据评估"""
        evaluator = DataQualityEvaluator()
        context = {
            "total_records": 100,
            "complete_records": 95,
            "valid_records": 90,
            "fresh_records": 85,
            "consistent_records": 92,
            "field_completeness": {"title": 0.95, "price": 0.90, "url": 1.0},
            "data_age_hours": 2.0,
            "freshness_threshold_hours": 24.0,
        }
        result = evaluator.evaluate(context)

        assert result["score"] > 80
        assert result["name"] == "数据质量"
        assert len(result["metrics"]) > 0

    def test_evaluate_low_quality(self):
        """测试低质量数据评估"""
        evaluator = DataQualityEvaluator()
        context = {
            "total_records": 100,
            "complete_records": 50,
            "valid_records": 40,
            "fresh_records": 30,
            "consistent_records": 45,
            "field_completeness": {"title": 0.50, "price": 0.40, "url": 0.60},
            "data_age_hours": 48.0,
            "freshness_threshold_hours": 24.0,
        }
        result = evaluator.evaluate(context)

        assert result["score"] < 60

    def test_evaluate_zero_records(self):
        """测试零记录情况"""
        evaluator = DataQualityEvaluator()
        context = {
            "total_records": 0,
            "complete_records": 0,
            "valid_records": 0,
            "fresh_records": 0,
            "consistent_records": 0,
            "field_completeness": {},
            "data_age_hours": 0.0,
            "freshness_threshold_hours": 24.0,
        }
        result = evaluator.evaluate(context)

        assert result["score"] == 20.0  # 新鲜度100%贡献20分

    def test_evaluate_freshness_scoring(self):
        """测试新鲜度评分逻辑"""
        evaluator = DataQualityEvaluator()
        
        # 非常新鲜的数据
        context_fresh = {
            "total_records": 100,
            "complete_records": 100,
            "valid_records": 100,
            "fresh_records": 100,
            "consistent_records": 100,
            "field_completeness": {"title": 1.0},
            "data_age_hours": 1.0,
            "freshness_threshold_hours": 24.0,
        }
        result_fresh = evaluator.evaluate(context_fresh)
        
        # 过期的数据
        context_stale = {
            "total_records": 100,
            "complete_records": 100,
            "valid_records": 100,
            "fresh_records": 100,
            "consistent_records": 100,
            "field_completeness": {"title": 1.0},
            "data_age_hours": 48.0,
            "freshness_threshold_hours": 24.0,
        }
        result_stale = evaluator.evaluate(context_stale)
        
        assert result_fresh["score"] > result_stale["score"]


class TestDataQualityMonitor:
    """数据质量监控器测试"""

    def test_record_quality(self):
        """测试记录质量数据"""
        monitor = DataQualityMonitor(freshness_threshold_hours=24.0)
        
        monitor.record_quality(
            source="test_source",
            quality_data={
                "completeness_rate": 90.0,
                "validity_rate": 85.0,
                "freshness_score": 95.0,
            }
        )
        
        assert len(monitor._history) == 1
        assert monitor._history[0]["source"] == "test_source"

    def test_alert_triggering(self):
        """测试告警触发"""
        monitor = DataQualityMonitor(freshness_threshold_hours=24.0)
        
        # 触发完整性告警
        monitor.record_quality(
            source="test_source",
            quality_data={
                "completeness_rate": 50.0,  # 低于70%阈值
                "validity_rate": 90.0,
                "freshness_score": 90.0,
            }
        )
        
        alerts = monitor.get_alerts()
        assert len(alerts) > 0
        assert alerts[0]["type"] == "completeness_low"

    def test_no_alert_for_good_data(self):
        """测试良好数据不触发告警"""
        monitor = DataQualityMonitor(freshness_threshold_hours=24.0)
        
        monitor.record_quality(
            source="test_source",
            quality_data={
                "completeness_rate": 95.0,
                "validity_rate": 95.0,
                "freshness_score": 95.0,
            }
        )
        
        alerts = monitor.get_alerts()
        assert len(alerts) == 0


class TestElementEvaluator:
    """元素定位评估器测试"""

    def test_evaluate_high_accuracy(self):
        """测试高准确率场景"""
        evaluator = ElementEvaluator()
        context = {
            "total_location_attempts": 100,
            "successful_locations": 95,
            "total_interactions": 80,
            "successful_interactions": 75,
            "total_dynamic_elements": 20,
            "identified_dynamic_elements": 18,
            "strategies_used": ["id", "class", "xpath"],
            "available_strategies": ["id", "class", "xpath", "css", "name"],
        }
        result = evaluator.evaluate(context)

        assert result["score"] > 80
        assert result["name"] == "元素定位准确率"

    def test_evaluate_low_accuracy(self):
        """测试低准确率场景"""
        evaluator = ElementEvaluator()
        context = {
            "total_location_attempts": 100,
            "successful_locations": 50,
            "total_interactions": 50,
            "successful_interactions": 25,
            "total_dynamic_elements": 20,
            "identified_dynamic_elements": 5,
            "strategies_used": ["id"],
            "available_strategies": ["id", "class", "xpath", "css", "name"],
        }
        result = evaluator.evaluate(context)

        assert result["score"] < 60

    def test_evaluate_zero_attempts(self):
        """测试零尝试情况"""
        evaluator = ElementEvaluator()
        context = {
            "total_location_attempts": 0,
            "successful_locations": 0,
            "total_interactions": 0,
            "successful_interactions": 0,
            "total_dynamic_elements": 0,
            "identified_dynamic_elements": 0,
            "strategies_used": [],
            "available_strategies": ["id", "class", "xpath"],
        }
        result = evaluator.evaluate(context)

        assert result["score"] == 0.0


class TestAntiDetectionEvaluator:
    """反检测评估器测试"""

    def test_evaluate_good_evasion(self):
        """测试良好反检测"""
        evaluator = AntiDetectionEvaluator()
        context = {
            "anti_crawl_triggered": 10,
            "anti_crawl_bypassed": 8,
            "captcha_triggered": 5,
            "captcha_passed": 4,
            "fingerprint_detected": 2,
            "total_checks": 20,
            "human_like_score": 85,
        }
        result = evaluator.evaluate(context)

        assert result["score"] > 70

    def test_evaluate_poor_evasion(self):
        """测试差反检测"""
        evaluator = AntiDetectionEvaluator()
        context = {
            "anti_crawl_triggered": 10,
            "anti_crawl_bypassed": 3,
            "captcha_triggered": 5,
            "captcha_passed": 1,
            "fingerprint_detected": 15,
            "total_checks": 20,
            "human_like_score": 40,
        }
        result = evaluator.evaluate(context)

        assert result["score"] < 50

    def test_evaluate_no_triggers(self):
        """测试无触发场景"""
        evaluator = AntiDetectionEvaluator()
        context = {
            "anti_crawl_triggered": 0,
            "anti_crawl_bypassed": 0,
            "captcha_triggered": 0,
            "captcha_passed": 0,
            "fingerprint_detected": 0,
            "total_checks": 0,
            "human_like_score": 100,
        }
        result = evaluator.evaluate(context)

        # 无触发时，human_like_score=100贡献20分，其他为0
        assert result["score"] == 20.0


class TestStabilityEvaluator:
    """稳定性评估器测试"""

    def test_evaluate_stable(self):
        """测试稳定场景"""
        evaluator = StabilityEvaluator()
        context = {
            "total_runs": 100,
            "consistent_runs": 95,
            "total_errors": 5,
            "recovered_errors": 4,
            "memory_growth_mb_per_hour": 2.0,
            "total_connection_time": 3600,
            "disconnected_time": 30,
        }
        result = evaluator.evaluate(context)

        assert result["score"] > 80

    def test_evaluate_unstable(self):
        """测试不稳定场景"""
        evaluator = StabilityEvaluator()
        context = {
            "total_runs": 100,
            "consistent_runs": 70,
            "total_errors": 20,
            "recovered_errors": 10,
            "memory_growth_mb_per_hour": 15.0,
            "total_connection_time": 3600,
            "disconnected_time": 600,
        }
        result = evaluator.evaluate(context)

        assert result["score"] < 60

    def test_evaluate_memory_leak(self):
        """测试内存泄漏检测"""
        evaluator = StabilityEvaluator()
        context = {
            "total_runs": 50,
            "consistent_runs": 45,
            "total_errors": 5,
            "recovered_errors": 5,
            "memory_growth_mb_per_hour": 50.0,  # 高内存增长
            "total_connection_time": 3600,
            "disconnected_time": 0,
        }
        result = evaluator.evaluate(context)

        # 内存增长50MB/h，内存稳定性得分为0，总分72
        assert result["score"] == 72.0


class TestErrorRecoveryEvaluator:
    """错误恢复评估器测试"""

    def test_evaluate_good_recovery(self):
        """测试良好恢复"""
        evaluator = ErrorRecoveryEvaluator()
        context = {
            "total_errors": 10,
            "correctly_classified": 9,
            "total_retries": 15,
            "successful_retries": 12,
            "total_fallbacks": 5,
            "successful_fallbacks": 4,
        }
        result = evaluator.evaluate(context)

        assert result["score"] > 70

    def test_evaluate_zero_errors(self):
        """测试零错误场景"""
        evaluator = ErrorRecoveryEvaluator()
        context = {
            "total_errors": 0,
            "correctly_classified": 0,
            "total_retries": 0,
            "successful_retries": 0,
            "total_fallbacks": 0,
            "successful_fallbacks": 0,
        }
        result = evaluator.evaluate(context)

        assert result["score"] == 100.0

    def test_evaluate_poor_recovery(self):
        """测试差恢复"""
        evaluator = ErrorRecoveryEvaluator()
        context = {
            "total_errors": 20,
            "correctly_classified": 10,
            "total_retries": 25,
            "successful_retries": 10,
            "total_fallbacks": 10,
            "successful_fallbacks": 3,
        }
        result = evaluator.evaluate(context)

        assert result["score"] < 50


class TestPerformanceEvaluator:
    """性能评估器测试"""

    def test_evaluate_fast_page(self):
        """测试快速页面"""
        evaluator = PerformanceEvaluator()
        context = {
            "first_paint_time": 1.5,
            "full_load_time": 3.0,
            "element_wait_time": 0.5,
            "total_time": 5.0,
            "operation_count": 5,
        }
        result = evaluator.evaluate(context)

        assert result["score"] > 40

    def test_evaluate_slow_page(self):
        """测试慢速页面"""
        evaluator = PerformanceEvaluator()
        context = {
            "first_paint_time": 8.0,
            "full_load_time": 20.0,
            "element_wait_time": 5.0,
            "total_time": 35.0,
            "operation_count": 5,
        }
        result = evaluator.evaluate(context)

        assert result["score"] < 50

    def test_evaluate_extreme_slow(self):
        """测试极端慢速页面"""
        evaluator = PerformanceEvaluator()
        context = {
            "first_paint_time": 15.0,
            "full_load_time": 30.0,
            "element_wait_time": 10.0,
            "total_time": 55.0,
            "operation_count": 10,
        }
        result = evaluator.evaluate(context)

        # 55s总耗时，得分约47
        assert result["score"] < 50


class TestReportGenerator:
    """报告生成器测试"""

    def test_calculate_overall_score(self):
        """测试综合得分计算"""
        generator = ReportGenerator()
        generator.add_dimension("维度A", {"score": 90, "weight": 0.5})
        generator.add_dimension("维度B", {"score": 70, "weight": 0.5})

        score = generator.calculate_overall_score()
        assert score == 80.0

    def test_generate_markdown_report(self):
        """测试Markdown报告生成"""
        generator = ReportGenerator()
        generator.add_dimension("抓取成功率", {
            "score": 85.0,
            "weight": 0.30,
            "weighted_score": 25.5,
            "metrics": [],
            "observations": ["测试观察"]
        })

        markdown = generator.generate_markdown_report()
        assert "网站操作能力评估报告" in markdown
        assert "85.0" in markdown

    def test_save_report_json(self, tmp_path):
        """测试JSON报告保存"""
        generator = ReportGenerator()
        generator.add_dimension("测试维度", {"score": 90.0, "weight": 1.0})

        output_file = tmp_path / "report.json"
        generator.save_report(str(output_file))

        assert output_file.exists()
        with open(output_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        assert "dimensions" in data

    def test_save_report_markdown(self, tmp_path):
        """测试Markdown报告保存"""
        generator = ReportGenerator()
        generator.add_dimension("测试维度", {"score": 90.0, "weight": 1.0})

        output_file = tmp_path / "report.json"
        generator.save_report(str(output_file))

        assert output_file.exists()
        content = output_file.read_text(encoding='utf-8')
        assert "测试维度" in content


class TestWebsiteEvaluator:
    """网站评估器测试"""

    def test_evaluate_comprehensive(self):
        """测试综合评估"""
        evaluator = WebsiteEvaluator(website_url="https://example.com")
        context = {
            "url": "https://example.com",
            "total_attempts": 100,
            "successful_accesses": 95,
            "total_data_items": 500,
            "correct_extractions": 450,
            "first_paint_time": 2.0,
            "full_load_time": 4.0,
            "anti_crawl_triggered": 5,
            "anti_crawl_bypassed": 4,
            "total_errors": 3,
            "recovered_errors": 3,
        }
        result = evaluator.evaluate(context)

        assert "overall_score" in result
        assert "dimensions" in result
        assert len(result["dimensions"]) > 0

    def test_evaluate_with_missing_data(self):
        """测试缺失数据场景"""
        evaluator = WebsiteEvaluator(website_url="https://example.com")
        context = {
            "url": "https://example.com",
            # 缺少部分字段
        }
        result = evaluator.evaluate(context)

        # 应该返回默认值而不是报错
        assert "overall_score" in result

    def test_evaluate_single_dimension(self):
        """测试单维度评估"""
        evaluator = WebsiteEvaluator(website_url="https://example.com")
        context = {
            "url": "https://example.com",
            "total_attempts": 100,
            "successful_accesses": 95,
        }
        result = evaluator.evaluate(context)

        assert "overall_score" in result
        assert "dimensions" in result


class TestMetricResult:
    """MetricResult 数据类测试"""

    def test_weighted_score(self):
        """测试加权得分计算"""
        metric = MetricResult(
            name="test",
            value=80.0,
            unit="分",
            weight=0.5
        )
        assert metric.weighted_score == 40.0

    def test_to_dict(self):
        """测试字典转换"""
        metric = MetricResult(
            name="test",
            value=80.0,
            unit="分",
            target=90.0,
            weight=0.5,
            details={"extra": "info"}
        )
        d = metric.to_dict()
        
        assert d["name"] == "test"
        assert d["value"] == 80.0
        assert d["unit"] == "分"
        assert d["target"] == 90.0
        assert d["weight"] == 0.5
        assert d["details"] == {"extra": "info"}

    def test_to_dict_without_optional(self):
        """测试不含可选字段的字典转换"""
        metric = MetricResult(
            name="test",
            value=80.0,
            unit="分"
        )
        d = metric.to_dict()
        
        assert "target" not in d
        assert "details" not in d


class TestDimensionResult:
    """DimensionResult 数据类测试"""

    def test_weighted_score(self):
        """测试加权得分计算"""
        dim = DimensionResult(
            name="测试维度",
            score=85.0,
            weight=0.3
        )
        assert dim.weighted_score == 25.5

    def test_to_dict(self):
        """测试字典转换"""
        dim = DimensionResult(
            name="测试维度",
            score=85.0,
            weight=0.3,
            metrics=[MetricResult(name="m1", value=90.0)],
            observations=["观察1"]
        )
        d = dim.to_dict()
        
        assert d["name"] == "测试维度"
        assert d["score"] == 85.0
        assert d["weight"] == 0.3
        assert len(d["metrics"]) == 1
        assert d["observations"] == ["观察1"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
