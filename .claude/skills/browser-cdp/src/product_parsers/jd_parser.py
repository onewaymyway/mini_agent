#!/usr/bin/env python
"""
jd_parser.py - 京东商品解析器

解析京东搜索结果页和商品详情页。
"""

import re
from typing import List, Dict, Any

from src.product_parsers.base import BaseProductParser, ProductData


class JDProductParser(BaseProductParser):
    """京东商品解析器"""

    @property
    def source_name(self) -> str:
        return "jd"

    @property
    def supported_url_patterns(self) -> List[str]:
        return ["jd.com", "item.jd.com"]

    # ========== JD 列表页选择器 ==========
    JD_LIST_SELECTORS = {
        "items": ["#J_goodsList .item", ".gl-item", ".item"],
        "title": [".p-name em", ".name em", ".p-name a em"],
        "price": [".p-price strong", ".p-price i", ".price"],
        "commit": [".p-commit em", ".commit-count", ".p-commit strong"],
        "shop": [".p-shop a", ".shop-name", ".p-shop em"],
        "img": [".p-img img", ".gl-img img"],
        "link": [".p-name a", ".gl-i-wrap .p-name a"],
    }

    # ========== JD 详情页选择器 ==========
    JD_DETAIL_SELECTORS = {
        "title": [".sku-name", "h1#name", ".product-title"],
        "price": [".p-price strong", "#price", ".product-price"],
        "original_price": [".p-price del", ".original-price", ".list-price"],
        "commit_count": ["#comment-count", ".p-commit em", "#comment-count-wrap"],
        "shop_name": ["#shopName span", ".p-shop a", ".shop-name"],
        "specs_table": [".parameter2 p", ".parameter2 li", "#parameter2"],
        "description": [".description", ".desc", ".detail-content"],
        "images": ["#spec-n1 img", ".gallery-list img", ".preview-thumb"],
        "stock": ["#choose-btns", ".purchase", ".stock"],
    }

    def parse_list_page(self, html: str, url: str, max_results: int = 20) -> List[ProductData]:
        """解析京东搜索结果页"""
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "lxml")
        products = []

        # 尝试多种选择器获取商品列表
        item_selectors = [
            '#J_goodsList .item',
            '.gl-item',
            '[class*="gl-item"]',
            '.item',
        ]
        items = None
        for sel in item_selectors:
            items = soup.select(sel)
            if items:
                break
        if not items:
            items = soup.select('.goods-list .item, .item')
        if not items:
            items = soup.select('[class*="item"]')

        if not items:
            return products

        for item in items[:max_results]:
            product = ProductData(source="jd", scraped_at=self._now())

            # 标题
            for sel in self.JD_LIST_SELECTORS["title"]:
                el = item.select_one(sel)
                if el:
                    product.title = self.clean_title(el.get_text(strip=True))
                    break

            # 链接
            for sel in self.JD_LIST_SELECTORS["link"]:
                el = item.select_one(sel)
                if el and el.get("href"):
                    href = el["href"]
                    if href.startswith("//"):
                        href = "https:" + href
                    elif not href.startswith("http"):
                        href = "https://item.jd.com" + href if "/item" in href else href
                    product.url = href
                    break

            # 价格
            for sel in self.JD_LIST_SELECTORS["price"]:
                el = item.select_one(sel)
                if el:
                    product.price = el.get_text(strip=True)
                    product.price_num = self.extract_price_number(product.price)
                    break

            # 评论数
            for sel in self.JD_LIST_SELECTORS["commit"]:
                el = item.select_one(sel)
                if el:
                    product.commit_count = el.get_text(strip=True)
                    break

            # 店铺
            for sel in self.JD_LIST_SELECTORS["shop"]:
                el = item.select_one(sel)
                if el:
                    product.shop_name = el.get_text(strip=True)
                    break

            # 图片
            for sel in self.JD_LIST_SELECTORS["img"]:
                el = item.select_one(sel)
                if el:
                    img_src = el.get("src") or el.get("data-lazy-img")
                    if img_src:
                        if img_src.startswith("//"):
                            img_src = "https:" + img_src
                        product.images.append(img_src)
                    break

            if product.title:
                products.append(product)

        return products

    def parse_detail_page(self, html: str, url: str) -> ProductData:
        """解析京东商品详情页"""
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "lxml")
        product = ProductData(source="jd", url=url, scraped_at=self._now())

        # 标题
        for sel in self.JD_DETAIL_SELECTORS["title"]:
            el = soup.select_one(sel)
            if el:
                product.title = self.clean_title(el.get_text(strip=True))
                break

        # 价格
        for sel in self.JD_DETAIL_SELECTORS["price"]:
            el = soup.select_one(sel)
            if el:
                product.price = el.get_text(strip=True)
                product.price_num = self.extract_price_number(product.price)
                break

        # 原价
        for sel in self.JD_DETAIL_SELECTORS["original_price"]:
            el = soup.select_one(sel)
            if el:
                product.original_price = el.get_text(strip=True)
                break

        # 评论数
        for sel in self.JD_DETAIL_SELECTORS["commit_count"]:
            el = soup.select_one(sel)
            if el:
                product.commit_count = el.get_text(strip=True)
                break

        # 店铺
        for sel in self.JD_DETAIL_SELECTORS["shop_name"]:
            el = soup.select_one(sel)
            if el:
                product.shop_name = el.get_text(strip=True)
                break

        # 规格参数
        spec_el = soup.select_one("#parameter2, .parameter2")
        if spec_el:
            for p in spec_el.select("p, li"):
                text = p.get_text(strip=True)
                if ":" in text or " " in text:
                    parts = re.split(r'[:：]', text, 1)
                    if len(parts) == 2:
                        product.specs[parts[0].strip()] = parts[1].strip()

        # 描述
        for sel in self.JD_DETAIL_SELECTORS["description"]:
            el = soup.select_one(sel)
            if el:
                product.description = el.get_text(strip=True)[:3000]
                break

        # 图片
        img_sel = soup.select_one("#spec-n1, .gallery-list")
        if img_sel:
            for img in img_sel.select("img")[:5]:
                src = img.get("src") or img.get("data-src")
                if src and src.startswith(("http://", "https://")):
                    product.images.append(src)
        else:
            for img in soup.select("img")[:5]:
                src = img.get("src") or img.get("data-src")
                if src and src.startswith(("http://", "https://")) and "jpg" in src.lower():
                    product.images.append(src)

        # 库存状态
        stock_el = soup.select_one("#choose-btns")
        if stock_el:
            stock_text = stock_el.get_text()
            product.in_stock = "有货" in stock_text or "现货" in stock_text

        # 抓取页面截图用于调试
        product.raw_html_snippet = soup.prettify()[:500]

        return product

    def _now(self) -> str:
        from datetime import datetime
        return datetime.now().isoformat()

    def clean_title(self, title: str) -> str:
        title = re.sub(r'\s+', ' ', title).strip()
        title = re.sub(r'[\[\]【】\(\)（）].*$', '', title).strip()
        return title
