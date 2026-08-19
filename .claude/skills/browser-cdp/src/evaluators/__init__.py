"""
browser-cdp 网站操作能力评估模块

提供完整的评估框架，包括：
- 抓取成功率评估
- 页面加载性能评估
- 元素定位准确率评估
- 反检测能力评估
- 稳定性评估
- 错误恢复能力评估
"""

from .base_evaluator import BaseEvaluator
from .success_rate_evaluator import SuccessRateEvaluator
from .performance_evaluator import PerformanceEvaluator
from .element_evaluator import ElementEvaluator
from .anti_detection_evaluator import AntiDetectionEvaluator
from .stability_evaluator import StabilityEvaluator
from .data_quality_evaluator import DataQualityEvaluator, DataQualityMonitor
from .report_generator import ReportGenerator
from .validators import (
    Severity,
    FieldType,
    DataFreshness,
    FieldSchema,
    ValidationError,
    ValidationRule,
    ValidationResult,
    SchemaValidator,
    CompletenessValidator,
    FreshnessValidator,
    DomainValidator,
    ConsistencyValidator,
    DataValidator,
    build_article_schema,
    build_product_schema,
    build_news_schema,
    build_search_result_schema,
)

__all__ = [
    'BaseEvaluator',
    'SuccessRateEvaluator',
    'PerformanceEvaluator',
    'ElementEvaluator',
    'AntiDetectionEvaluator',
    'StabilityEvaluator',
    'DataQualityEvaluator',
    'DataQualityMonitor',
    'ReportGenerator',
    'Severity',
    'FieldType',
    'DataFreshness',
    'FieldSchema',
    'ValidationError',
    'ValidationRule',
    'ValidationResult',
    'SchemaValidator',
    'CompletenessValidator',
    'FreshnessValidator',
    'DomainValidator',
    'ConsistencyValidator',
    'DataValidator',
    'build_article_schema',
    'build_product_schema',
    'build_news_schema',
    'build_search_result_schema',
]
