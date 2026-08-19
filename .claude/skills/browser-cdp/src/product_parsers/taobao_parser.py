#!/usr/bin/env python
"""
taobao_parser.py - 淘宝/天猫商品解析器

解析淘宝/天猫搜索结果页和商品详情页。
"""

import re
from typing import List, Dict, Any

from src.product_parsers.base import BaseProductParser, ProductData


class TaobaoProductParser(BaseProductParser):
    """淘宝/天猫商品解析器"""

    @property
    def source_name(self) -> str:
        return "taobao"

    @property
    def supported_url_patterns(self) -> List[str]:
        return ["taobao.com", "tmall.com", "item.taobao.com", "item.tmall.com"]

    # ========== 淘宝列表页选择器 ==========
    TB_LIST_SELECTORS = {
        "items": [".item, .grid-item, [class*='product'], [class*='card']"],
        "title": [".title a", ".product-title a", "[class*='title'] a"],
        "price": [".price", "[class*='price']", ".g_price"],
        "sales": [".sales, .deal-num, [class*='sales']"],
        "shop": [".shop a, [class*='shop'] a, .seller-name"],
        "location": [".location, [class*='location']"],
        "img": ["img[data-src], img[src*='img']"],
        "is_tmall": [".tag-tmall, [class*='tmall'], .icon-tmall"],
        "link": ["a[href*='item']"],
    }

    # ========== 淘宝详情页选择器 ==========
    TB_DETAIL_SELECTORS = {
        "title": [".tb-detail-hd h1, .item-title, [class*='title'] h1"],
        "price": [".tb-price, [class*='price']"],
        "original_price": [".tb-original-price, [class*='original-price']"],
        "sales": [".tb-sales, [class*='sales']"],
        "reviews": [".tb-review, [class*='review']"],
        "shop": [".tb-shop a, [class*='shop'] a"],
        "shop_score": [".shop-score, [class*='shop-score']"],
        "location": [".tb-location, [class*='location']"],
        "images": [".tb-img img, .slider-img img"],
        "description": [".tb-desc, .detail-content, [class*='description']"],
        "params": [".tb-props tbody tr, [class*='params'] tr"],
    }

    def parse_list_page(self, html: str, url: str, max_results: int = 20) -> List[ProductData]:
        """解析淘宝/天猫搜索结果页"""
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "lxml")
        products = []

        # 尝试多种选择器
        item_selectors = [
            ".item",
            ".grid-item",
            "[class*='product']",
            "[class*='card']",
            "[class*='list-item']",
        ]
        items = None
        for sel in item_selectors:
            items = soup.select(sel)
            if items:
                break
        if not items:
            items = soup.find_all(class_=re.compile(r'item|product|card|list'))
        if not items:
            return products

        platform_map = {"tmall": "tmall", "taobao": "taobao"}

        for item in items[:max_results]:
            product = ProductData(source="taobao", scraped_at=self._now())
            is_tmall = False

            # 标题
            for sel in self.TB_LIST_SELECTORS["title"]:
                el = item.select_one(sel)
                if el:
                    product.title = self.clean_title(el.get_text(strip=True))
                    break

            # 链接
            for sel in self.TB_LIST_SELECTORS["link"]:
                el = item.select_one(sel)
                if el and el.get("href"):
                    href = el["href"]
                    if href.startswith("//"):
                        href = "https:" + href
                    product.url = href
                    if "tmall.com" in href:
                        is_tmall = True
                    break

            # 平台标记
            for sel in self.TB_LIST_SELECTORS["is_tmall"]:
                if item.select_one(sel):
                    is_tmall = True
                    break
            product.tags = ["tmall"] if is_tmall else ["taobao"]

            # 价格
            for sel in self.TB_LIST_SELECTORS["price"]:
                el = item.select_one(sel)
                if el:
                    product.price = el.get_text(strip=True)
                    product.price_num = self.extract_price_number(product.price)
                    break

            # 销量
            for sel in self.TB_LIST_SELECTORS["sales"]:
                el = item.select_one(sel)
                if el:
                    product.sales = el.get_text(strip=True)
                    product.sales_count = self.extract_sales_number(product.sales)
                    break

            # 店铺
            for sel in self.TB_LIST_SELECTORS["shop"]:
                el = item.select_one(sel)
                if el:
                    product.shop_name = el.get_text(strip=True)
                    break

            # 所在地
            for sel in self.TB_LIST_SELECTORS["location"]:
                el = item.select_one(sel)
                if el:
                    product.location = el.get_text(strip=True)
                    break

            # 图片
            for sel in self.TB_LIST_SELECTORS["img"]:
                el = item.select_one(sel)
                if el:
                    img_src = el.get("data-src") or el.get("src")
                    if img_src:
                        product.images.append(img_src)
                    break

            if product.title:
                products.append(product)

        return products

    def parse_detail_page(self, html: str, url: str) -> ProductData:
        """解析淘宝/天猫商品详情页"""
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "lxml")
        is_tmall = "tmall.com" in url.lower()
        product = ProductData(source="taobao", url=url, scraped_at=self._now())
        product.tags = ["tmall"] if is_tmall else ["taobao"]

        # 标题
        for sel in self.TB_DETAIL_SELECTORS["title"]:
            el = soup.select_one(sel)
            if el:
                product.title = self.clean_title(el.get_text(strip=True))
                break

        # 价格
        for sel in self.TB_DETAIL_SELECTORS["price"]:
            el = soup.select_one(sel)
            if el:
                product.price = el.get_text(strip=True)
                product.price_num = self.extract_price_number(product.price)
                break

        # 原价
        for sel in self.TB_DETAIL_SELECTORS["original_price"]:
            el = soup.select_one(sel)
            if el:
                product.original_price = el.get_text(strip=True)
                break

        # 销量
        for sel in self.TB_DETAIL_SELECTORS["sales"]:
            el = soup.select_one(sel)
            if el:
                product.sales = el.get_text(strip=True)
                product.sales_count = self.extract_sales_number(product.sales)
                break

        # 评价数
        for sel in self.TB_DETAIL_SELECTORS["reviews"]:
            el = soup.select_one(sel)
            if el:
                product.commit_count = el.get_text(strip=True)
                break

        # 店铺
        for sel in self.TB_DETAIL_SELECTORS["shop"]:
            el = soup.select_one(sel)
            if el:
                product.shop_name = el.get_text(strip=True)
                break

        # 店铺评分
        for sel in self.TB_DETAIL_SELECTORS["shop_score"]:
            el = soup.select_one(sel)
            if el:
                product.raw_html_snippet = el.get_text(strip=True)
                break

        # 所在地
        for sel in self.TB_DETAIL_SELECTORS["location"]:
            el = soup.select_one(sel)
            if el:
                product.location = el.get_text(strip=True)
                break

        # 规格参数
        for sel in self.TB_DETAIL_SELECTORS["params"]:
            rows = soup.select(sel)
            for row in rows[:20]:
                cols = row.select("td, th")
                if len(cols) >= 2:
                    k = cols[0].get_text(strip=True)
                    v = cols[1].get_text(strip=True)
                    if k and v:
                        product.specs[k] = v
            break

        # 商品描述
        for sel in self.TB_DETAIL_SELECTORS["description"]:
            el = soup.select_one(sel)
            if el:
                for tag in el.select("script, style"):
                    tag.decompose()
                product.description = el.get_text(strip=True)[:3000]
                break

        # 图片
        for sel in self.TB_DETAIL_SELECTORS["images"]:
            imgs = soup.select(sel)
            for img in imgs[:5]:
                src = img.get("data-src") or img.get("src")
                if src and src.startswith(("http://", "https://", "//")):
                    if src.startswith("//"):
                        src = "https:" + src
                    if src not in product.images:
                        product.images.append(src)
            if product.images:
                break
        else:
            for img in soup.select("img")[:5]:
                src = img.get("src") or img.get("data-src")
                if src and src.startswith(("http://", "https://")):
                    product.images.append(src)

        product.raw_html_snippet = soup.prettify()[:500]
        return product

    def _now(self) -> str:
        from datetime import datetime
        return datetime.now().isoformat()

    def clean_title(self, title: str) -> str:
        title = re.sub(r'\s+', ' ', title).strip()
        # 只去除开头的标签前缀，如 [天猫]、【官方标配】，保留标题主体
        title = re.sub(r'^[\[【（].*?[\]】）]\s*', '', title).strip()
        return title
