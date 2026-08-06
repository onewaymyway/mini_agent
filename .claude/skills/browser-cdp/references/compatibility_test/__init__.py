"""
网站兼容性测试框架

提供完整的网站兼容性测试能力，覆盖电商、新闻、社交、政务四类网站。
"""

from .models import WebsiteConfig, TestCase, TestResult
from .executor import TestCaseExecutor
from .scheduler import TestScheduler
from .collector import ResultCollector
from .evaluator import EvaluationMetrics

__all__ = [
    "WebsiteConfig",
    "TestCase",
    "TestResult",
    "TestCaseExecutor",
    "TestScheduler",
    "ResultCollector",
    "EvaluationMetrics",
]
