"""
适配器包 - 统一网站操作入口。

- base:          BaseWebsiteAdapter 基类（现有）
- registry:      AdapterRegistry 注册中心
- mixins/:       通用能力混入（电商/后台/CSRF/图表）
"""
from src.adapters.base import (
    BaseWebsiteAdapter, AdapterResult, WebsiteConfig,
    AntiCrawlLevel, AdapterStatus, EvaluationDimension, DimensionScore,
)
from src.adapters.registry import AdapterRegistry, get_registry, register, get_adapter

__all__ = [
    "BaseWebsiteAdapter",
    "AdapterResult",
    "WebsiteConfig",
    "AntiCrawlLevel",
    "AdapterStatus",
    "EvaluationDimension",
    "DimensionScore",
    "AdapterRegistry",
    "get_registry",
    "register",
    "get_adapter",
]
