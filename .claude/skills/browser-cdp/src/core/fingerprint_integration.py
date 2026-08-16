"""
fingerprint_integration.py - 指纹隐藏与浏览器集成模块

将请求指纹管理器和请求拦截器集成到CDP浏览器会话中。
"""
from __future__ import annotations

import logging
from typing import Optional, Dict, List
from dataclasses import dataclass

from .request_fingerprint import (
    FingerprintManager,
    RequestHeaders,
    get_fingerprint_manager,
    reset_fingerprint_manager,
)
from .request_interceptor import RequestInterceptor, HeaderInjectionInterceptor

logger = logging.getLogger(__name__)


@dataclass
class FingerprintConfig:
    """指纹配置"""
    user_agent: Optional[str] = None
    referer: Optional[str] = None
    accept_language: Optional[str] = None
    custom_headers: Dict[str, str] = None
    auto_randomize: bool = True
    
    def __post_init__(self):
        if self.custom_headers is None:
            self.custom_headers = {}
    
    def to_request_headers(self) -> RequestHeaders:
        """转换为RequestHeaders对象"""
        return RequestHeaders(
            user_agent=self.user_agent,
            referer=self.referer,
            accept_language=self.accept_language,
            custom_headers=self.custom_headers,
        )


class FingerprintBrowserIntegration:
    """浏览器指纹集成器"""
    
    def __init__(self, cdp_session, config: Optional[FingerprintConfig] = None):
        self._cdp = cdp_session
        self._config = config or FingerprintConfig()
        self._manager = get_fingerprint_manager()
        self._interceptor: Optional[HeaderInjectionInterceptor] = None
        self._initialized = False
        
    def initialize(self):
        """初始化集成"""
        if self._initialized:
            return
        
        # 创建请求拦截器
        self._interceptor = HeaderInjectionInterceptor(self._cdp)
        
        # 设置默认配置
        default_headers = self._config.to_request_headers()
        self._manager.set_default_headers(default_headers)
        
        # 启用拦截器
        self._interceptor.enable()
        
        self._initialized = True
        logger.info("指纹集成器已初始化")
    
    def is_initialized(self) -> bool:
        return self._initialized
    
    def set_config(self, config: FingerprintConfig):
        """设置指纹配置"""
        self._config = config
        if self._initialized:
            default_headers = config.to_request_headers()
            self._manager.set_default_headers(default_headers)
    
    def get_config(self) -> FingerprintConfig:
        """获取当前配置"""
        return self._config
    
    def apply_to_domain(self, domain: str, headers: RequestHeaders):
        """为指定域名应用请求头"""
        self._manager.add_domain_config(domain, headers)
        
        if self._interceptor and self._interceptor.is_enabled():
            self._interceptor.apply_headers(domain, headers.get_headers())
    
    def remove_from_domain(self, domain: str):
        """移除指定域名的请求头"""
        self._manager.remove_domain_config(domain)
        
        if self._interceptor and self._interceptor.is_enabled():
            self._interceptor.remove_headers(domain)
    
    def randomize(self):
        """随机化当前配置"""
        random_headers = self._manager.generate_random_headers()
        self._config = FingerprintConfig(
            user_agent=random_headers.user_agent,
            referer=random_headers.referer,
            accept_language=random_headers.accept_language,
            custom_headers=dict(random_headers.custom_headers),
        )
        self._manager.set_default_headers(random_headers)
        
        # 重新应用到所有已配置的域名
        for domain, headers in list(self._manager._headers.items()):
            self.apply_to_domain(domain, headers)
    
    def clear(self):
        """清除所有配置"""
        if self._interceptor:
            self._interceptor.disable()
            self._interceptor.clear_header_overrides()
            self._interceptor.clear_handlers()
        
        self._manager.clear_all()
        self._initialized = False
    
    def get_status(self) -> Dict:
        """获取当前状态"""
        return {
            "initialized": self._initialized,
            "enabled": self._manager.is_enabled() if hasattr(self._manager, 'is_enabled') else True,
            "user_agent": self._config.user_agent or "random",
            "referer": self._config.referer or "none",
            "custom_headers_count": len(self._config.custom_headers),
            "domain_configs": len(self._manager._headers),
        }


class MultiDomainFingerprintManager:
    """多域名指纹管理器"""
    
    def __init__(self, cdp_session):
        self._cdp = cdp_session
        self._integrations: Dict[str, FingerprintBrowserIntegration] = {}
        self._default_config: Optional[FingerprintConfig] = None
    
    def set_default_config(self, config: FingerprintConfig):
        """设置默认配置"""
        self._default_config = config
    
    def get_or_create_integration(self, domain: str) -> FingerprintBrowserIntegration:
        """获取或创建指定域名的集成器"""
        if domain not in self._integrations:
            config = self._default_config
            integration = FingerprintBrowserIntegration(self._cdp, config)
            integration.initialize()
            self._integrations[domain] = integration
        return self._integrations[domain]
    
    def remove_integration(self, domain: str):
        """移除指定域名的集成器"""
        if domain in self._integrations:
            self._integrations[domain].clear()
            del self._integrations[domain]
    
    def clear_all(self):
        """清除所有集成器"""
        for integration in self._integrations.values():
            integration.clear()
        self._integrations.clear()
        self._default_config = None
    
    def get_all_status(self) -> Dict[str, Dict]:
        """获取所有域名的状态"""
        return {
            domain: integration.get_status()
            for domain, integration in self._integrations.items()
        }


# 便捷函数
def create_fingerprint_integration(cdp_session, config: Optional[FingerprintConfig] = None) -> FingerprintBrowserIntegration:
    """创建指纹集成器"""
    return FingerprintBrowserIntegration(cdp_session, config)


def create_multi_domain_manager(cdp_session) -> MultiDomainFingerprintManager:
    """创建多域名管理器"""
    return MultiDomainFingerprintManager(cdp_session)