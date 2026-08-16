"""
request_fingerprint.py - 基础请求指纹隐藏模块

提供自定义User-Agent、Referer和请求头的功能，
通过HTTP请求拦截和CDP命令设置请求头。
"""
from __future__ import annotations

import random
import json
from typing import Optional, Dict, List
from dataclasses import dataclass, field


# 常见的User-Agent池
USER_AGENTS = [
    # Chrome Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    # Chrome macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    # Firefox Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0",
    # Firefox macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:125.0) Gecko/20100101 Firefox/125.0",
    # Edge Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0",
    # Safari macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15",
]

# 常见的Referer池
REFERERS = [
    "https://www.google.com/",
    "https://www.bing.com/",
    "https://www.baidu.com/",
    "https://www.youtube.com/",
    "https://twitter.com/",
    "https://news.ycombinator.com/",
]

# 常见Accept-Language
ACCEPT_LANGUAGES = [
    "zh-CN,zh;q=0.9,en;q=0.8",
    "en-US,en;q=0.9,zh;q=0.8",
    "zh-CN,zh",
    "en-US,en",
]


@dataclass
class RequestHeaders:
    """请求头配置"""
    user_agent: Optional[str] = None
    referer: Optional[str] = None
    accept_language: Optional[str] = None
    custom_headers: Dict[str, str] = field(default_factory=dict)
    
    def get_headers(self) -> Dict[str, str]:
        """获取完整的请求头字典"""
        headers = {
            "User-Agent": self.user_agent or self._random_ua(),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
            "Accept-Language": self.accept_language or self._random_lang(),
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none" if not self.referer else "same-origin",
            "Sec-Fetch-User": "?1",
            "Cache-Control": "max-age=0",
        }
        
        if self.referer:
            headers["Referer"] = self.referer
        
        headers.update(self.custom_headers)
        return headers
    
    def _random_ua(self) -> str:
        return random.choice(USER_AGENTS)
    
    def _random_lang(self) -> str:
        return random.choice(ACCEPT_LANGUAGES)
    
    def set_referer(self, url: str):
        """设置Referer"""
        self.referer = url
    
    def set_custom_header(self, name: str, value: str):
        """设置自定义请求头"""
        self.custom_headers[name] = value


class FingerprintManager:
    """指纹管理器"""
    
    def __init__(self):
        self._headers: Dict[str, RequestHeaders] = {}
        self._default_headers: Optional[RequestHeaders] = None
        self._enabled: bool = True
        
    def enable(self):
        """启用指纹隐藏"""
        self._enabled = True
        
    def disable(self):
        """禁用指纹隐藏"""
        self._enabled = False
        
    def is_enabled(self) -> bool:
        return self._enabled
    
    def set_default_headers(self, headers: RequestHeaders):
        """设置默认请求头"""
        self._default_headers = headers
        
    def get_headers_for_domain(self, domain: str) -> RequestHeaders:
        """获取指定域名的请求头配置"""
        if not self._enabled:
            return RequestHeaders()
        
        # 优先使用特定域名的配置
        if domain in self._headers:
            return self._headers[domain]
        
        # 使用默认配置
        if self._default_headers:
            return self._default_headers
        
        # 返回随机配置
        return RequestHeaders()
    
    def add_domain_config(self, domain: str, headers: RequestHeaders):
        """为指定域名添加配置"""
        self._headers[domain] = headers
    
    def remove_domain_config(self, domain: str):
        """移除指定域名的配置"""
        if domain in self._headers:
            del self._headers[domain]
    
    def clear_all(self):
        """清除所有配置"""
        self._headers.clear()
        self._default_headers = None
    
    def generate_random_headers(self) -> RequestHeaders:
        """生成随机请求头配置"""
        return RequestHeaders()
    
    def get_predefined_config(self, site_type: str) -> RequestHeaders:
        """获取预定义的配置"""
        configs = {
            "news": RequestHeaders(
                referer="https://www.google.com/",
                custom_headers={
                    "Accept": "text/html,application/xhtml+xml",
                }
            ),
            "ecommerce": RequestHeaders(
                referer="https://www.google.com/search?q=shopping",
                custom_headers={
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                }
            ),
            "social": RequestHeaders(
                referer="https://www.google.com/",
                custom_headers={
                    "Accept": "text/html,application/xhtml+xml",
                }
            ),
        }
        return configs.get(site_type, self.generate_random_headers())


# 全局单例
_manager: Optional[FingerprintManager] = None


def get_fingerprint_manager() -> FingerprintManager:
    """获取全局指纹管理器实例"""
    global _manager
    if _manager is None:
        _manager = FingerprintManager()
    return _manager


def reset_fingerprint_manager():
    """重置全局指纹管理器（用于测试）"""
    global _manager
    _manager = None