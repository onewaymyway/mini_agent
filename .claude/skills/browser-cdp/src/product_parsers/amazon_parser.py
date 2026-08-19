#!/usr/bin/env python
"""
amazon_parser.py - Amazon商品解析器

解析Amazon搜索结果页和商品详情页。
支持amazon.com、amazon.co.jp等主流站点。
"""

import re
from typing import List, Dict, Any

from src.product_parsers.base import BaseProductParser, ProductData


class AmazonProductParser(BaseProductParser):
    """Amazon商品解析器"""

    @property
    def source_name(self) -> str:
        return "amazon"

    @property
    def supported_url_patterns(self) -> List[str]:
        return ["amazon.com", "amazon.co.jp", "amazon.co.uk", "amazon.de", "amazon.fr", "amazon.ca", "amazon.in"]

    # ========== Amazon列表页选择器 ==========
    AMAZON_LIST_SELECTORS = {
        "items": [".s-result-item", "[data-asin]", ".sg-col-20-of-24", ".sg-col-16-of-20"],
        "title": [".s-title-instructions-style span", "h2 a span", ".a-text-normal", ".s-item__title span"],
        "price": [".a-price .a-offscreen", ".a-text-bold", ".s-price"],
        "original_price": [".a-text-decoration-none", ".a-price .a-offscreen.strikethrough", ".list-price"],
        "rating": [".a-icon-alt", ".a-star-rating .a-icon-alt"],
        "reviews_count": [".s-item__reviewCount", ".a-size-small"],
        "prime": [".s-prime", ".a-icon-prime", ".p13n-sc-primed"],
        "img": [".sg-col-inner img", ".responsiveItemImage img", ".s-image"],
        "link": ["a[href*='/dp/'], a[href*='/gp/'], a[href*='/SP']"],
        "shipping": [".s-item__shipping", ".a-spacing-top-mini"],
    }

    # ========== Amazon详情页选择器 ==========
    AMAZON_DETAIL_SELECTORS = {
        "title": ["#productTitle", ".product-title", "h1#title span", ".a-text-center h1"],
        "price": [".a-price .a-offscreen", "#priceblock_ourprice", ".a-text-bold", "#[id*='priceblock']"],
        "original_price": [".a-text-price", "#priceblock_wholeprice", ".list-price", "#[id*='wasPrice']"],
        "rating": ["#acrPopover", ".a-icon-alt", ".a-star-rating .a-icon-alt"],
        "reviews_count": ["#averageCustomerReviews span", ".a-size-small", ".cr-widget-Rating"],
        "brand": ["#bylineInfo a", ".a-link-normal", ".brand"],
        "availability": ["#availability span", ".a-size-medium", "#[id*='availability']"],
        "description": ["#productDescription", ".a-unordered-list", ".detail-bullets"],
        "specs": ["#techSpecification", ".detail-bullets li", ".product-details"],
        "images": ["#landingImage", ".altImages img", ".image-block img"],
        "bullet_points": ["#[id*='bullets'] li", ".a-unordered-list li"],
        "seller": ["#merchantInfoText", ".merchant-info"],
        "ships_from": ["#shipsFromSellerDisplay", ".ships-from-seller-display"],
    }

    def parse_list_page(self, html: str, url: str, max_results: int = 20) -> List[ProductData]:
        """解析Amazon搜索结果页"""
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "lxml")
        products = []

        # 尝试多种选择器获取商品列表
        item_selectors = [
            ".s-result-item",
            "[data-asin]",
            ".sg-col-20-of-24",
            ".sg-col-16-of-20",
        ]
        items = None
        for sel in item_selectors:
            items = soup.select(sel)
            if items:
                break
        if not items:
            items = soup.select(".sg-col-inner, .s-result-item")
        if not items:
            items = soup.find_all(class_=re.compile(r"s-result-item|sg-col"))
        if not items:
            return products

        for item in items[:max_results]:
            asin_el = item.get("data-asin") or item.select_one("[data-asin]")
            product = ProductData(source="amazon", scraped_at=self._now())

            # 标题
            for sel in self.AMAZON_LIST_SELECTORS["title"]:
                el = item.select_one(sel)
                if el:
                    product.title = self.clean_title(el.get_text(strip=True))
                    break

            # 链接（优先ASIN链接）
            for sel in self.AMAZON_LIST_SELECTORS["link"]:
                el = item.select_one(sel)
                if el and el.get("href"):
                    href = el["href"]
                    if href.startswith("//"):
                        href = "https:" + href
                    elif href.startswith("/"):
                        href = "https://www.amazon.com" + href
                    product.url = href
                    break

            # 如果URL没有内容但有ASIN，构造详情页URL
            if not product.url and asin_el:
                product.url = f"https://www.amazon.com/dp/{asin_el}"

            # 价格
            for sel in self.AMAZON_LIST_SELECTORS["price"]:
                el = item.select_one(sel)
                if el:
                    product.price = el.get_text(strip=True)
                    product.price_num = self.extract_price_number(product.price)
                    break

            # 原价
            for sel in self.AMAZON_LIST_SELECTORS["original_price"]:
                el = item.select_one(sel)
                if el:
                    product.original_price = el.get_text(strip=True)
                    break

            # Prime标识
            prime_el = item.select_one(".s-prime, .a-icon-prime")
            if prime_el:
                product.tags.append("prime")

            # 图片
            for sel in self.AMAZON_LIST_SELECTORS["img"]:
                el = item.select_one(sel)
                if el:
                    img_src = el.get("src") or el.get("data-src")
                    if img_src:
                        if img_src.startswith("//"):
                            img_src = "https:" + img_src
                        product.images.append(img_src)
                    break

            if product.title:
                products.append(product)

        return products

    def parse_detail_page(self, html: str, url: str) -> ProductData:
        """解析Amazon商品详情页"""
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "lxml")
        product = ProductData(source="amazon", url=url, scraped_at=self._now())

        # 从URL提取ASIN
        asin_match = re.search(r'/dp/([A-Z0-9]{10})', url)
        if asin_match:
            product.tags.append(f"asin:{asin_match.group(1)}")

        # 标题
        for sel in self.AMAZON_DETAIL_SELECTORS["title"]:
            el = soup.select_one(sel)
            if el:
                product.title = self.clean_title(el.get_text(strip=True))
                break

        # 价格
        for sel in self.AMAZON_DETAIL_SELECTORS["price"]:
            el = soup.select_one(sel)
            if el:
                product.price = el.get_text(strip=True)
                product.price_num = self.extract_price_number(product.price)
                break

        # 原价
        for sel in self.AMAZON_DETAIL_SELECTORS["original_price"]:
            el = soup.select_one(sel)
            if el:
                product.original_price = el.get_text(strip=True)
                break

        # 评分
        rating_el = soup.select_one(".a-icon-alt")
        if rating_el:
            rating_text = rating_el.get_text(strip=True)
            match = re.search(r'(\d+\.?\d*)', rating_text)
            if match:
                product.tags.append(f"rating:{match.group(1)}")

        # 评论数
        reviews_el = soup.select_one("[id*='reviews'], .a-size-small")
        if reviews_el:
            product.commit_count = reviews_el.get_text(strip=True)

        # 品牌
        brand_el = soup.select_one("#bylineInfo a")
        if brand_el:
            product.shop_name = brand_el.get_text(strip=True)
            product.tags.append("brand")

        # 库存状态
        avail_el = soup.select_one("#availability span")
        if avail_el:
            avail_text = avail_el.get_text(strip=True)
            if "Currently unavailable" in avail_text or "out of stock" in avail_text.lower():
                product.in_stock = False
            else:
                product.in_stock = True
            product.promo_text = avail_text[:100]

        # Bullet points
        bullet_el = soup.select_one("[id*='bullets']")
        if bullet_el:
            bullets = bullet_el.select("li")[:5]
            for b in bullets:
                text = b.get_text(strip=True)
                if text and len(text) > 10:
                    product.tags.append(text[:80])

        # 描述
        for sel in self.AMAZON_DETAIL_SELECTORS["description"]:
            el = soup.select_one(sel)
            if el:
                for tag in el.select("script, style"):
                    tag.decompose()
                desc = el.get_text(strip=True)[:2000]
                if desc:
                    product.description = desc
                break

        # 图片（主图优先）
        main_img = soup.select_one("#landingImage")
        if main_img:
            img_src = main_img.get("src") or main_img.get("data-old-hires")
            if img_src:
                product.images.append(img_src)
        else:
            for sel in self.AMAZON_DETAIL_SELECTORS["images"]:
                imgs = soup.select(sel)
                for img in imgs[:5]:
                    src = img.get("src") or img.get("data-old-hires") or img.get("data-hybrid-src")
                    if src and src.startswith(("http://", "https://", "//")):
                        if src.startswith("//"):
                            src = "https:" + src
                        if src not in product.images:
                            product.images.append(src)
                if product.images:
                    break

        # 规格参数
        spec_el = soup.select_one("#techSpecification")
        if spec_el:
            for row in spec_el.select("tr")[:20]:
                cols = row.select("td")
                if len(cols) >= 2:
                    k = cols[0].get_text(strip=True)
                    v = cols[1].get_text(strip=True)
                    if k and v:
                        product.specs[k] = v

        # 抓取页面片段用于调试
        product.raw_html_snippet = soup.prettify()[:500]

        return product

    def _now(self) -> str:
        from datetime import datetime
        return datetime.now().isoformat()

    def clean_title(self, title: str) -> str:
        title = re.sub(r'\s+', ' ', title).strip()
        # 去除开头的前缀标签如 [Sponsored], [Deal]
        title = re.sub(r'^\[.*?]\s*', '', title).strip()
        return title
