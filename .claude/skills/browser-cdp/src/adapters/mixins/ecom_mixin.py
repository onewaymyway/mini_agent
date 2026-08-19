"""
src/adapters/mixins/ecom_mixin.py

电商类站点通用混入：签名 API 拦截、滑块验证码处理、价格快照。
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class EcomMixin:
    """
    电商类站点通用混入，提供以下能力：
    1. 签名 API 拦截：监听匹配的 XHR/Fetch 响应，直接获取 JSON
    2. 滑块验证码检测：检测页面中的滑块验证元素并触发处理
    3. 价格快照记录：为每条结果附加价格和时间戳信息
    """
    
    DEFAULT_API_PATTERNS = [
        "/api/goods", "/api/product", "/search", "/item",
        "/rest/search", "/search?q=", "/fetchSearch",
        "/pc/search", "/v3/search", "/openapi",
    ]
    
    SLIDER_CAPTCHA_SELECTORS = [
        ".slider-captcha", ".geetest_wrap", "#nc_1_wrapper",
        ".tc-slide-wrap", ".slide-container", "[class*='slider']",
        "#geetest-box", ".geetest_btn",
    ]
    
    def __init__(self, **kwargs):
        self._api_patterns = kwargs.get("api_patterns", self.DEFAULT_API_PATTERNS)
        self._captcha_selectors = kwargs.get("captcha_selectors", self.SLIDER_CAPTCHA_SELECTORS)
        self._price_fields = kwargs.get("price_fields", ["price", "salePrice", "marketPrice", "jdPrice"])
    
    async def intercept_api_response(self, page, api_pattern: str = None, timeout: float = 20.0) -> Optional[Dict]:
        pattern = api_pattern or (getattr(self, "descriptor", None).signature_patterns[0] if getattr(self, "descriptor", None) else None)
        if not pattern:
            logger.warning("未指定 API 拦截模式")
            return None
        try:
            async with page.expect_response(
                lambda r: pattern in r.url and r.request.method.upper() in ("GET", "POST"),
                timeout=int(timeout * 1000)
            ) as resp_info:
                await self._trigger_search_action(page)
            response = await resp_info.value
            if response.status == 200:
                try:
                    return await response.json()
                except Exception as e:
                    logger.warning(f"API 响应解析失败: {e}")
        except asyncio.TimeoutError:
            logger.warning(f"API 拦截超时: {pattern}")
        except Exception as e:
            logger.warning(f"API 拦截异常: {e}")
        return None
    
    async def _trigger_search_action(self, page, **kwargs) -> None:
        selectors = ["button[type='submit']", ".search-btn", "input[type='submit']"]
        for sel in selectors:
            el = await page.query_selector(sel)
            if el:
                await el.click()
                return
        await page.keyboard.press("Enter")
    
    async def detect_slider_captcha(self, page) -> bool:
        for selector in self._captcha_selectors:
            el = await page.query_selector(selector)
            if el and await el.is_visible():
                logger.info(f"检测到滑块验证码: {selector}")
                return True
        return False
    
    async def handle_captcha_wait(self, page, timeout: float = 60.0) -> bool:
        start = asyncio.get_event_loop().time()
        while True:
            if not await self.detect_slider_captcha(page):
                return True
            if asyncio.get_event_loop().time() - start >= timeout:
                logger.warning(f"验证码处理超时 ({timeout}s)")
                return False
            await asyncio.sleep(1.0)
    
    async def extract_price_snapshot(self, page, item_selector: str = ".product-item") -> Dict[str, Any]:
        try:
            price_data = await page.evaluate(f'''
                () => {{
                    const prices = {json.dumps(self._price_fields)};
                    const result = {{}};
                    for (const key of prices) {{
                        const el = document.querySelector(`.price-${key}`)
                            || document.querySelector(`[data-price="{key}"]`);
                        if (el) {{
                            const num = parseFloat(el.textContent.trim().replace(/[元￥$\s,]/g, ''));
                            if (!isNaN(num)) result[key] = num;
                        }}
                    }}
                    result.snapshot_at = new Date().toISOString();
                    return result;
                }}
            ''')
            return price_data
        except Exception as e:
            logger.warning(f"价格快照提取失败: {e}")
            return {"snapshot_at": datetime.now().isoformat()}
    
    async def extract_product_list(self, page, item_selector: str = ".result-item, .product-card") -> List[Dict]:
        items = await page.query_selector_all(item_selector)
        results = []
        for el in items:
            try:
                data = await el.evaluate('''
                    (el) => {
                        const link = el.querySelector('a[href]');
                        const titleEl = el.querySelector('.title, h3, .name');
                        const priceEl = el.querySelector('.price, [class*="price"]');
                        return {
                            title: titleEl ? titleEl.textContent.trim() : '',
                            url: link ? link.getAttribute('href') : '',
                            price: priceEl ? priceEl.textContent.trim() : '',
                        };
                    }
                ''')
                if data["title"] or data["url"]:
                    results.append(data)
            except Exception:
                pass
        return results
    
    def register_hooks(self, descriptor) -> None:
        if not hasattr(descriptor, "hooks") or descriptor.hooks is None:
            descriptor.hooks = {}
        descriptor.hooks["on_captcha"] = self.detect_slider_captcha
        descriptor.hooks["post_extract"] = self.extract_price_snapshot


__all__ = ["EcomMixin"]
