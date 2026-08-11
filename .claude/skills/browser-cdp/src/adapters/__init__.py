#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
adapters - 网站适配器模块

提供统一的网站抓取适配器接口，支持快速扩展新网站类型。
"""

from .base import (
    BaseWebsiteAdapter,
    WebsiteConfig,
    AdapterResult,
    AdapterStatus,
    AntiCrawlLevel,
    EvaluationDimension,
)

__all__ = [
    "BaseWebsiteAdapter",
    "WebsiteConfig",
    "AdapterResult",
    "AdapterStatus",
    "AntiCrawlLevel",
    "EvaluationDimension",
]
