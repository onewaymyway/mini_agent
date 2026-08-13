"""
jd_search_pattern.py - 京东搜索交互模式

基于 EcommercePattern 实现京东的商品搜索与详情页抓取。
"""
from __future__ import annotations

import logging
from typing import Dict, Optional

from .ecommerce_pattern import EcommercePattern, EcommerceResults, ProductResultItem
from ..core.selector_manager import Selector, SelectorType

logger = logging.getLogger(__name__)


class JDSearchPattern(EcommercePattern):
    """京东搜索模式"""

    def __init__(self, session, domain: str = "jd.com", config: Optional[Dict] = None):
        jd_config = {
            "search_url": "https://search.jd.com/Search?keyword={query}",
            "cart_url": "https://cart.jd.com/cart.action",
            "max_pages": 3,
        }
        if config:
            jd_config.update(config)
        super().__init__(session, domain, jd_config)
        self._site_name = "jd"
        self._register_jd_selectors()

    def _register_jd_selectors(self):
        domain = self._domain
        selectors = {
            "search_input":    Selector(type=SelectorType.CSS, value="#key, input[name=keyword], input[placeholder*='商品']"),
            "search_button":   Selector(type=SelectorType.CSS, value="#search-btn, button[type=submit]"),
            "result_item":     Selector(type=SelectorType.CSS, value="#J_goodsList .item, .gl-item"),
            "result_title":    Selector(type=SelectorType.CSS, value=".p-name em, .name em, .p-name a em"),
            "result_url":      Selector(type=SelectorType.ATTRIBUTE, value=".p-name a[href*='item']"),
            "result_price":    Selector(type=SelectorType.CSS, value=".p-price strong, #price"),
            "result_sales":    Selector(type=SelectorType.CSS, value=".p-commit em, .commit-count"),
            "result_shop":     Selector(type=SelectorType.CSS, value=".p-shop a, .shop-name"),
            "result_image":    Selector(type=SelectorType.CSS, value=".p-img img, .gl-img img"),
            "next_page":       Selector(type=SelectorType.CSS, value=".pn-next, .page-next, .next-page"),
            "detail_title":    Selector(type=SelectorType.CSS, value=".sku-name, h1#name"),
            "detail_price":    Selector(type=SelectorType.CSS, value=".p-price strong, #price"),
            "detail_img":      Selector(type=SelectorType.CSS, value="#spec-n1 img, .gallery-list img"),
            "cart_button":     Selector(type=SelectorType.CSS, value=".add-cart, .btn-cart, #chooseBtn"),
        }
        for name, sel in selectors.items():
            self._selectors.register(domain, name, sel)

    async def execute(self, query: str, max_results: int = 20, **kwargs) -> EcommerceResults:
        self._record_start()
        self._config["last_query"] = query
        try:
            search_url = self._config["search_url"].format(query=query)
            await self._session.navigate(search_url)
            await self._wait.wait_for_selector(sel="#J_goodsList .item, .gl-item", timeout=20.0)
            await self._wait.wait_for_network_idle(timeout=15.0)
            results = await self._parse_jd_list(max_results)
            results.pattern_used = "JDSearchPattern"
            return self._record_latency(results.to_dict())
        except Exception as e:
            logger.error(f"JDSearchPattern failed: {e}")
            return EcommerceResults(success=False, query=query, error_message=str(e), pattern_used="JDSearchPattern")

    async def _parse_jd_list(self, max_results: int) -> EcommerceResults:
        items = await self._session.query_selector_all("#J_goodsList .item, .gl-item")
        results = []
        for item in items[:max_results]:
            try:
                row = await self._extract_jd_item(item)
                if row and row.title:
                    results.append(row)
            except Exception as e:
                logger.warning(f"parse jd item failed: {e}")
        return EcommerceResults(success=True, query=self._config.get("last_query", ""), results=results)

    async def _extract_jd_item(self, item) -> Optional[ProductResultItem]:
        import re
        title_el = await item.query_selector(".p-name em, .name em")
        title = await title_el.get_text() if title_el else ""

        url_el = await item.query_selector(".p-name a[href*='item'], .gl-i-wrap .p-name a")
        url = await url_el.get_attribute("href") if url_el else ""
        if url and url.startswith("//"):
            url = "https:" + url

        price_el = await item.query_selector(".p-price strong, .p-price i")
        price = await price_el.get_text() if price_el else ""
        price_num = 0.0
        if price:
            nums = re.findall(r'[\d,]+\.?\d*', price.replace(',', ''))
            price_num = float(nums[0]) if nums else 0.0

        shop_el = await item.query_selector(".p-shop a, .shop-name")
        shop = await shop_el.get_text() if shop_el else ""

        img_el = await item.query_selector(".p-img img")
        image = ""
        if img_el:
            image = await img_el.get_attribute("src") or await img_el.get_attribute("data-lazy-img") or ""

        sales_el = await item.query_selector(".p-commit em")
        sales = await sales_el.get_text() if sales_el else ""

        return ProductResultItem(
            title=title[:200],
            url=url,
            price=price,
            price_num=price_num,
            sales=sales,
            shop=shop[:100],
            image=image,
            source_domain=self._domain,
        )

    async def get_product_detail(self, url: str, **kwargs) -> Optional[Dict]:
        try:
            await self._session.navigate(url)
            await self._wait.wait_for_ready(timeout=30.0)
            js = """
            (() => {
              const result = {};
              const t = document.querySelector('.sku-name, h1#name');
              result.title = t ? t.innerText.trim() : '';
              const p = document.querySelector('.p-price strong, #price');
              result.price = p ? p.innerText.trim() : '';
              const op = document.querySelector('.p-price del, .original-price');
              result.original_price = op ? op.innerText.trim() : '';
              const cm = document.querySelector('#comment-count, .p-commit em');
              result.commit_count = cm ? cm.innerText.trim() : '';
              const shop = document.querySelector('#shopName span, .p-shop a');
              result.shop_name = shop ? shop.innerText.trim() : '';
              const imgs = [];
              document.querySelectorAll('#spec-n1 img, .gallery-list img').forEach(img => {
                const src = img.getAttribute('src') || img.getAttribute('data-src');
                if (src) imgs.push(src);
              });
              result.images = imgs;
              return result;
            })()
            """
            detail = await self._session.evaluate(js)
            if detail and detail.get("title"):
                detail["url"] = url
                detail["source"] = "jd"
                return detail
            return None
        except Exception as e:
            logger.error(f"JD get_product_detail failed: {e}")
            return None

    async def add_to_cart(self, product_url: str, quantity: int = 1) -> bool:
        try:
            await self._session.navigate(product_url)
            await self._wait.wait_for_selector(sel="#chooseBtn, .add-cart", timeout=15.0)
            await self._session.click("#chooseBtn, .add-cart")
            await self._wait.wait_for_network_idle(timeout=5.0)
            return True
        except Exception as e:
            logger.warning(f"JD add_to_cart failed: {e}")
            return False
