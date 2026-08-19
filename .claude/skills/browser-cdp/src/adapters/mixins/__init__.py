"""
混入模块包 - 提供跨领域通用能力扩展。

- ecom_mixin:      电商类站点（签名拦截、验证码处理、价格快照）
- admin_mixin:     后台系统（Session管理、CSRF、ECharts提取）
- csrf_handler:    CSRF Token 自动提取与注入
- chart_extractor: 图表数据提取（ECharts/AntV/Chart.js）
"""
from src.adapters.mixins.ecom_mixin import EcomMixin
from src.adapters.mixins.admin_mixin import AdminMixin
from src.adapters.mixins.csrf_handler import CsrfHandler
from src.adapters.mixins.chart_extractor import ChartExtractor

__all__ = [
    "EcomMixin",
    "AdminMixin",
    "CsrfHandler",
    "ChartExtractor",
]
