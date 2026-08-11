#!/usr/bin/env python3
"""
评估工具测试用例
"""

import pytest
import asyncio
from pathlib import Path

# 添加 scripts 目录到路径
import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from eval_availability import AvailabilityEvaluator
from eval_performance import PerformanceEvaluator
from eval_security import SecurityEvaluator
from eval_compatibility import CompatibilityEvaluator
from eval_stability import StabilityEvaluator
from eval_scalability import ScalabilityEvaluator
from eval_orchestrator import EvalOrchestrator


class TestAvailabilityEvaluator:
    """可用性评估器测试"""
    
    @pytest.mark.asyncio
    async def test_evaluate(self):
        evaluator = AvailabilityEvaluator("https://example.com")
        metrics = await evaluator.evaluate()
        
        assert metrics.page_access_rate >= 0
        assert metrics.avg_response_time >= 0
        assert metrics.error_rate >= 0
        assert metrics.feature_coverage >= 0
        assert metrics.score >= 0
    
    def test_get_report(self):
        evaluator = AvailabilityEvaluator("https://example.com")
        report = evaluator.get_report()
        assert "可用性评估报告" in report
        assert "页面访问成功率" in report


class TestPerformanceEvaluator:
    """性能评估器测试"""
    
    @pytest.mark.asyncio
    async def test_evaluate(self):
        evaluator = PerformanceEvaluator("https://example.com")
        metrics = await evaluator.evaluate()
        
        assert metrics.first_contentful_paint >= 0
        assert metrics.page_load_time >= 0
        assert metrics.concurrent_capacity >= 0
        assert metrics.memory_efficiency >= 0
        assert metrics.score >= 0


class TestSecurityEvaluator:
    """安全性评估器测试"""
    
    @pytest.mark.asyncio
    async def test_evaluate(self):
        evaluator = SecurityEvaluator("https://example.com")
        metrics = await evaluator.evaluate()
        
        assert metrics.anti_crawl_bypass_rate >= 0
        assert metrics.captcha_pass_rate >= 0
        assert metrics.fingerprint_evasion_rate >= 0
        assert metrics.data_protection_compliance >= 0
        assert metrics.score >= 0


class TestCompatibilityEvaluator:
    """兼容性评估器测试"""
    
    @pytest.mark.asyncio
    async def test_evaluate(self):
        evaluator = CompatibilityEvaluator("https://example.com")
        metrics = await evaluator.evaluate()
        
        assert metrics.browser_compatibility >= 0
        assert metrics.device_adaptation_rate >= 0
        assert metrics.version_stability >= 0
        assert metrics.cross_platform_consistency >= 0
        assert metrics.score >= 0


class TestStabilityEvaluator:
    """稳定性评估器测试"""
    
    @pytest.mark.asyncio
    async def test_evaluate(self):
        evaluator = StabilityEvaluator("https://example.com")
        metrics = await evaluator.evaluate()
        
        assert metrics.execution_consistency >= 0
        assert metrics.error_recovery_rate >= 0
        assert metrics.connection_stability >= 0
        assert metrics.crash_rate >= 0
        assert metrics.score >= 0


class TestScalabilityEvaluator:
    """可扩展性评估器测试"""
    
    @pytest.mark.asyncio
    async def test_evaluate(self):
        evaluator = ScalabilityEvaluator("https://example.com")
        metrics = await evaluator.evaluate()
        
        assert metrics.new_site_onboarding_time >= 0
        assert metrics.extension_cost >= 0
        assert metrics.maintenance_complexity >= 0
        assert metrics.documentation_completeness >= 0
        assert metrics.score >= 0


class TestOrchestrator:
    """编排器测试"""
    
    @pytest.mark.asyncio
    async def test_evaluate_all(self, tmp_path):
        evaluator = EvalOrchestrator("https://example.com", str(tmp_path))
        results = await evaluator.evaluate_all()
        
        assert "availability" in results
        assert "performance" in results
        assert "security" in results
        assert "compatibility" in results
        assert "stability" in results
        assert "scalability" in results
        assert "overall_score" in results
        assert results["overall_score"] >= 0
    
    def test_get_grade(self):
        evaluator = EvalOrchestrator("https://example.com")
        evaluator.results = {"overall_score": 95}
        assert evaluator.get_grade() == "A (优秀)"
        
        evaluator.results = {"overall_score": 85}
        assert evaluator.get_grade() == "B (良好)"
        
        evaluator.results = {"overall_score": 70}
        assert evaluator.get_grade() == "C (合格)"
        
        evaluator.results = {"overall_score": 50}
        assert evaluator.get_grade() == "D (待改进)"
        
        evaluator.results = {"overall_score": 30}
        assert evaluator.get_grade() == "F (不可用)"
    
    def test_generate_report(self):
        evaluator = EvalOrchestrator("https://example.com")
        evaluator.results = {
            "availability": type('obj', (object,), {'score': 85})(),
            "performance": type('obj', (object,), {'score': 90})(),
            "security": type('obj', (object,), {'score': 75})(),
            "compatibility": type('obj', (object,), {'score': 88})(),
            "stability": type('obj', (object,), {'score': 92})(),
            "scalability": type('obj', (object,), {'score': 80})(),
            "overall_score": 85.5,
            "timestamp": "2026-08-07T00:00:00",
            "target_url": "https://example.com",
        }
        
        report = evaluator.generate_report()
        assert "网站操作能力评估报告" in report
        assert "85.5" in report


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
