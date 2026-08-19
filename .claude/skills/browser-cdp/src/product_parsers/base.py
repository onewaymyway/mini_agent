#!/usr/bin/env python
"""
base.py - 商品解析器抽象基类

定义商品数据的统一结构和解析器接口。
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any
from datetime import datetime
import re


@dataclass
class ProductData:
    """商品数据统一结构"""
    # 基本信息
    source: str = ""                    # 数据源标识 (jd/taobao/pdd)
    title: str = ""                     # 商品标题
    url: str = ""                       # 商品链接
    price: str = ""                     # 当前价格
    price_num: float = 0.0              # 价格数值化
    original_price: str = ""            # 原价/划线价
    discount: str = ""                  # 折扣信息

    # 销售信息
    sales: str = ""                     # 销量
    sales_count: int = 0                # 销量数值化
    commit_count: str = ""              # 评论数
    shop_name: str = ""                 # 店铺名称
    shop_url: str = ""                  # 店铺链接
    location: str = ""                  # 发货地

    # 媒体资源
    images: List[str] = field(default_factory=list)
    video_url: str = ""                 # 商品视频

    # 详情信息
    description: str = ""               # 商品描述
    specs: Dict[str, str] = field(default_factory=dict)
    category: str = ""                  # 商品分类
    tags: List[str] = field(default_factory=list)

    # 状态信息
    in_stock: bool = True               # 是否有货
    is_promotion: bool = False          # 是否促销中
    promo_text: str = ""                # 促销文案

    # 元数据
    scraped_at: str = ""
    raw_html_snippet: str = ""          # 原始HTML片段（用于调试）

    def __post_init__(self):
        if not self.price_num and self.price:
            self.price_num = self._extract_price_number(self.price)

    @staticmethod
    def _extract_price_number(price_text: str) -> float:
        matches = re.findall(r'[\d,]+\.?\d*', price_text.replace(',', ''))
        if matches:
            return float(matches[0])
        return 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source": self.source,
            "title": self.title,
            "url": self.url,
            "price": self.price,
            "original_price": self.original_price,
            "discount": self.discount,
            "sales": self.sales,
            "sales_count": self.sales_count,
            "commit_count": self.commit_count,
            "shop_name": self.shop_name,
            "shop_url": self.shop_url,
            "location": self.location,
            "images": self.images[:5],  # 最多存5张图
            "description": self.description[:2000],
            "specs": self.specs,
            "category": self.category,
            "tags": self.tags,
            "in_stock": self.in_stock,
            "is_promotion": self.is_promotion,
            "promo_text": self.promo_text,
            "scraped_at": self.scraped_at or datetime.now().isoformat(),
        }

    def to_json(self) -> str:
        import json
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ProductData":
        valid_keys = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in data.items() if k in valid_keys}
        return cls(**filtered)


class BaseProductParser(ABC):
    """商品解析器抽象基类"""

    @property
    @abstractmethod
    def source_name(self) -> str:
        """数据源名称"""
        ...

    @property
    @abstractmethod
    def supported_url_patterns(self) -> List[str]:
        """支持的URL模式列表"""
        ...

    @abstractmethod
    def parse_list_page(self, html: str, url: str, max_results: int = 20) -> List[ProductData]:
        """解析商品列表页"""
        ...

    @abstractmethod
    def parse_detail_page(self, html: str, url: str) -> ProductData:
        """解析商品详情页"""
        ...

    def detect(self, url: str) -> bool:
        """检测URL是否属于此解析器处理范围"""
        url_lower = url.lower()
        return any(pattern.lower() in url_lower for pattern in self.supported_url_patterns)

    def extract_price_number(self, price_text: str) -> float:
        """从价格文本中提取数字"""
        import re
        matches = re.findall(r'[\d,]+\.?\d*', price_text.replace(',', ''))
        if matches:
            return float(matches[0])
        return 0.0

    def extract_sales_number(self, sales_text: str) -> int:
        """从销量文本中提取数值"""
        import re
        # 处理 "10万+", "5000+", "1.2万" 等格式
        match = re.search(r'([\d.]+)\s*([万千百]?)', sales_text)
        if match:
            num = float(match.group(1))
            unit = match.group(2)
            multipliers = {'': 1, '百': 100, '千': 1000, '万': 10000}
            return int(num * multipliers.get(unit, 1))
        # 纯数字
        matches = re.findall(r'\d+', sales_text)
        if matches:
            return int(matches[0])
        return 0

    def clean_title(self, title: str) -> str:
        """清理商品标题"""
        # 去除多余空白
        title = re.sub(r'\s+', ' ', title).strip()
        # 去除常见的前缀标签如 [天猫]、[官方标配]、【自营】、（红色）等
        # 按长度从长到短排序，避免部分匹配
        for pattern in [
            r'^【.*?】\s*',   # 【自营】
            r'^\[.*?\]\s*',   # [天猫]
            r'^（.*?）\s*',   # （红色）
            r'^\(.*?\)\s*',   # (red)
        ]:
            title = re.sub(pattern, '', title).strip()
        return title
