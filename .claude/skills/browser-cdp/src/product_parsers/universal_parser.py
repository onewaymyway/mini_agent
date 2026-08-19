#!/usr/bin/env python
"""
universal_parser.py - 通用商品解析器

基于JSON-LD和通用选择器解析任意商品页面。
"""

import json
import re
from typing import List, Dict, Any, Optional

from src.product_parsers.base import BaseProductParser, ProductData


class UniversalProductParser(BaseProductParser):
    """通用商品解析器 - 支持任意电商平台"""

    @property
    def source_name(self) -> str:
        return "universal"

    @property
    def supported_url_patterns(self) -> List[str]:
        return ["/product/", "/item/", "/goods/", "/shop/"]

    # ========== 通用JSON-LD属性名 ==========
    JSON_LD_PRODUCT_PROPS = {
        "title": ["name", "headline", "productTitle"],
        "price": ["price"],
        "priceCurrency": ["priceCurrency", "currency"],
        "originalPrice": ["offers.highPrice", "ListPrice", "wasPrice"],
        "image": ["image", "imageUrl", "thumbnailUrl"],
        "description": ["description", "shortDescription", "sku"],
        "brand": ["brand", "manufacturer"],
        "sku": ["sku", "model"],
        "availability": ["offers.availability", "availability"],
        "reviewCount": ["aggregateRating.reviewCount", "reviewCount"],
        "rating": ["aggregateRating.ratingValue", "ratingValue"],
        "seller": ["seller.name", "seller"],
        "category": ["categoryCode", "productID", "gtin"],
    }

    # ========== 通用HTML选择器 ==========
    UNIVERSAL_SELECTORS = {
        "title": ["h1.product-title, h1.item-name, h1.goods-name, #productTitle, .product-title", "h1, title"],
        "price": ["[class*='price'], [itemprop='price']", ".product-price, .item-price, .goods-price, #price"],
        "original_price": ["[class*='original-price'], [class*='list-price'], [class*='was-price']"],
        "image": ["img[alt*='product'], img[itemprop='image'], .product-image, .item-img"],
        "description": ["[itemprop='description'], .product-desc, .item-desc, .description, [class*='description'], #description"],
        "specs": ["table.specs, .spec-table, [itemprop='spec'], .parameter-table"],
        "review": ["[itemprop='reviewCount'], .review-count, .comment-count"],
    }

    def parse_list_page(self, html: str, url: str, max_results: int = 20) -> List[ProductData]:
        """通用列表页解析"""
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "lxml")
        products = []

        # 尝试多种商品容器选择器
        container_selectors = [
            '[itemtype*="Product"]',
            '.product-item, .goods-item, .item',
            '[class*="product"], [class*="goods"], [class*="item"]',
            '.card, .list-item',
        ]
        items = None
        for sel in container_selectors:
            items = soup.select(sel)
            if items:
                break
        if not items:
            items = soup.find_all(class_=re.compile(r'product|goods|item|card|list'))
        if not items:
            return products

        for item in items[:max_results]:
            product = ProductData(source="universal", scraped_at=self._now())

            # 提取链接
            link = item.select_one('a[href*="product"], a[href*="item"], a[href*="goods"]')
            if link and link.get("href"):
                href = link["href"]
                if href.startswith("//"):
                    href = "https:" + href
                elif href.startswith("/"):
                    href = url.split("/")[0] + "//" + url.split("/")[2] + href
                product.url = href

            # 提取标题
            title = item.select_one('[itemprop="name"], .product-title, .item-name, h2, h3, .name')
            if title:
                product.title = self.clean_title(title.get_text(strip=True))

            # 提取价格
            price_el = item.select_one('[itemprop="price"], .price, [class*="price"]')
            if price_el:
                product.price = price_el.get_text(strip=True)
                product.price_num = self.extract_price_number(product.price)

            # 提取图片
            img = item.select_one('img[alt], img[itemprop="image"], .product-img img')
            if img and img.get("src"):
                src = img["src"]
                if src.startswith("//"):
                    src = "https:" + src
                product.images.append(src)

            if product.title:
                products.append(product)

        return products

    def parse_detail_page(self, html: str, url: str) -> ProductData:
        """通用详情页解析"""
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "lxml")
        product = ProductData(source="universal", url=url, scraped_at=self._now())

        # ========== 优先从JSON-LD提取 ==========
        ld_results = self._extract_from_jsonld(soup)
        if ld_results:
            product.title = ld_results.get("title", product.title)
            product.price = ld_results.get("price", product.price)
            product.price_num = ld_results.get("price_num", product.price_num)
            product.original_price = ld_results.get("original_price", product.original_price)
            product.images = ld_results.get("images", product.images)
            product.description = ld_results.get("description", product.description)
            product.commit_count = ld_results.get("review_count", product.commit_count)
            product.shop_name = ld_results.get("seller", product.shop_name)
            product.specs = ld_results.get("specs", product.specs)
            return product

        # ========== HTML选择器回退 ========== 
        # 标题
        for sel in self.UNIVERSAL_SELECTORS["title"]:
            el = soup.select_one(sel)
            if el:
                product.title = self.clean_title(el.get_text(strip=True))
                if product.title:
                    break

        # 价格
        for sel in self.UNIVERSAL_SELECTORS["price"]:
            el = soup.select_one(sel)
            if el:
                product.price = el.get_text(strip=True)
                product.price_num = self.extract_price_number(product.price)
                break

        # 原价
        for sel in self.UNIVERSAL_SELECTORS["original_price"]:
            el = soup.select_one(sel)
            if el:
                product.original_price = el.get_text(strip=True)
                break

        # 图片
        for sel in self.UNIVERSAL_SELECTORS["image"]:
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
        else:
            for img in soup.select("img")[:5]:
                src = img.get("src") or img.get("data-src")
                if src and src.startswith(("http://", "https://")):
                    product.images.append(src)

        # 描述
        for sel in self.UNIVERSAL_SELECTORS["description"]:
            el = soup.select_one(sel)
            if el:
                for tag in el.select("script, style"):
                    tag.decompose()
                product.description = el.get_text(strip=True)[:3000]
                break

        # 规格参数
        table = soup.select_one(self.UNIVERSAL_SELECTORS["specs"][0])
        if table:
            for row in table.select("tr")[:20]:
                cols = row.select("td")
                if len(cols) >= 2:
                    k = cols[0].get_text(strip=True)
                    v = cols[1].get_text(strip=True)
                    if k and v:
                        product.specs[k] = v

        product.raw_html_snippet = soup.prettify()[:500]
        return product

    def _extract_from_jsonld(self, soup) -> Optional[Dict[str, Any]]:
        """从JSON-LD结构化数据中提取商品信息"""
        scripts = soup.find_all("script", type="application/ld+json")
        for script in scripts:
            try:
                data = json.loads(script.string)
                results = self._parse_jsonld_product(data)
                if results:
                    return results
            except (json.JSONDecodeError, TypeError):
                continue

        # 也尝试单个JSON对象
        json_divs = soup.find_all("div", class_=re.compile(r'json-ld|ld+json|schema'))
        for div in json_divs:
            try:
                text = div.get_text()
                data = json.loads(text)
                results = self._parse_jsonld_product(data)
                if results:
                    return results
            except (json.JSONDecodeError, TypeError):
                continue

        return None

    def _parse_jsonld_product(self, data: Any) -> Optional[Dict[str, Any]]:
        """递归解析JSON-LD数据结构"""
        if isinstance(data, dict):
            # 检查是否是Product类型
            is_product = (
                data.get("@type") == "Product" or
                data.get("@type") == ["Product", "Offer"] or
                any(k in data for k in ["name", "offers", "sku", "productID"])
            )

            if is_product:
                result = {"title": "", "price": "", "original_price": "", "images": [], "specs": {}, "price_num": 0.0}

                # 标题
                for key in self.JSON_LD_PRODUCT_PROPS["title"]:
                    val = self._get_nested(data, key)
                    if val:
                        result["title"] = str(val)
                        break

                # 价格 — 同时提取数值方便 price_num 转换
                offers = data.get("offers", {})
                price_str = ""
                if isinstance(offers, dict):
                    price_str = str(offers.get("price", ""))
                    result["price"] = price_str
                    result["priceCurrency"] = offers.get("priceCurrency", "CNY")
                    result["original_price"] = str(offers.get("highPrice", ""))
                elif isinstance(offers, list):
                    for o in offers:
                        if isinstance(o, dict):
                            price_str = str(o.get("price", ""))
                            result["price"] = price_str
                            result["priceCurrency"] = o.get("priceCurrency", "CNY")
                            result["original_price"] = str(o.get("highPrice", ""))
                            break
                # 转换 price 字符串为数值
                if price_str:
                    matches = re.findall(r'[\d,]+\.?\d*', price_str.replace(',', ''))
                    if matches:
                        result["price_num"] = float(matches[0])

                # 图片
                images = data.get("image", [])
                if isinstance(images, str):
                    images = [images]
                if isinstance(images, list):
                    result["images"] = [img for img in images if isinstance(img, str) and img.startswith(("http", "//"))]

                # 评论数
                rating = data.get("aggregateRating", {})
                if isinstance(rating, dict):
                    result["review_count"] = str(rating.get("reviewCount", ""))

                # 品牌
                brand = data.get("brand", {})
                if isinstance(brand, dict):
                    result["seller"] = brand.get("name", "")
                elif isinstance(brand, str):
                    result["seller"] = brand

                if result["title"]:
                    return result

            # 递归子对象
            for key, value in data.items():
                if isinstance(value, dict):
                    sub_result = self._parse_jsonld_product(value)
                    if sub_result and sub_result.get("title"):
                        return sub_result
                elif isinstance(value, list):
                    for item in value:
                        if isinstance(item, dict):
                            sub_result = self._parse_jsonld_product(item)
                            if sub_result and sub_result.get("title"):
                                return sub_result

        return None

    @staticmethod
    def _get_nested(data: Dict, key: str) -> Any:
        """获取嵌套字典的值"""
        if key in data:
            return data[key]
        parts = key.split(".")
        current = data
        for part in parts:
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                return None
        return current

    def _now(self) -> str:
        from datetime import datetime
        return datetime.now().isoformat()

    def clean_title(self, title: str) -> str:
        title = re.sub(r'\s+', ' ', title).strip()
        title = re.sub(r'[\[\]【】\(\)（）].*$', '', title).strip()
        return title