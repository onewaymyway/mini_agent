"""
ua_rotator.py - User-Agent 轮换模块

提供真实的 User-Agent 池和智能轮换策略，模拟不同设备和浏览器行为。
"""
from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


# 真实 User-Agent 池（按设备类型分类）
USER_AGENTS = {
    "desktop": {
        "chrome_windows": [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36",
        ],
        "chrome_mac": [
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
        ],
        "firefox_windows": [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:120.0) Gecko/20100101 Firefox/120.0",
        ],
        "firefox_mac": [
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:121.0) Gecko/20100101 Firefox/121.0",
        ],
        "edge_windows": [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0",
        ],
    },
    "mobile": {
        "chrome_android": [
            "Mozilla/5.0 (Linux; Android 14; SM-S908B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36",
            "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36",
        ],
        "safari_ios": [
            "Mozilla/5.0 (iPhone; CPU iPhone OS 17_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Mobile/15E148 Safari/604.1",
            "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1",
        ],
        "chrome_ios": [
            "Mozilla/5.0 (iPhone; CPU iPhone OS 17_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) CriOS/120.0.0.0 Mobile/15E148 Safari/604.1",
        ],
    },
    "tablet": {
        "ipad": [
            "Mozilla/5.0 (iPad; CPU OS 17_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Mobile/15E148 Safari/604.1",
            "Mozilla/5.0 (iPad; CPU OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1",
        ],
        "android_tablet": [
            "Mozilla/5.0 (Linux; Android 13; SM-X900) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        ],
    },
}


# 网站特定的 UA 偏好（某些网站对特定 UA 更友好）
SITE_UA_PREFERENCES: Dict[str, List[str]] = {
    "baidu.com": ["chrome_windows", "chrome_android"],
    "zhihu.com": ["chrome_windows", "firefox_windows"],
    "github.com": ["chrome_windows", "firefox_mac"],
    "google.com": ["chrome_windows", "chrome_android", "safari_ios"],
    "weibo.com": ["chrome_android", "safari_ios"],
    "bilibili.com": ["chrome_windows", "chrome_android"],
}


@dataclass
class UARotationConfig:
    """UA 轮换配置"""
    # 轮换策略
    strategy: str = "weighted_random"  # weighted_random / round_robin / site_preferred
    
    # 设备分布权重（影响随机选择概率）
    device_weights: Dict[str, float] = None
    
    # 是否启用站点偏好
    enable_site_preference: bool = True
    
    # 请求间隔范围（秒）- 模拟人类浏览节奏
    request_delay_range: tuple = (0.5, 2.0)
    
    # 是否记录 UA 使用历史
    enable_history: bool = True
    
    def __post_init__(self):
        if self.device_weights is None:
            # 默认权重：桌面设备更常见
            self.device_weights = {
                "desktop": 0.6,
                "mobile": 0.35,
                "tablet": 0.05,
            }


class UARotator:
    """User-Agent 轮换器"""
    
    def __init__(self, config: Optional[UARotationConfig] = None):
        self.config = config or UARotationConfig()
        self._current_index: int = 0
        self._history: List[Dict] = []
        self._site_uas: Dict[str, str] = {}  # 缓存站点 UA
    
    def get_random_ua(self, site: Optional[str] = None) -> str:
        """
        获取随机 UA
        
        Args:
            site: 目标网站域名（可选，用于站点偏好）
            
        Returns:
            User-Agent 字符串
        """
        if self.config.strategy == "round_robin":
            ua = self._get_round_robin_ua(site)
        elif self.config.strategy == "site_preferred" and site:
            ua = self._get_site_preferred_ua(site)
        else:
            ua = self._get_weighted_random_ua(site)
        
        # 记录历史
        if self.config.enable_history:
            self._history.append({
                "ua": ua,
                "site": site,
                "timestamp": time.time()
            })
            # 限制历史记录大小
            if len(self._history) > 1000:
                self._history = self._history[-500:]
        
        return ua
    
    def _get_weighted_random_ua(self, site: Optional[str] = None) -> str:
        """按权重随机选择 UA"""
        # 选择设备类型
        device_type = random.choices(
            list(self.config.device_weights.keys()),
            weights=list(self.config.device_weights.values()),
            k=1
        )[0]
        
        # 获取该设备类型的 UA 列表
        ua_list = USER_AGENTS.get(device_type, {})
        if not ua_list:
            # 回退到桌面 Chrome
            ua_list = USER_AGENTS["desktop"]["chrome_windows"]
        
        # 随机选择具体 UA
        return random.choice(ua_list)
    
    def _get_round_robin_ua(self, site: Optional[str] = None) -> str:
        """轮询选择 UA"""
        all_uas = []
        for device_type, browsers in USER_AGENTS.items():
            for browser, uas in browsers.items():
                all_uas.extend(uas)
        
        if not all_uas:
            return USER_AGENTS["desktop"]["chrome_windows"][0]
        
        ua = all_uas[self._current_index % len(all_uas)]
        self._current_index += 1
        return ua
    
    def _get_site_preferred_ua(self, site: str) -> str:
        """获取站点偏好的 UA"""
        # 提取域名
        domain = site.split("/")[2] if "/" in site else site
        domain = domain.split("?")[0].split("#")[0]
        
        # 检查是否有站点偏好
        if domain in SITE_UA_PREFERENCES:
            preferred_types = SITE_UA_PREFERENCES[domain]
            
            # 尝试从偏好类型中选择
            for pref_type in preferred_types:
                if pref_type in USER_AGENTS:
                    # 遍历设备类型下的浏览器
                    for browser, uas in USER_AGENTS[pref_type].items():
                        if uas:
                            ua = random.choice(uas)
                            self._site_uas[domain] = ua
                            return ua
        
        # 无偏好时随机选择
        return self._get_weighted_random_ua(site)
    
    def get_ua_for_page(self, page) -> str:
        """
        获取页面 UA 并设置到浏览器
        
        Args:
            page: Playwright page 对象
            
        Returns:
            设置的 UA 字符串
        """
        ua = self.get_random_ua()
        
        # 设置到浏览器
        try:
            # Playwright 方式
            if hasattr(page, 'set_extra_http_headers'):
                page.set_extra_http_headers({
                    'User-Agent': ua
                })
        except Exception as e:
            logger.debug(f"设置 UA 失败: {e}")
        
        return ua
    
    def get_request_headers(self, site: Optional[str] = None) -> Dict[str, str]:
        """
        获取请求头（包含 UA）
        
        Args:
            site: 目标网站
            
        Returns:
            请求头字典
        """
        ua = self.get_random_ua(site)
        
        return {
            'User-Agent': ua,
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
        }
    
    def random_delay(self) -> float:
        """
        生成随机延迟（模拟人类浏览节奏）
        
        Returns:
            延迟秒数
        """
        delay = random.uniform(*self.config.request_delay_range)
        time.sleep(delay)
        return delay
    
    def get_stats(self) -> Dict:
        """获取 UA 使用统计"""
        if not self._history:
            return {
                "total_requests": 0,
                "unique_uas": 0,
                "site_distribution": {}
            }
        
        # 统计 UA 分布
        ua_counts: Dict[str, int] = {}
        site_counts: Dict[str, int] = {}
        
        for record in self._history:
            ua = record["ua"]
            site = record.get("site", "unknown")
            
            ua_counts[ua] = ua_counts.get(ua, 0) + 1
            site_counts[site] = site_counts.get(site, 0) + 1
        
        return {
            "total_requests": len(self._history),
            "unique_uas": len(ua_counts),
            "top_uas": sorted(ua_counts.items(), key=lambda x: -x[1])[:5],
            "site_distribution": site_counts
        }


# 全局单例
_ua_rotator: Optional[UARotator] = None


def get_ua_rotator() -> UARotator:
    """获取全局 UA 轮换器单例"""
    global _ua_rotator
    if _ua_rotator is None:
        _ua_rotator = UARotator()
    return _ua_rotator


def set_ua_rotator(rotator: UARotator):
    """设置全局 UA 轮换器"""
    global _ua_rotator
    _ua_rotator = rotator


def reset_ua_rotator():
    """重置全局 UA 轮换器"""
    global _ua_rotator
    _ua_rotator = None


# 便捷函数
def get_random_ua(site: Optional[str] = None) -> str:
    """获取随机 UA（便捷函数）"""
    return get_ua_rotator().get_random_ua(site)


def get_request_headers(site: Optional[str] = None) -> Dict[str, str]:
    """获取请求头（便捷函数）"""
    return get_ua_rotator().get_request_headers(site)