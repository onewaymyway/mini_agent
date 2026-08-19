"""
src/adapters/mixins/csrf_handler.py

CSRF Token 处理器：自动提取和注入 CSRF 凭证。
支持 meta tag、Cookie、X-Header 等多种方式。
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class CsrfHandler:
    """
    CSRF Token 处理器。
    
    支持方式：
    1. meta tag: <meta name="csrf-token" content="...">
    2. Cookie: 读取指定名称的 Cookie
    3. Form field: 从表单隐藏字段提取
    4. Window global: window.__CSRF_TOKEN__
    """
    
    META_SELECTORS = [
        'meta[name="csrf-token"]',
        'meta[name="csrf_token"]',
        'meta[name="X-CSRF-Token"]',
    ]
    
    FORM_SELECTORS = [
        'input[name="_token"]',
        'input[name="csrf_token"]',
        'input[name="X-CSRFToken"]',
        'input[type="hidden"][name*="csrf"]',
    ]
    
    COOKIE_NAMES = [
        "csrf_token", "csrf", "_csrf", "XSRF-TOKEN", "X-CSRF-Token",
        "csrftoken", "JWT",
    ]
    
    HEADER_NAMES = [
        "X-CSRF-Token", "X-CSRF-Token", "X-CSRFToken",
        "X-XSRF-Token", "Authorization", "Authorization",
    ]
    
    def __init__(self, cookie_name: str = None, header_name: str = None):
        self._token: Optional[str] = None
        self._cookie_name = cookie_name or "csrf_token"
        self._header_name = header_name or "X-CSRF-Token"
    
    async def extract_from_page(self, page) -> Optional[str]:
        """从页面提取 CSRF Token，返回最优结果"""
        # 优先级：meta > window global > form > cookie
        
        # 1. Meta tag
        token = await self._extract_meta(page)
        if token:
            self._token = token
            logger.debug(f"从 meta tag 提取 CSRF Token: {token[:8]}...")
            return token
        
        # 2. Window global
        token = await self._extract_global(page)
        if token:
            self._token = token
            return token
        
        # 3. Form field
        token = await self._extract_form(page)
        if token:
            self._token = token
            return token
        
        # 4. Cookie
        token = await self._extract_cookie(page)
        if token:
            self._token = token
            return token
        
        logger.warning("未能提取到 CSRF Token")
        return None
    
    async def _extract_meta(self, page) -> Optional[str]:
        for selector in self.META_SELECTORS:
            val = await page.evaluate(f"document.querySelector('{selector}')?.content")
            if val:
                return val.strip()
        return None
    
    async def _extract_global(self, page) -> Optional[str]:
        for key in ["__CSRF_TOKEN__", "csrfToken", "_csrf", "CSRF_TOKEN"]:
            try:
                val = await page.evaluate(f"window.{key}")
                if val and isinstance(val, str):
                    return val
            except Exception:
                pass
        return None
    
    async def _extract_form(self, page) -> Optional[str]:
        for selector in self.FORM_SELECTORS:
            val = await page.evaluate(f"document.querySelector('{selector}')?.value")
            if val:
                return val.strip()
        return None
    
    async def _extract_cookie(self, page) -> Optional[str]:
        cookies = await page.context.cookies()
        for c in cookies:
            if c["name"].lower() == self._cookie_name.lower():
                return c["value"]
        return None
    
    async def inject_into_request(self, page, method: str = "GET",
                                   url: str = None, body: Dict = None,
                                   extra_headers: Dict = None) -> Dict[str, str]:
        """
        将 CSRF Token 注入到请求头中。
        
        Returns:
            headers 字典，包含 CSRF Token 相关的 header
        """
        if not self._token:
            await self.extract_from_page(page)
        
        headers = dict(extra_headers or {})
        if self._token:
            headers[self._header_name] = self._token
        return headers
    
    async def build_form_payload(self, page, base_payload: Dict = None) -> Dict:
        """
        将 CSRF Token 添加到表单数据中。
        适用于 POST 表单提交。
        """
        payload = dict(base_payload or {})
        if not self._token:
            await self.extract_from_page(page)
        
        if self._token:
            payload["_token"] = self._token
            payload["csrf_token"] = self._token
        return payload
    
    @property
    def token(self) -> Optional[str]:
        return self._token


__all__ = ["CsrfHandler"]
