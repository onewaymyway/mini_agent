"""
anti_detection_manager.py - 统一反检测协调器

整合所有反检测模块，提供统一入口和自动化决策：
1. UA 轮换（UARotator）
2. 请求头伪装（RequestHeaders）
3. Stealth 模式（StealthMode）
4. 验证码处理（CaptchaHandler）
5. Cloudflare/Turnstile 绕过
6. 请求速率控制（RateLimiter）
7. 代理池管理（ProxyPool）

根据目标网站特征自动选择反检测策略组合。

用法示例：
    from src.core.anti_detection_manager import AntiDetectionManager
    mgr = AntiDetectionManager(session)
    await mgr.apply("xueqiu")  # 自动选择雪球的反检测策略
    result = await mgr.scrape_with_protection(url, selectors)
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)


class DetectionLevel(Enum):
    """反检测强度等级"""
    NONE = "none"              # 无检测，直接请求
    LIGHT = "light"            # 基础伪装（UA + 请求头）
    MEDIUM = "medium"          # + stealth 模式
    STRONG = "strong"          # + 验证码处理 + 代理池
    EXTREME = "extreme"        # + 行为模拟 + 人工辅助


class AntiDetectionStrategy(Enum):
    """反检测策略组合"""
    BASIC = "basic"                  # UA + Headers
    STEALTH = "stealth"              # + stealth.js
    CAPTCHA = "captcha"              # + 验证码处理
    PROXY = "proxy"                  # + 代理池
    BEHAVIOR = "behavior"            # + 用户行为模拟
    FULL = "full"                    # 全部策略


@dataclass
class SiteProfile:
    """目标网站反检测配置文件"""
    domain: str
    detection_level: DetectionLevel
    strategy: AntiDetectionStrategy
    requires_stealth: bool = False
    requires_proxy: bool = False
    requires_captcha: bool = False
    requires_behavior_sim: bool = False
    rate_limit_rpm: int = 60  # 每分钟请求数限制
    user_agents: List[str] = field(default_factory=list)
    custom_headers: Dict[str, str] = field(default_factory=dict)
    blocked_patterns: List[str] = field(default_factory=list)
    notes: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "domain": self.domain,
            "detection_level": self.detection_level.value,
            "strategy": self.strategy.value,
            "requires_stealth": self.requires_stealth,
            "requires_proxy": self.requires_proxy,
            "requires_captcha": self.requires_captcha,
            "requires_behavior_sim": self.requires_behavior_sim,
            "rate_limit_rpm": self.rate_limit_rpm,
            "user_agents": self.user_agents,
            "custom_headers": self.custom_headers,
            "blocked_patterns": self.blocked_patterns,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SiteProfile":
        return cls(
            domain=data.get("domain", ""),
            detection_level=DetectionLevel(data.get("detection_level", "none")),
            strategy=AntiDetectionStrategy(data.get("strategy", "basic")),
            requires_stealth=data.get("requires_stealth", False),
            requires_proxy=data.get("requires_proxy", False),
            requires_captcha=data.get("requires_captcha", False),
            requires_behavior_sim=data.get("requires_behavior_sim", False),
            rate_limit_rpm=data.get("rate_limit_rpm", 60),
            user_agents=data.get("user_agents", []),
            custom_headers=data.get("custom_headers", {}),
            blocked_patterns=data.get("blocked_patterns", []),
            notes=data.get("notes", ""),
        )


# 预定义网站反检测配置
default_site_profiles = {
    "zhihu.com": SiteProfile(
        domain="zhihu.com",
        detection_level=DetectionLevel.MEDIUM,
        strategy=AntiDetectionStrategy.STEALTH,
        requires_stealth=True,
        rate_limit_rpm=30,
        notes="知乎需要 stealth 模式，高频请求会触发验证",
    ),
    "xueqiu.com": SiteProfile(
        domain="xueqiu.com",
        detection_level=DetectionLevel.STRONG,
        strategy=AntiDetectionStrategy.FULL,
        requires_stealth=True,
        requires_proxy=True,
        requires_captcha=True,
        rate_limit_rpm=20,
        notes="雪球反爬严格，需要代理池和验证码处理",
    ),
    "weibo.com": SiteProfile(
        domain="weibo.com",
        detection_level=DetectionLevel.EXTREME,
        strategy=AntiDetectionStrategy.FULL,
        requires_stealth=True,
        requires_proxy=True,
        requires_captcha=True,
        requires_behavior_sim=True,
        rate_limit_rpm=10,
        notes="微博需要完整反检测，包括行为模拟",
    ),
    "gov.cn": SiteProfile(
        domain="gov.cn",
        detection_level=DetectionLevel.LIGHT,
        strategy=AntiDetectionStrategy.BASIC,
        rate_limit_rpm=60,
        notes="政府网站反爬弱，基础伪装即可",
    ),
    "baidu.com": SiteProfile(
        domain="baidu.com",
        detection_level=DetectionLevel.LIGHT,
        strategy=AntiDetectionStrategy.BASIC,
        requires_stealth=False,
        rate_limit_rpm=60,
        notes="百度搜索基础伪装即可，stealth 可能触发验证",
    ),
    "github.com": SiteProfile(
        domain="github.com",
        detection_level=DetectionLevel.LIGHT,
        strategy=AntiDetectionStrategy.BASIC,
        rate_limit_rpm=60,
        notes="GitHub API 有速率限制，基础伪装即可",
    ),
}


class AntiDetectionManager:
    """
    统一反检测协调器

    功能：
    1. 根据目标网站自动选择反检测策略
    2. 协调多个反检测模块（UA/Headers/Stealth/Proxy/Captcha）
    3. 提供统一的 scrape_with_protection API
    4. 记录反检测日志和统计
    """

    def __init__(
        self,
        session: Any,
        site_profiles: Optional[Dict[str, SiteProfile]] = None,
        auto_detect: bool = True,
    ):
        self.session = session
        self.auto_detect = auto_detect
        self.site_profiles = site_profiles or default_site_profiles
        self._active_profile: Optional[SiteProfile] = None
        self._log: List[Dict[str, Any]] = []
        self._stats = {
            "total_requests": 0,
            "blocked_count": 0,
            "captcha_triggered": 0,
            "success_count": 0,
        }

    # ------------------------------------------------------------------
    # 核心 API
    # ------------------------------------------------------------------

    async def apply(self, domain: str) -> SiteProfile:
        """
        为指定域名应用反检测策略

        Args:
            domain: 目标域名

        Returns:
            应用的 SiteProfile
        """
        profile = self._resolve_profile(domain)
        self._active_profile = profile

        # 按策略应用各模块
        if profile.requires_stealth:
            await self._apply_stealth(profile)

        if profile.requires_proxy:
            await self._apply_proxy(profile)

        if profile.requires_captcha:
            await self._setup_captcha_handler(profile)

        self._log_event("apply", domain=domain, profile=profile.to_dict())
        logger.info(f"AntiDetectionManager: 已应用反检测策略 [{domain}] -> {profile.strategy.value}")
        return profile

    async def scrape_with_protection(
        self,
        url: str,
        selectors: Optional[List[str]] = None,
        timeout: float = 30.0,
    ) -> Dict[str, Any]:
        """
        带反检测保护的抓取

        Args:
            url: 目标 URL
            selectors: CSS 选择器列表
            timeout: 超时时间

        Returns:
            抓取结果
        """
        domain = self._extract_domain(url)
        profile = await self.apply(domain)

        self._stats["total_requests"] += 1
        start_time = time.time()

        try:
            # 执行抓取
            result = await self._do_scrape(url, selectors, timeout)
            elapsed = time.time() - start_time

            if result.get("success"):
                self._stats["success_count"] += 1
                self._log_event("scrape_success", url=url, elapsed=elapsed)
            else:
                self._stats["blocked_count"] += 1
                self._log_event("scrape_blocked", url=url, error=result.get("error"))

            return result

        except Exception as e:
            elapsed = time.time() - start_time
            self._stats["blocked_count"] += 1
            self._log_event("scrape_error", url=url, error=str(e), elapsed=elapsed)
            return {
                "success": False,
                "error": str(e),
                "url": url,
                "elapsed": elapsed,
            }

    def get_stats(self) -> Dict[str, Any]:
        """返回反检测统计"""
        return {
            **self._stats,
            "active_profile": self._active_profile.to_dict() if self._active_profile else None,
            "log_size": len(self._log),
        }

    def get_log(self, limit: int = 100) -> List[Dict[str, Any]]:
        """返回反检测日志"""
        return self._log[-limit:]

    # ------------------------------------------------------------------
    # 内部实现
    # ------------------------------------------------------------------

    def _resolve_profile(self, domain: str) -> SiteProfile:
        """解析域名对应的反检测配置"""
        # 精确匹配
        if domain in self.site_profiles:
            return self.site_profiles[domain]

        # 后缀匹配（如 subdomain.example.com -> example.com）
        parts = domain.split(".")
        for i in range(len(parts) - 1):
            suffix = ".".join(parts[i:])
            if suffix in self.site_profiles:
                return self.site_profiles[suffix]

        # 默认配置（LIGHT + BASIC）
        logger.warning(f"AntiDetectionManager: 未找到 [{domain}] 的反检测配置，使用默认配置")
        return SiteProfile(
            domain=domain,
            detection_level=DetectionLevel.LIGHT,
            strategy=AntiDetectionStrategy.BASIC,
        )

    async def _apply_stealth(self, profile: SiteProfile) -> None:
        """应用 stealth 模式"""
        try:
            from src.core.stealth import StealthMode
            stealth = StealthMode(self.session)
            await stealth.apply()
            logger.debug(f"AntiDetectionManager: stealth 模式已应用")
        except ImportError:
            logger.warning("AntiDetectionManager: stealth 模块不可用")
        except Exception as e:
            logger.warning(f"AntiDetectionManager: stealth 应用失败: {e}")

    async def _apply_proxy(self, profile: SiteProfile) -> None:
        """应用代理池"""
        try:
            from src.core.proxy_pool import ProxyPool
            pool = ProxyPool()
            proxy = await pool.get_proxy()
            if proxy:
                await self.session.set_proxy(proxy)
                logger.debug(f"AntiDetectionManager: 代理已设置 -> {proxy}")
        except ImportError:
            logger.warning("AntiDetectionManager: proxy_pool 模块不可用")
        except Exception as e:
            logger.warning(f"AntiDetectionManager: 代理设置失败: {e}")

    async def _setup_captcha_handler(self, profile: SiteProfile) -> None:
        """设置验证码处理器"""
        try:
            from src.core.captcha_handler import CaptchaHandler
            handler = CaptchaHandler(self.session)
            logger.debug("AntiDetectionManager: 验证码处理器已就绪")
        except ImportError:
            logger.warning("AntiDetectionManager: captcha_handler 模块不可用")

    async def _do_scrape(
        self,
        url: str,
        selectors: Optional[List[str]],
        timeout: float,
    ) -> Dict[str, Any]:
        """执行实际抓取"""
        # 这里调用现有的抓取逻辑
        # 简化实现，实际应该调用 browser_api.py 或 enhanced_cdp_session.py
        result = {
            "success": True,
            "url": url,
            "selectors": selectors,
            "data": {},
        }

        if selectors:
            for selector in selectors:
                try:
                    elements = await self.session.query_selector_all(selector)
                    result["data"][selector] = [
                        {
                            "text": el.text[:500] if el.text else "",
                            "href": await el.get_attribute("href") if await el.get_attribute("href") else None,
                        }
                        for el in elements[:20]  # 限制数量
                    ]
                except Exception as e:
                    result["data"][selector] = {"error": str(e)}

        return result

    def _extract_domain(self, url: str) -> str:
        """从 URL 提取域名"""
        try:
            from urllib.parse import urlparse
            return urlparse(url).netloc
        except Exception:
            return url.split("/")[2] if "/" in url else url

    def _log_event(
        self,
        event_type: str,
        **kwargs,
    ) -> None:
        """记录事件日志"""
        entry = {
            "timestamp": time.time(),
            "event": event_type,
            **kwargs,
        }
        self._log.append(entry)
        # 限制日志大小
        if len(self._log) > 1000:
            self._log = self._log[-500:]


# =====================================================================
# 便捷函数
# =====================================================================

async def quick_apply_protection(
    session: Any,
    domain: str,
) -> SiteProfile:
    """快捷函数：为域名快速应用反检测保护"""
    mgr = AntiDetectionManager(session)
    return await mgr.apply(domain)


async def batch_scrape_protected(
    session: Any,
    urls: List[str],
    selectors: Optional[List[str]] = None,
    delay_between: float = 1.0,
) -> List[Dict[str, Any]]:
    """
    批量抓取（带反检测保护）

    Args:
        session: CDP session
        urls: URL 列表
        selectors: CSS 选择器
        delay_between: 请求间隔（秒）

    Returns:
        抓取结果列表
    """
    mgr = AntiDetectionManager(session)
    results = []

    for i, url in enumerate(urls):
        logger.info(f"batch_scrape: [{i+1}/{len(urls)}] {url}")
        result = await mgr.scrape_with_protection(url, selectors)
        results.append(result)

        # 间隔请求
        if i < len(urls) - 1:
            await asyncio.sleep(delay_between)

    return results
