"""
测试执行框架模块

提供统一的测试执行器、Mock 浏览器工厂、测试数据生成器和报告模板。
"""
from .executor import TestExecutor
from .mock_factory import MockBrowserFactory
from .data_generator import TestDataGenerator
from .report_template import TestReportTemplate

__all__ = [
    'TestExecutor',
    'MockBrowserFactory',
    'TestDataGenerator',
    'TestReportTemplate',
]
