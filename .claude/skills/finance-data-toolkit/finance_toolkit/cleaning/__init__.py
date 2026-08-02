"""
数据清洗模块
提供 L1-L4 分级清洗流水线：结构标准化、类型转换、时间标准化、字段映射、业务校验、特征工程
以及去重、缺失值处理、时间对齐、增量更新、质量监控功能
"""

from .pipeline import (
    CleanLevel,
    CleanResult,
    BaseCleaner,
    CleanPipeline,
)
from .normalizers import (
    StructureNormalizer,
    TypeCoercer,
    TimeNormalizer,
)
from .mappers import (
    FieldMapper,
    SymbolNormalizer,
)
from .validators import (
    QuoteValidator,
    FinancialValidator,
    NewsValidator,
    GubaValidator,
)
from .features import (
    FeatureEngineer,
    TechnicalFeatureEngineer,
    VolatilityFeatureEngineer,
)
from .dedup import (
    Deduplicator,
    IncrementalDeduplicator,
    MemoryStorage,
)
from .quality import (
    QualityMetrics,
    QualityMonitor,
    DataQualityReport,
    QualityMetricsCalculator,
    QualityThreshold,
)
from .missing import (
    MissingValueHandler,
    TimeSeriesMissingHandler,
    FillStrategy,
    FieldFillConfig,
)
from .alignment import (
    TimeAligner,
    CalendarAligner,
)

__all__ = [
    # 核心流水线
    'CleanLevel',
    'CleanResult',
    'BaseCleaner',
    'CleanPipeline',
    # L1 结构标准化
    'StructureNormalizer',
    'TypeCoercer',
    'TimeNormalizer',
    # L2 字段映射
    'FieldMapper',
    'SymbolNormalizer',
    # L3 业务校验
    'QuoteValidator',
    'FinancialValidator',
    'NewsValidator',
    'GubaValidator',
    # L4 特征工程
    'FeatureEngineer',
    'TechnicalFeatureEngineer',
    'VolatilityFeatureEngineer',
    # 去重
    'Deduplicator',
    'IncrementalDeduplicator',
    'MemoryStorage',
    # 质量监控
    'QualityMetrics',
    'QualityMonitor',
    'DataQualityReport',
    'QualityMetricsCalculator',
    'QualityThreshold',
    # 缺失值处理
    'MissingValueHandler',
    'TimeSeriesMissingHandler',
    'FillStrategy',
    'FieldFillConfig',
    # 时间对齐
    'TimeAligner',
    'CalendarAligner',
]

__version__ = '1.0.0'