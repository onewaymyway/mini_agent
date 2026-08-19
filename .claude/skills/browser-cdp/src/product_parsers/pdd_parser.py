#!/usr/bin/env python
"""
pdd_parser.py - 拼多多商品解析器

解析拼多多搜索结果页和商品详情页。
"""

import re
from typing import List, Dict, Any

from src.product_parsers.base import BaseProductParser, ProductData


class PDDProductParser(BaseProductParser):
    """拼多多商品解析器"""

    @property
    def source_name(self) -> str:
        return "pdd"

    @property
    def supported_url_patterns(self) -> List[str]:
        return ["yangkeduo.com", "pinduoduo.com"]

    # ========== 拼多多列表页选择器 ==========
    PDD_LIST_SELECTORS = {
        "items": [".goods-card, .goods-item, [class*='goods-card']", ".list-mode .item"],
        "title": [".goods-name, [class*='goods-name'], .goods-name-inner, .goods-title"],
        "price": [".goods-price, [class*='goods-price'], .price"],
        "original_price": [".goods-original-price, [class*='original-price'], .list-price"],
        "sales": [".goods-sales, [class*='goods-sales'], .sales-count"],
        "shop": [".goods-shop, [class*='goods-shop'], .shop-name"],
        "img": [".goods-img img, .lazy-img, [class*='goods-img'] img"],
        "link": ["a[href*='goods.html'], a[href*='goods.php']"],
        "promo": [".promo-tag, [class*='promo'], .coupon-info"],
    }

    # ========== 拼多多详情页选择器 ==========
    PDD_DETAIL_SELECTORS = {
        "title": [".goods-name, [class*='goods-name'], .detail-title, h1"],
        "price": [".detail-price, [class*='detail-price'], .price", "#price"],
        "original_price": [".detail-original-price, [class*='original-price'], .list-price"],
        "sales": [".detail-sales, [class*='detail-sales'], .sales"],
        "shop": [".detail-shop, [class*='detail-shop'], .shop-name"],
        "specs": [".detail-specs, [class*='detail-specs'], .params"],
        "description": [".detail-desc, [class*='detail-desc'], .description"],
        "images": [".detail-gallery img, .swiper img, [class*='gallery'] img"],
        "coupon": [".coupon-info, [class*='coupon'], .discount-tag"],
    }

    def parse_list_page(self, html: str, url: str, max_results: int = 20) -> List[ProductData]:
        """解析拼多多搜索结果页"""
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "lxml")
        products = []

        # 尝试多种选择器
        item_selectors = [
            ".goods-card",
            ".goods-item",
            "[class*='goods-card']",
            ".list-mode .item",
        ]
        items = None
        for sel in item_selectors:
            items = soup.select(sel)
            if items:
                break
        if not items:
            items = soup.find_all(class_=re.compile(r'goods|item|card'))
        if not items:
            return products

        for item in items[:max_results]:
            product = ProductData(source="pdd", scraped_at=self._now())

            # 标题
            for sel in self.PDD_LIST_SELECTORS["title"]:
                el = item.select_one(sel)
                if el:
                    product.title = self.clean_title(el.get_text(strip=True))
                    break

            # 链接
            for sel in self.PDD_LIST_SELECTORS["link"]:
                el = item.select_one(sel)
                if el and el.get("href"):
                    href = el["href"]
                    if href.startswith("//"):
                        href = "https:" + href
                    elif href.startswith("/"):
                        href = "https://mobile.yangkeduo.com" + href
                    product.url = href
                    break

            # 拼团价格
            for sel in self.PDD_LIST_SELECTORS["price"]:
                el = item.select_one(sel)
                if el:
                    product.price = el.get_text(strip=True)
                    product.price_num = self.extract_price_number(product.price)
                    break

            # 原价
            for sel in self.PDD_LIST_SELECTORS["original_price"]:
                el = item.select_one(sel)
                if el:
                    product.original_price = el.get_text(strip=True)
                    break

            # 销量
            for sel in self.PDD_LIST_SELECTORS["sales"]:
                el = item.select_one(sel)
                if el:
                    product.sales = el.get_text(strip=True)
                    product.sales_count = self.extract_sales_number(product.sales)
                    break

            # 店铺
            for sel in self.PDD_LIST_SELECTORS["shop"]:
                el = item.select_one(sel)
                if el:
                    product.shop_name = el.get_text(strip=True)
                    break

            # 促销标签
            for sel in self.PDD_LIST_SELECTORS["promo"]:
                el = item.select_one(sel)
                if el:
                    promo_text = el.get_text(strip=True)
                    if promo_text:
                        product.is_promotion = True
                        product.promo_text = promo_text
                        product.tags.append(promo_text[:20])
                    break

            # 图片
            for sel in self.PDD_LIST_SELECTORS["img"]:
                el = item.select_one(sel)
                if el:
                    img_src = el.get("src") or el.get("data-src") or el.get("data-lazy-src")
                    if img_src:
                        if img_src.startswith("//"):
                            img_src = "https:" + img_src
                        product.images.append(img_src)
                    break

            if product.title:
                products.append(product)

        return products

    def parse_detail_page(self, html: str, url: str) -> ProductData:
        """解析拼多多商品详情页"""
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "lxml")
        product = ProductData(source="pdd", url=url, scraped_at=self._now())

        # 标题
        for sel in self.PDD_DETAIL_SELECTORS["title"]:
            el = soup.select_one(sel)
            if el:
                product.title = self.clean_title(el.get_text(strip=True))
                break

        # 拼团价格
        for sel in self.PDD_DETAIL_SELECTORS["price"]:
            el = soup.select_one(sel)
            if el:
                product.price = el.get_text(strip=True)
                product.price_num = self.extract_price_number(product.price)
                break

        # 原价
        for sel in self.PDD_DETAIL_SELECTORS["original_price"]:
            el = soup.select_one(sel)
            if el:
                product.original_price = el.get_text(strip=True)
                break

        # 销量
        for sel in self.PDD_DETAIL_SELECTORS["sales"]:
            el = soup.select_one(sel)
            if el:
                product.sales = el.get_text(strip=True)
                product.sales_count = self.extract_sales_number(product.sales)
                break

        # 店铺
        for sel in self.PDD_DETAIL_SELECTORS["shop"]:
            el = soup.select_one(sel)
            if el:
                product.shop_name = el.get_text(strip=True)
                break

        # 规格
        for sel in self.PDD_DETAIL_SELECTORS["specs"]:
            el = soup.select_one(sel)
            if el:
                for row in el.select("tr, div, li")[:20]:
                    text = row.get_text(strip=True)
                    if ":" in text or "=" in text:
                        parts = re.split(r'[:=:]', text, 1)
                        if len(parts) == 2:
                            product.specs[parts[0].strip()] = parts[1].strip()
                break

        # 描述
        for sel in self.PDD_DETAIL_SELECTORS["description"]:
            el = soup.select_one(sel)
            if el:
                for tag in el.select("script, style"):
                    tag.decompose()
                product.description = el.get_text(strip=True)[:3000]
                break

        # 图片
        for sel in self.PDD_DETAIL_SELECTORS["images"]:
            imgs = soup.select(sel)
            for img in imgs[:5]:
                src = img.get("src") or img.get("data-src")
                if src and src.startswith(("http://", "https://", "//")):
                    if src.startswith("//"):
                        src = "https:" + src
                    if src not in product.images:
                        product.images.append(src)
            if product.images:
                break

        # 优惠券/促销
        for sel in self.PDD_DETAIL_SELECTORS["coupon"]:
            el = soup.select_one(sel)
            if el:
                promo = el.get_text(strip=True)
                if promo:
                    product.is_promotion = True
                    product.promo_text = promo
                    break

        product.raw_html_snippet = soup.prettify()[:500]
        return product

    def _now(self) -> str:
        from datetime import datetime
        return datetime.now().isoformat()

    def clean_title(self, title: str) -> str:
        title = re.sub(r'\s+', ' ', title).strip()
        title = re.sub(r'[\[\]【】\(\)（）].*$', '', title).strip()
        return title
