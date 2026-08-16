"""
request_interceptor.py - CDP请求拦截器

通过CDP Network.enable和Network.requestWillBeSent事件拦截请求，
动态注入自定义请求头。
"""
from __future__ import annotations

import logging
from typing import Optional, Dict, Callable
from dataclasses import dataclass
import re

logger = logging.getLogger(__name__)


@dataclass
class InterceptedRequest:
    """被拦截的请求"""
    request_id: str
    url: str
    method: str
    headers: Dict[str, str]
    frame_id: Optional[str] = None
    timestamp: Optional[float] = None
    
    def domain(self) -> str:
        """提取域名"""
        try:
            # 简单提取域名
            match = re.match(r'https?://([^/]+)', self.url)
            if match:
                return match.group(1)
        except:
            pass
        return ""
    
    def set_header(self, name: str, value: str):
        """设置请求头"""
        self.headers[name] = value
    
    def remove_header(self, name: str):
        """移除请求头"""
        if name in self.headers:
            del self.headers[name]


class RequestInterceptor:
    """请求拦截器"""
    
    def __init__(self, cdp_session):
        self._cdp = cdp_session
        self._enabled = False
        self._handlers: Dict[str, Callable[[InterceptedRequest], None]] = {}
        self._header_overrides: Dict[str, Dict[str, str]] = {}  # domain -> headers
        
    def enable(self):
        """启用拦截器"""
        if not self._enabled:
            self._cdp.send('Network.enable')
            self._cdp.subscribe('Network.requestWillBeSent', self._on_request)
            self._enabled = True
            logger.info("请求拦截器已启用")
    
    def disable(self):
        """禁用拦截器"""
        if self._enabled:
            self._cdp.unsubscribe('Network.requestWillBeSent', self._on_request)
            self._cdp.send('Network.disable')
            self._enabled = False
            logger.info("请求拦截器已禁用")
    
    def is_enabled(self) -> bool:
        return self._enabled
    
    def add_header_override(self, domain: str, headers: Dict[str, str]):
        """为指定域名添加请求头覆盖"""
        if domain not in self._header_overrides:
            self._header_overrides[domain] = {}
        self._header_overrides[domain].update(headers)
    
    def remove_header_override(self, domain: str):
        """移除指定域名的请求头覆盖"""
        if domain in self._header_overrides:
            del self._header_overrides[domain]
    
    def clear_header_overrides(self):
        """清除所有请求头覆盖"""
        self._header_overrides.clear()
    
    def _on_request(self, params: dict):
        """处理请求事件"""
        request = params.get('request', {})
        request_id = params.get('requestId', '')
        url = request.get('url', '')
        method = request.get('method', 'GET')
        headers = request.get('headers', {})
        
        # 创建拦截对象
        intercepted = InterceptedRequest(
            request_id=request_id,
            url=url,
            method=method,
            headers=headers,
            frame_id=params.get('frameId'),
            timestamp=params.get('timestamp')
        )
        
        # 应用域名级别的头覆盖
        domain = intercepted.domain()
        if domain in self._header_overrides:
            for name, value in self._header_overrides[domain].items():
                intercepted.set_header(name, value)
        
        # 触发注册的处理函数
        for handler in self._handlers.values():
            try:
                handler(intercepted)
            except Exception as e:
                logger.warning(f"请求拦截处理器异常: {e}")
    
    def register_handler(self, name: str, handler: Callable[[InterceptedRequest], None]):
        """注册请求处理函数"""
        self._handlers[name] = handler
    
    def unregister_handler(self, name: str):
        """注销请求处理函数"""
        if name in self._handlers:
            del self._handlers[name]
    
    def clear_handlers(self):
        """清除所有处理函数"""
        self._handlers.clear()


class HeaderInjectionInterceptor(RequestInterceptor):
    """请求头注入拦截器（更简洁的接口）"""
    
    def __init__(self, cdp_session):
        super().__init__(cdp_session)
        self._applied_requests: set = set()
    
    def apply_headers(self, domain: str, headers: Dict[str, str]):
        """应用请求头到指定域名的请求"""
        self.add_header_override(domain, headers)
        
        # 注册一个一次性的处理函数来立即应用
        def inject_handler(request: InterceptedRequest):
            if request.domain() == domain and request.request_id not in self._applied_requests:
                for name, value in headers.items():
                    request.set_header(name, value)
                self._applied_requests.add(request.request_id)
        
        self.register_handler(f"inject_{domain}", inject_handler)
    
    def remove_headers(self, domain: str):
        """移除指定域名的请求头"""
        self.remove_header_override(domain)
        # 注销处理函数
        handler_name = f"inject_{domain}"
        if handler_name in self._handlers:
            self.unregister_handler(handler_name)