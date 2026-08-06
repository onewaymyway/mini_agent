"""
评估器单元测试

测试所有评估器的功能正确性。
"""

import pytest
import json
from pathlib import Path

from .success_rate_evaluator import SuccessRateEvaluator
from .performance_evaluator import PerformanceEvaluator
from .element_evaluator import ElementEvaluator
from .anti_detection_evaluator import AntiDetectionEvaluator
from .stability_evaluator import StabilityEvaluator
from .error_recovery_evaluator import ErrorRecoveryEvaluator
from .report_generator import ReportGenerator
from .website_evaluator import WebsiteEvaluator


class TestSuccessRateEvaluator:
    """抓取成功率评估器测试"""

    def test_evaluate_normal_case(self):
        evaluator = SuccessRateEvaluator()
        context = {
            "total_attempts": 100,
            "successful_accesses": 95,
            "total_data_items": 500,
            "correct_extractions": 450,
            "expected_fields": 10,
            "extracted_fields": 8,
        }
        result = evaluator.evaluate(context)

        assert result["score"] > 0
        assert result["name"] == "抓取成功率"
        assert len(result["metrics"]) > 0

    def test_evaluate_zero_attempts(self):
        evaluator = SuccessRateEvaluator()
        context = {
            "total_attempts": 0,
            "successful_accesses": 0,
            "total_data_items": 0,
            "correct_extractions": 0,
            "expected_fields": 0,
            "extracted_fields": 0,
        }
        result = evaluator.evaluate(context)

        # 应该返回 0 分而不是 NaN
        assert result["score"] == 0.0


class TestPerformanceEvaluator:
    """性能评估器测试"""

    def test_evaluate_fast_page(self):
        evaluator = PerformanceEvaluator()
        context = {
            "first_paint_time": 1.5,
            "full_load_time": 3.0,
            "element_wait_time": 0.5,
            "total_time": 5.0,
            "operation_count": 5,
        }
        result = evaluator.evaluate(context)

        # 快速页面应该得分较高（调整阈值）
        assert result["score"] > 40

    def test_evaluate_slow_page(self):
        evaluator = PerformanceEvaluator()
        context = {
            "first_paint_time": 8.0,
            "full_load_time": 20.0,
            "element_wait_time": 5.0,
            "total_time": 35.0,
            "operation_count": 5,
        }
        result = evaluator.evaluate(context)

        assert result["score"] < 50  # 慢速页面应该得分较低


class TestElementEvaluator:
    """元素定位评估器测试"""

    def test_evaluate_high_accuracy(self):
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

    def test_evaluate_low_accuracy(self):
        evaluator = ElementEvaluator()
        context = {
            "total_location_attempts": 100,
            "successful_locations": 60,
            "total_interactions": 50,
            "successful_interactions": 30,
            "total_dynamic_elements": 20,
            "identified_dynamic_elements": 10,
            "strategies_used": ["id"],
            "available_strategies": ["id", "class", "xpath", "css", "name"],
        }
        result = evaluator.evaluate(context)

        assert result["score"] < 60


class TestAntiDetectionEvaluator:
    """反检测评估器测试"""

    def test_evaluate_good_evasion(self):
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


class TestStabilityEvaluator:
    """稳定性评估器测试"""

    def test_evaluate_stable(self):
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


class TestErrorRecoveryEvaluator:
    """错误恢复评估器测试"""

    def test_evaluate_good_recovery(self):
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

        # 没有错误时应该得满分
        assert result["score"] == 100.0


class TestReportGenerator:
    """报告生成器测试"""

    def test_calculate_overall_score(self):
        generator = ReportGenerator()
        generator.add_dimension("维度A", {"score": 90, "weight": 0.5})
        generator.add_dimension("维度B", {"score": 70, "weight": 0.5})

        score = generator.calculate_overall_score()
        assert score == 80.0

    def test_generate_markdown_report(self):
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
        generator = ReportGenerator()
        generator.add_dimension("测试维度", {
            "score": 80.0,
            "weight": 1.0,
            "weighted_score": 80.0,
            "metrics": [],
            "observations": []
        })

        filepath = tmp_path / "test_report.json"
        generator.save_report(str(filepath), format="json")

        assert filepath.exists()
        with open(filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)
        assert data["overall_score"] == 80.0


class TestWebsiteEvaluator:
    """网站评估器集成测试"""

    def test_evaluate_complete(self):
        evaluator = WebsiteEvaluator("https://example.com")
        context = {
            "scraping_success": {
                "total_attempts": 100,
                "successful_accesses": 95,
                "total_data_items": 500,
                "correct_extractions": 450,
                "expected_fields": 10,
                "extracted_fields": 8,
            },
            "performance": {
                "first_paint_time": 2.0,
                "full_load_time": 5.0,
                "element_wait_time": 1.0,
                "total_time": 10.0,
                "operation_count": 10,
            },
            "element_accuracy": {
                "total_location_attempts": 100,
                "successful_locations": 95,
                "total_interactions": 80,
                "successful_interactions": 75,
                "total_dynamic_elements": 20,
                "identified_dynamic_elements": 18,
                "strategies_used": ["id", "class"],
                "available_strategies": ["id", "class", "xpath"],
            },
            "anti_detection": {
                "anti_crawl_triggered": 5,
                "anti_crawl_bypassed": 4,
                "captcha_triggered": 2,
                "captcha_passed": 1,
                "fingerprint_detected": 1,
                "total_checks": 10,
                "human_like_score": 80,
            },
            "stability": {
                "total_runs": 50,
                "consistent_runs": 48,
                "total_errors": 3,
                "recovered_errors": 3,
                "memory_growth_mb_per_hour": 1.0,
                "total_connection_time": 1800,
                "disconnected_time": 10,
            },
            "error_recovery": {
                "total_errors": 3,
                "correctly_classified": 3,
                "total_retries": 5,
                "successful_retries": 4,
                "total_fallbacks": 2,
                "successful_fallbacks": 2,
            },
        }

        report = evaluator.evaluate(context)

        assert report["website_url"] == "https://example.com"
        assert report["website_name"] == "example.com"
        assert report["overall_score"] > 0
        assert "dimensions" in report
        assert len(report["dimensions"]) == 6

    def test_get_markdown_report(self):
        evaluator = WebsiteEvaluator("https://test.com")
        context = {
            "scraping_success": {
                "total_attempts": 10,
                "successful_accesses": 10,
                "total_data_items": 50,
                "correct_extractions": 50,
                "expected_fields": 5,
                "extracted_fields": 5,
            },
            "performance": {
                "first_paint_time": 1.0,
                "full_load_time": 2.0,
                "element_wait_time": 0.5,
                "total_time": 5.0,
                "operation_count": 5,
            },
            "element_accuracy": {
                "total_location_attempts": 10,
                "successful_locations": 10,
                "total_interactions": 10,
                "successful_interactions": 10,
                "total_dynamic_elements": 5,
                "identified_dynamic_elements": 5,
                "strategies_used": ["id"],
                "available_strategies": ["id", "class"],
            },
            "anti_detection": {
                "anti_crawl_triggered": 0,
                "anti_crawl_bypassed": 0,
                "captcha_triggered": 0,
                "captcha_passed": 0,
                "fingerprint_detected": 0,
                "total_checks": 0,
                "human_like_score": 90,
            },
            "stability": {
                "total_runs": 10,
                "consistent_runs": 10,
                "total_errors": 0,
                "recovered_errors": 0,
                "memory_growth_mb_per_hour": 0.5,
                "total_connection_time": 600,
                "disconnected_time": 0,
            },
            "error_recovery": {
                "total_errors": 0,
                "correctly_classified": 0,
                "total_retries": 0,
                "successful_retries": 0,
                "total_fallbacks": 0,
                "successful_fallbacks": 0,
            },
        }

        evaluator.evaluate(context)
        markdown = evaluator.get_markdown_report()

        assert "网站操作能力评估报告" in markdown
        assert "综合评分" in markdown


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
