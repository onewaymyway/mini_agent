"""
taobao_search_pattern.py - 淘宝/天猫搜索交互模式

基于 EcommercePattern 实现淘宝/天猫的商品搜索与详情页抓取。
"""
from __future__ import annotations

import logging
from typing import Dict, Optional

from .ecommerce_pattern import EcommercePattern, EcommerceResults, ProductResultItem
from ..core.selector_manager import Selector, SelectorType
from ..product_parsers.taobao_parser import TaobaoProductParser

logger = logging.getLogger(__name__)


class TaobaoSearchPattern(EcommercePattern):
    """淘宝/天猫搜索模式"""

    def __init__(self, session, domain: str = "taobao.com", config: Optional[Dict] = None):
        tb_config = {
            "search_url": "https://s.taobao.com/search?q={query}",
            "cart_url": "https://cart.taobao.com/cart.htm",
            "max_pages": 3,
        }
        if config:
            tb_config.update(config)
        super().__init__(session, domain, tb_config)
        self._site_name = "taobao"
        self._parser = TaobaoProductParser()
        self._register_taobao_selectors()

    def _register_taobao_selectors(self):
        """注册淘宝专用选择器"""
        domain = self._domain
        selectors = {
            "search_input":    Selector(type=SelectorType.CSS, value="#q", description="淘宝搜索框"),
            "search_button":   Selector(type=SelectorType.CSS, value="#search-btn, button[type=submit]"),
            "result_item":     Selector(type=SelectorType.CSS, value=".item, .grid-item, [class*='product']"),
            "result_title":    Selector(type=SelectorType.CSS, value=".title a, .product-title a"),
            "result_url":      Selector(type=SelectorType.ATTRIBUTE, value="a[href*='item']"),
            "result_price":    Selector(type=SelectorType.CSS, value=".price, .g_price"),
            "result_sales":    Selector(type=SelectorType.CSS, value=".sales, .deal-num"),
            "result_shop":     Selector(type=SelectorType.CSS, value=".shop a, .seller-name"),
            "result_image":    Selector(type=SelectorType.CSS, value="img[data-src], img[src*='img']"),
            "next_page":       Selector(type=SelectorType.CSS, value="a.next, .next, [class*='next']"),
            "detail_title":    Selector(type=SelectorType.CSS, value=".tb-detail-hd h1, .item-title"),
            "detail_price":    Selector(type=SelectorType.CSS, value=".tb-price, [class*='price']"),
            "detail_img":      Selector(type=SelectorType.CSS, value=".tb-img img, .slider-img img"),
            "cart_button":     Selector(type=SelectorType.CSS, value=".J_AddCartBtn, .btn-cart, [class*='cart']"),
        }
        for name, sel in selectors.items():
            self._selectors.register(domain, name, sel)

    async def execute(self, query: str, max_results: int = 20, **kwargs) -> EcommerceResults:
        self._record_start()
        self._config["last_query"] = query
        try:
            # 1. 导航
            search_url = self._config["search_url"].format(query=query)
            await self._session.navigate(search_url)
            await self._wait.wait_for_selector(sel=".item", timeout=20.0)
            await self._wait.wait_for_network_idle(timeout=15.0)

            # 2. 检查反爬
            blocked = await self._check_blocked()
            if blocked:
                return EcommerceResults(success=False, query=query, error_message=blocked, pattern_used="TaobaoSearchPattern")

            # 3. 解析列表
            results = await self._parse_taobao_list(max_results)
            results.pattern_used = "TaobaoSearchPattern"
            return self._record_latency(results.to_dict())
        except Exception as e:
            logger.error(f"TaobaoSearchPattern failed: {e}")
            return EcommerceResults(success=False, query=query, error_message=str(e), pattern_used="TaobaoSearchPattern")

    async def _check_blocked(self) -> Optional[str]:
        """检查是否触发验证码或登录墙"""
        try:
            js = """
            (() => {
              const captcha = document.querySelector('#nc_1_wrapper, .geetest_panel, [class*="captcha"]');
              const loginWall = document.querySelector('.login-wrap, [class*="login"]');
              const blocked = document.querySelector('[class*="blocked"], [class*="forbidden"]');
              if (captcha) return 'captcha_detected';
              if (loginWall) return 'login_required';
              if (blocked) return 'blocked';
              return null;
            })()
            """
            result = await self._session.evaluate(js)
            return result if result else None
        except Exception:
            return None

    async def _parse_taobao_list(self, max_results: int) -> EcommerceResults:
        items = await self._session.query_selector_all(".item, .grid-item, [class*='product']")
        results = []
        for item in items[:max_results]:
            try:
                row = await self._extract_product_item(item)
                if row and row.title:
                    results.append(row)
            except Exception as e:
                logger.warning(f"parse taobao item failed: {e}")
        return EcommerceResults(success=True, query=self._config.get("last_query", ""), results=results)

    async def get_product_detail(self, url: str, **kwargs) -> Optional[Dict]:
        try:
            await self._session.navigate(url)
            await self._wait.wait_for_ready(timeout=30.0)
            js = """
            (() => {
              const result = {};
              const t = document.querySelector('.tb-detail-hd h1, .item-title');
              result.title = t ? t.innerText.trim() : '';
              const p = document.querySelector('.tb-price, [class*=\"price\"]');
              result.price = p ? p.innerText.trim() : '';
              const sp = document.querySelector('.tb-original-price');
              result.original_price = sp ? sp.innerText.trim() : '';
              const s = document.querySelector('.tb-sales');
              result.sales = s ? s.innerText.trim() : '';
              const shop = document.querySelector('.tb-shop a');
              result.shop = shop ? shop.innerText.trim() : '';
              const imgs = [];
              document.querySelectorAll('.tb-img img, .slider-img img').forEach(img => {
                const src = img.getAttribute('data-src') || img.getAttribute('src');
                if (src) imgs.push(src);
              });
              result.images = imgs;
              return result;
            })()
            """
            detail = await self._session.evaluate(js)
            if detail and detail.get("title"):
                detail["url"] = url
                detail["source"] = "taobao"
                return detail
            return None
        except Exception as e:
            logger.error(f"get_product_detail failed: {e}")
            return None

    async def add_to_cart(self, product_url: str, quantity: int = 1) -> bool:
        try:
            await self._session.navigate(product_url)
            await self._wait.wait_for_selector(sel=".J_AddCartBtn, .btn-cart", timeout=15.0)
            await self._session.click(".J_AddCartBtn, .btn-cart")
            await self._wait.wait_for_network_idle(timeout=5.0)
            return True
        except Exception as e:
            logger.warning(f"add_to_cart failed: {e}")
            return False
