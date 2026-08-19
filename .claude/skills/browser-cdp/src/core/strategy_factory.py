"""
src/core/strategy_factory.py

策略工厂：根据网站分类和 CapabilityDescriptor 自动选择最优策略组合。
涵盖等待策略、反检测策略、代理策略、验证码处理策略等。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class StrategyConfig:
    """
    策略配置：一组策略参数的集合。
    """
    # 等待策略
    wait_strategy: str = "networkidle"
    wait_timeout: float = 30.0
    
    # 反检测策略
    stealth: bool = False
    stealth_scripts: List[str] = field(default_factory=list)
    
    # 代理策略
    proxy_required: bool = False
    proxy_geo: str = ""           # CN / US / JP / ...
    proxy_rotation: bool = True
    
    # 频率控制
    delay_range: Tuple[float, float] = (2.0, 5.0)
    rate_limit_per_minute: int = 10
    
    # 验证码策略
    captcha_types: List[str] = field(default_factory=list)
    captcha_auto_handle: bool = False
    captcha_max_retries: int = 3
    
    # API 拦截策略（电商签名等）
    api_patterns: List[str] = field(default_factory=list)
    api_intercept_enabled: bool = False
    
    # CSRF 策略
    csrf_token_selector: str = "meta[name=csrf-token]"
    csrf_cookie_name: str = "csrf_token"
    
    # 编码策略
    encoding: str = "utf-8"
    encoding_fallback: str = "gbk"
    
    # 超时
    navigation_timeout: float = 30.0
    request_timeout: float = 15.0
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "wait_strategy": self.wait_strategy,
            "wait_timeout": self.wait_timeout,
            "stealth": self.stealth,
            "proxy_required": self.proxy_required,
            "proxy_geo": self.proxy_geo,
            "delay_range": list(self.delay_range),
            "rate_limit_per_minute": self.rate_limit_per_minute,
            "captcha_types": self.captcha_types,
            "captcha_auto_handle": self.captcha_auto_handle,
            "api_intercept_enabled": self.api_intercept_enabled,
            "api_patterns": self.api_patterns,
            "csrf_token_selector": self.csrf_token_selector,
            "encoding": self.encoding,
            "navigation_timeout": self.navigation_timeout,
            "request_timeout": self.request_timeout,
        }


class StrategyFactory:
    """
    策略工厂：根据网站分类和 CapabilityDescriptor 自动组合策略。
    
    使用示例：
        descriptor = CapabilityDescriptor(site_id="gov", domain="gov.cn", name="中国政府网", ...)
        config = StrategyFactory.get_strategies(descriptor)
        page = await browser.goto(url, stealth=config.stealth, wait_strategy=config.wait_strategy)
    """
    
    # 分类 → 默认策略映射
    STRATEGY_MAP: Dict[str, Dict[str, Any]] = {
        # 电商类：高反爬，需 stealth + 代理
        "ECOM": {
            "wait_strategy": "networkidle",
            "stealth": True,
            "proxy_required": True,
            "proxy_geo": "CN",
            "delay_range": (3.0, 6.0),
            "rate_limit_per_minute": 6,
            "captcha_types": ["slider", "click"],
            "captcha_auto_handle": True,
            "api_intercept_enabled": True,
            "navigation_timeout": 45.0,
        },
        # 政府类：低反爬，直接抓取
        "GOV": {
            "wait_strategy": "selector",
            "stealth": False,
            "proxy_required": False,
            "proxy_geo": "",
            "delay_range": (1.0, 3.0),
            "rate_limit_per_minute": 20,
            "captcha_types": [],
            "captcha_auto_handle": False,
            "api_intercept_enabled": False,
            "navigation_timeout": 20.0,
        },
        # 后台系统：需登录态，无公网反爬
        "ADMIN": {
            "wait_strategy": "selector",
            "stealth": False,
            "proxy_required": False,
            "proxy_geo": "",
            "delay_range": (1.0, 3.0),
            "rate_limit_per_minute": 15,
            "captcha_types": [],
            "captcha_auto_handle": False,
            "api_intercept_enabled": True,
            "csrf_token_selector": "meta[name=csrf-token]",
            "navigation_timeout": 30.0,
        },
        # 社交类：中等反爬，需 stealth + 代理
        "SOCIAL": {
            "wait_strategy": "networkidle",
            "stealth": True,
            "proxy_required": True,
            "proxy_geo": "CN",
            "delay_range": (3.0, 6.0),
            "rate_limit_per_minute": 8,
            "captcha_types": ["slider"],
            "captcha_auto_handle": True,
            "api_intercept_enabled": True,
            "navigation_timeout": 30.0,
        },
        # 新闻类：低反爬
        "NEWS": {
            "wait_strategy": "networkidle",
            "stealth": False,
            "proxy_required": False,
            "proxy_geo": "",
            "delay_range": (1.0, 3.0),
            "rate_limit_per_minute": 20,
            "captcha_types": [],
            "captcha_auto_handle": False,
            "api_intercept_enabled": False,
            "navigation_timeout": 20.0,
        },
        # 金融类：中等反爬
        "FINANCE": {
            "wait_strategy": "route",
            "stealth": False,
            "proxy_required": True,
            "proxy_geo": "CN",
            "delay_range": (2.0, 4.0),
            "rate_limit_per_minute": 10,
            "captcha_types": [],
            "captcha_auto_handle": False,
            "api_intercept_enabled": True,
            "navigation_timeout": 25.0,
        },
        # 医疗健康：中等反爬
        "HEALTH": {
            "wait_strategy": "ajax",
            "stealth": False,
            "proxy_required": False,
            "proxy_geo": "",
            "delay_range": (2.0, 4.0),
            "rate_limit_per_minute": 12,
            "captcha_types": [],
            "captcha_auto_handle": False,
            "api_intercept_enabled": False,
            "navigation_timeout": 25.0,
        },
        # 法律类：低反爬
        "LEGAL": {
            "wait_strategy": "selector",
            "stealth": False,
            "proxy_required": False,
            "proxy_geo": "",
            "delay_range": (1.0, 3.0),
            "rate_limit_per_minute": 15,
            "captcha_types": ["text"],
            "captcha_auto_handle": True,
            "api_intercept_enabled": False,
            "navigation_timeout": 25.0,
        },
        # 招聘类：中等反爬
        "JOB": {
            "wait_strategy": "networkidle",
            "stealth": True,
            "proxy_required": True,
            "proxy_geo": "CN",
            "delay_range": (2.0, 5.0),
            "rate_limit_per_minute": 10,
            "captcha_types": ["slider"],
            "captcha_auto_handle": True,
            "api_intercept_enabled": False,
            "navigation_timeout": 30.0,
        },
        # 工具/搜索类：低反爬
        "TOOL": {
            "wait_strategy": "networkidle",
            "stealth": False,
            "proxy_required": False,
            "proxy_geo": "",
            "delay_range": (1.0, 3.0),
            "rate_limit_per_minute": 15,
            "captcha_types": [],
            "captcha_auto_handle": False,
            "api_intercept_enabled": True,
            "navigation_timeout": 20.0,
        },
        # 其他分类默认策略
        "DEFAULT": {
            "wait_strategy": "networkidle",
            "stealth": False,
            "proxy_required": False,
            "proxy_geo": "",
            "delay_range": (2.0, 4.0),
            "rate_limit_per_minute": 10,
            "captcha_types": [],
            "captcha_auto_handle": False,
            "api_intercept_enabled": False,
            "navigation_timeout": 30.0,
        },
    }
    
    @classmethod
    def get_strategies(cls, descriptor=None, category: str = "DEFAULT") -> StrategyConfig:
        """
        根据 descriptor 或 category 获取策略配置。
        descriptor 中的字段会覆盖默认值。
        """
        base = cls.STRATEGY_MAP.get(category, cls.STRATEGY_MAP["DEFAULT"])
        
        # 从 descriptor 中提取覆盖值
        if descriptor is not None:
            override = cls._extract_overrides(descriptor)
            base = {**base, **override}
        
        return StrategyConfig(
            wait_strategy=base.get("wait_strategy", "networkidle"),
            wait_timeout=base.get("navigation_timeout", 30.0),
            stealth=base.get("stealth", False),
            proxy_required=base.get("proxy_required", False),
            proxy_geo=base.get("proxy_geo", ""),
            delay_range=tuple(base.get("delay_range", (2.0, 4.0))),
            rate_limit_per_minute=base.get("rate_limit_per_minute", 10),
            captcha_types=base.get("captcha_types", []),
            captcha_auto_handle=base.get("captcha_auto_handle", False),
            api_intercept_enabled=base.get("api_intercept_enabled", False),
            api_patterns=base.get("api_patterns", []),
            csrf_token_selector=base.get("csrf_token_selector", "meta[name=csrf-token]"),
            navigation_timeout=base.get("navigation_timeout", 30.0),
            request_timeout=base.get("request_timeout", 15.0),
        )
    
    @classmethod
    def _extract_overrides(cls, descriptor) -> Dict[str, Any]:
        """从 CapabilityDescriptor 提取需要覆盖的字段"""
        overrides = {}
        if hasattr(descriptor, "default_wait_strategy") and descriptor.default_wait_strategy:
            overrides["wait_strategy"] = descriptor.default_wait_strategy
        if hasattr(descriptor, "default_stealth") and descriptor.default_stealth:
            overrides["stealth"] = True
        if hasattr(descriptor, "default_proxy_required") and descriptor.default_proxy_required:
            overrides["proxy_required"] = True
        if hasattr(descriptor, "default_delay_range") and descriptor.default_delay_range:
            overrides["delay_range"] = list(descriptor.default_delay_range)
        if hasattr(descriptor, "signature_patterns") and descriptor.signature_patterns:
            overrides["api_patterns"] = descriptor.signature_patterns
            overrides["api_intercept_enabled"] = True
        if hasattr(descriptor, "captcha_types") and descriptor.captcha_types:
            overrides["captcha_types"] = descriptor.captcha_types
        if hasattr(descriptor, "csrf_protection") and descriptor.csrf_protection:
            overrides["csrf_token_selector"] = "meta[name=csrf-token]"
        return overrides
    
    @classmethod
    def get_wait_config(cls, strategy: str, timeout: float = 30.0) -> Dict[str, Any]:
        """
        根据等待策略返回等待配置字典。
        用于传递给 CDP 页面对象。
        """
        configs = {
            "networkidle": {"wait_until": "networkidle", "timeout": timeout},
            "selector": {"wait_until": "domcontentloaded", "timeout": timeout},
            "route": {"wait_until": "load", "timeout": timeout},
            "stable": {"wait_until": "domcontentloaded", "timeout": timeout},
            "ajax": {"wait_until": "domcontentloaded", "timeout": timeout},
            "condition": {"wait_until": "domcontentloaded", "timeout": timeout},
        }
        return configs.get(strategy, configs["networkidle"])
    
    @classmethod
    def recommend_category(cls, url: str) -> str:
        """
        根据 URL 路径特征推荐站点分类。
        用于未知站点的初步分类。
        """
        url_lower = url.lower()
        if any(k in url_lower for k in ["jd.com", "taobao.com", "pdd", "amazon"]):
            return "ECOM"
        if any(k in url_lower for k in ["gov.cn", ".gov/", "court.gov"]):
            return "GOV"
        if any(k in url_lower for k in ["/admin", "/dashboard", "/backend", "/manage"]):
            return "ADMIN"
        if any(k in url_lower for k in ["zhihu.com", "weibo.com", "xiaohongshu"]):
            return "SOCIAL"
        if any(k in url_lower for k in ["sina.com.cn", "thepaper", "toutiao"]):
            return "NEWS"
        return "DEFAULT"
    
    @classmethod
    def strategy_matrix(cls) -> Dict[str, Dict[str, Any]]:
        """返回完整的策略矩阵（供文档生成使用）"""
        return {cat: {k: v for k, v in cfg.items() if k != "api_patterns" or not cfg.get("api_patterns")}
                for cat, cfg in cls.STRATEGY_MAP.items()}


__all__ = ["StrategyFactory", "StrategyConfig"]
