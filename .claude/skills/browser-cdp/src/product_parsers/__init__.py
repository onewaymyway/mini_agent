"""
商品类页面解析器模块

提供通用商品解析能力，支持京东、淘宝、拼多多、Amazon等电商平台。
"""

from src.product_parsers.base import BaseProductParser, ProductData
from src.product_parsers.jd_parser import JDProductParser
from src.product_parsers.taobao_parser import TaobaoProductParser
from src.product_parsers.pdd_parser import PDDProductParser
from src.product_parsers.universal_parser import UniversalProductParser
from src.product_parsers.amazon_parser import AmazonProductParser
from src.product_parsers.pipeline import (
    get_parser,
    resolve_parser,
    ProductPipeline,
)

__all__ = [
    "BaseProductParser",
    "ProductData",
    "JDProductParser",
    "TaobaoProductParser",
    "PDDProductParser",
    "UniversalProductParser",
    "AmazonProductParser",
    "get_parser",
    "resolve_parser",
    "ProductPipeline",
]
