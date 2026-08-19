#!/usr/bin/env python
"""
pipeline.py - 商品抓取管道

将商品解析器集成到 browser-cdp 数据管道中。
"""

import json
import time
from pathlib import Path
from typing import List, Dict, Any, Optional

from src.product_parsers.base import ProductData
from src.product_parsers.jd_parser import JDProductParser
from src.product_parsers.taobao_parser import TaobaoProductParser
from src.product_parsers.pdd_parser import PDDProductParser
from src.product_parsers.universal_parser import UniversalProductParser
from src.product_parsers.amazon_parser import AmazonProductParser


# ========== 解析器注册表 ==========
PARSER_REGISTRY: Dict[str, Any] = {
    "jd": JDProductParser(),
    "taobao": TaobaoProductParser(),
    "pdd": PDDProductParser(),
    "universal": UniversalProductParser(),
    "amazon": AmazonProductParser(),
}


def get_parser(source: str) -> Any:
    """获取指定数据源的解析器"""
    return PARSER_REGISTRY.get(source.lower())


def resolve_parser(url: str) -> Any:
    """根据URL自动解析并返回对应的解析器"""
    url_lower = url.lower()
    for parser in PARSER_REGISTRY.values():
        if parser.detect(url_lower):
            return parser
    return PARSER_REGISTRY["universal"]


class ProductPipeline:
    """商品抓取管道"""

    def __init__(self, output_dir: str = None):
        self.output_dir = Path(output_dir) if output_dir else Path(__file__).parent.parent.parent.parent / "output" / "products"
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.stats = {"total": 0, "success": 0, "failed": 0}

    def parse_list_page(self, html: str, url: str, max_results: int = 20) -> List[ProductData]:
        """解析商品列表页"""
        parser = resolve_parser(url)
        source = parser.source_name
        print(f"[管道] 使用 {source} 解析器处理列表页: {url[:60]}...")
        try:
            products = parser.parse_list_page(html, url, max_results)
            self.stats["total"] += 1
            self.stats["success"] += 1
            return products
        except Exception as e:
            self.stats["failed"] += 1
            print(f"[管道错误] {source} 解析失败: {e}")
            return []

    def parse_detail_page(self, html: str, url: str) -> ProductData:
        """解析商品详情页"""
        parser = resolve_parser(url)
        source = parser.source_name
        print(f"[管道] 使用 {source} 解析器处理详情页: {url[:60]}...")
        try:
            product = parser.parse_detail_page(html, url)
            self.stats["total"] += 1
            self.stats["success"] += 1
            return product
        except Exception as e:
            self.stats["failed"] += 1
            print(f"[管道错误] {source} 解析失败: {e}")
            return ProductData(source=source, url=url)

    def save_product(self, product: ProductData, filename: str = None) -> Path:
        """保存商品数据到文件"""
        if not filename:
            safe_title = product.title[:30].replace(" ", "_").replace("/", "-")
            safe_title = "".join(c for c in safe_title if c.isalnum() or c in "_-.") or "product"
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            filename = f"{product.source}_{safe_title}_{timestamp}.json"
        filepath = self.output_dir / filename
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(product.to_dict(), f, ensure_ascii=False, indent=2)
        print(f"[管道] 已保存: {filepath}")
        return filepath

    def batch_parse(self, html_list: List[Dict[str, str]], max_results: int = 10) -> List[ProductData]:
        """批量解析多个页面"""
        all_products = []
        for item in html_list:
            html = item.get("html", "")
            url = item.get("url", "")
            if not html or not url:
                continue
            products = self.parse_list_page(html, url, max_results)
            all_products.extend(products)
            time.sleep(0.5)
        return all_products

    def get_stats(self) -> Dict[str, Any]:
        return {**self.stats, "output_dir": str(self.output_dir), "registered_parsers": list(PARSER_REGISTRY.keys())}
