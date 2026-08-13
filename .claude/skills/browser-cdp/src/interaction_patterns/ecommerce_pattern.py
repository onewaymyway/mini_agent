"""
ecommerce_pattern.py - 电商交互模式基类

覆盖电商核心场景：商品搜索、列表浏览、详情页抓取、购物车操作。
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from ._base import InteractionPattern, SearchResultItem, SearchResults
from ..core.selector_manager import SelectorManager, Selector, SelectorType
from ..product_parsers.base import ProductData

logger = logging.getLogger(__name__)


@dataclass
class ProductResultItem:
    """电商搜索结果项"""
    title: str = ""
    url: str = ""
    price: str = ""
    price_num: float = 0.0
    sales: str = ""
    sales_count: int = 0
    shop: str = ""
    location: str = ""
    image: str = ""
    tags: List[str] = field(default_factory=list)
    source_domain: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict:
        return {
            "title": self.title,
            "url": self.url,
            "price": self.price,
            "price_num": self.price_num,
            "sales": self.sales,
            "sales_count": self.sales_count,
            "shop": self.shop,
            "location": self.location,
            "image": self.image,
            "tags": self.tags,
            "source_domain": self.source_domain,
            "metadata": self.metadata,
        }


@dataclass
class EcommerceResults:
    """电商搜索结果集"""
    success: bool = False
    query: str = ""
    results: List[ProductResultItem] = field(default_factory=list)
    total_count: Optional[int] = None
    error_message: Optional[str] = None
    latency_ms: float = 0.0
    pattern_used: str = ""

    @property
    def is_empty(self) -> bool:
        return len(self.results) == 0

    def to_dict(self) -> Dict:
        return {
            "success": self.success,
            "query": self.query,
            "total_count": self.total_count,
            "latency_ms": self.latency_ms,
            "pattern": self.pattern_used,
            "results": [r.to_dict() for r in self.results],
            "error": self.error_message,
        }


class EcommercePattern(InteractionPattern):
    """电商交互模式基类 - 搜索/浏览/购物车"""

    # 电商场景枚举
    SCENE_TYPES = {
        "search":     "商品搜索（关键词→列表）",
        "browse":     "商品详情浏览（单页）",
        "cart":       "购物车操作（添加/查看/结算）",
        "compare":    "多商品比价",
        "monitor":    "价格监控（定时追踪）",
    }

    # 通用选择器名称（子类可覆盖）
    DEFAULT_SELECTORS = {
        # 搜索
        "search_input":    Selector(type=SelectorType.CSS, value="input[type=text], input[name=q], input[name=keyword]"),
        "search_button":   Selector(type=SelectorType.CSS, value="button[type=submit], input[type=submit], .search-btn, [class*='search'] button"),
        # 列表
        "result_item":     Selector(type=SelectorType.CSS, value=".item, .product, .goods-item, [class*='product']"),
        "result_title":    Selector(type=SelectorType.CSS, value=".title a, .product-title a, [class*='title'] a"),
        "result_url":      Selector(type=SelectorType.ATTRIBUTE, value="a[href*='item'], a[href*='product']"),
        "result_price":    Selector(type=SelectorType.CSS, value=".price, [class*='price']"),
        "result_sales":    Selector(type=SelectorType.CSS, value=".sales, [class*='sales'], .deal-num"),
        "result_shop":     Selector(type=SelectorType.CSS, value=".shop, [class*='shop'], .seller-name"),
        "result_image":    Selector(type=SelectorType.CSS, value="img[data-src], img[src*='img'], .product-img img"),
        # 翻页
        "next_page":       Selector(type=SelectorType.CSS, value="a.next, .next-page, [class*='pagination'] a:last-child"),
        # 详情
        "detail_title":    Selector(type=SelectorType.CSS, value="h1, .sku-name, [class*='title'] h1"),
        "detail_price":    Selector(type=SelectorType.CSS, value=".tb-price, .p-price strong, [class*='price']"),
        "detail_specs":    Selector(type=SelectorType.CSS, value=".tb-props, .parameter2, [class*='spec']"),
        "detail_img":      Selector(type=SelectorType.CSS, value=".tb-img img, #spec-n1 img, .gallery-list img"),
        # 购物车
        "cart_button":     Selector(type=SelectorType.CSS, value=".add-cart, .btn-cart, [class*='cart'] button, [class*='buy'] button"),
        "cart_count":      Selector(type=SelectorType.CSS, value=".cart-count, [class*='cart'] .num, .shopping-cart .count"),
    }

    def __init__(self, session, domain: str, config: Optional[Dict] = None):
        super().__init__(session, domain, config)
        self._site_name = domain.split(".")[-2] if "." in domain else domain
        self._register_default_selectors()

    def _register_default_selectors(self):
        """注册默认选择器到当前域名"""
        for name, sel in self.DEFAULT_SELECTORS.items():
            if not self._selectors.has_domain(self._domain):
                self._selectors.register(self._domain, name, sel)

    async def execute(self, query: str, **kwargs) -> EcommerceResults:
        raise NotImplementedError("子类必须实现 execute()")

    # ─── 搜索流程 ───
    async def search_products(
        self,
        query: str,
        max_results: int = 20,
        sort: Optional[str] = None,
        **kwargs,
    ) -> EcommerceResults:
        """执行商品搜索（子类优先重写 execute）"""
        self._record_start()
        try:
            await self._navigate_to_search_page(query, sort)
            await self._wait.wait_for_network_idle(timeout=15.0)
            results = await self._parse_product_list(max_results)
            results.pattern_used = f"{self.__class__.__name__}"
            return self._record_latency(results.to_dict())
        except Exception as e:
            logger.error(f"{self.__class__.__name__}.search_products failed: {e}")
            return EcommerceResults(success=False, query=query, error_message=str(e), pattern_used=self.__class__.__name__)

    async def _navigate_to_search_page(self, query: str, sort: Optional[str] = None):
        search_url = self._config.get("search_url", f"https://{self._domain}/search?q={query}")
        await self._session.navigate(search_url)

    async def _parse_product_list(self, max_results: int) -> EcommerceResults:
        items = await self._session.query_selector_all(".item, .product, .gl-item, [class*='product']")
        results = []
        for item in items[:max_results]:
            try:
                row = await self._extract_product_item(item)
                if row and row.title:
                    results.append(row)
            except Exception as e:
                logger.warning(f"Failed to extract product item: {e}")
        return EcommerceResults(success=True, query=self._config.get("last_query", ""), results=results)

    async def _extract_product_item(self, item) -> Optional[ProductResultItem]:
        sel = SelectorManager.get_instance()

        # 标题
        title_el = await item.query_selector(sel.resolve(self._domain, "result_title").value)
        title = await title_el.get_text() if title_el else ""

        # URL
        url_el = await item.query_selector(sel.resolve(self._domain, "result_url").value)
        url = await url_el.get_attribute("href") if url_el else ""
        if url and url.startswith("//"):
            url = "https:" + url

        # 价格
        price_el = await item.query_selector(sel.resolve(self._domain, "result_price").value)
        price = await price_el.get_text() if price_el else ""

        # 销量
        sales_el = await item.query_selector(sel.resolve(self._domain, "result_sales").value)
        sales = await sales_el.get_text() if sales_el else ""

        # 店铺
        shop_el = await item.query_selector(sel.resolve(self._domain, "result_shop").value)
        shop = await shop_el.get_text() if shop_el else ""

        # 图片
        img_el = await item.query_selector(sel.resolve(self._domain, "result_image").value)
        image = ""
        if img_el:
            image = await img_el.get_attribute("data-src") or await img_el.get_attribute("src") or ""

        import re
        price_num = 0.0
        if price:
            nums = re.findall(r'[\d,]+\.?\d*', price.replace(',', ''))
            price_num = float(nums[0]) if nums else 0.0

        sales_count = 0
        if sales:
            m = re.search(r'([\d.]+)\s*([万千百]?)', sales)
            if m:
                sales_count = int(float(m.group(1)) * {'':1,'百':100,'千':1000,'万':10000}.get(m.group(2),1))

        return ProductResultItem(
            title=title[:200],
            url=url,
            price=price,
            price_num=price_num,
            sales=sales,
            sales_count=sales_count,
            shop=shop[:100],
            image=image,
            source_domain=self._domain,
        )

    # ─── 商品详情 ───
    async def get_product_detail(self, url: str, **kwargs) -> Optional[ProductData]:
        """导航到商品详情页并提取结构化数据"""
        try:
            await self._session.navigate(url)
            await self._wait.wait_for_ready(timeout=30.0)
            return await self._extract_product_detail()
        except Exception as e:
            logger.error(f"get_product_detail failed for {url}: {e}")
            return None

    async def _extract_product_detail(self) -> Optional[ProductData]:
        """从当前页面提取商品详情（子类可覆盖）"""
        raise NotImplementedError("子类必须实现 _extract_product_detail()")

    # ─── 购物车 ───
    async def add_to_cart(self, product_url: str, quantity: int = 1) -> bool:
        """添加商品到购物车"""
        try:
            await self._session.navigate(product_url)
            await self._wait.wait_for_selector(sel=self._get_selector_value("cart_button"), timeout=15.0)
            await self._session.click(self._get_selector_value("cart_button"))
            await asyncio.sleep(1.0)
            count_el = await self._session.query_selector(self._get_selector_value("cart_count"))
            if count_el:
                count_text = await count_el.get_text()
                logger.info(f"Cart count after add: {count_text}")
            return True
        except Exception as e:
            logger.warning(f"add_to_cart failed: {e}")
            return False

    async def view_cart(self) -> Dict:
        """查看购物车内容"""
        try:
            cart_url = self._config.get("cart_url", f"https://{self._domain}/cart")
            await self._session.navigate(cart_url)
            await self._wait.wait_for_network_idle(timeout=15.0)
            return {"success": True, "cart_url": cart_url}
        except Exception as e:
            logger.warning(f"view_cart failed: {e}")
            return {"success": False, "error": str(e)}

    # ─── 工具方法 ───
    def _get_selector_value(self, name: str) -> Optional[str]:
        sel = self._selectors.resolve(self._domain, name)
        return sel.value if sel else None

    def _record_start(self):
        import time
        self._start_time = time.time()

    def _record_latency(self, results_dict: Dict) -> Dict:
        import time
        elapsed = (time.time() - self._start_time) * 1000
        results_dict["latency_ms"] = round(elapsed, 2)
        return results_dict
