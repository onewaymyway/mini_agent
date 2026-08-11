"""
request_headers.py - 请求头伪装模块

支持按站点自定义请求头、Sec-Fetch-* 等现代浏览器头，
模拟真实浏览器的请求特征。
"""
from __future__ import annotations

import logging
from typing import Dict, Optional, List
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class HeaderConfig:
    """请求头配置"""
    # 基础头
    accept: str = "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8"
    accept_language: str = "zh-CN,zh;q=0.9,en;q=0.8"
    accept_encoding: str = "gzip, deflate, br"
    cache_control: str = "max-age=0"
    
    # Sec-Fetch-* 头（现代浏览器特征）
    sec_fetch_dest: str = "document"
    sec_fetch_mode: str = "navigate"
    sec_fetch_site: str = "same-origin"
    sec_fetch_user: str = "?1"
    
    # DNT
    dnt: str = "1"
    
    # Connection
    connection: str = "keep-alive"
    
    # 自定义头（可覆盖默认值）
    custom_headers: Dict[str, str] = field(default_factory=dict)
    
    # 站点特定配置
    site_overrides: Dict[str, Dict[str, str]] = field(default_factory=dict)


class RequestHeaderManager:
    """
    请求头管理器
    
    根据目标站点动态生成浏览器风格的请求头
    """
    
    # 常见站点配置
    SITE_CONFIGS: Dict[str, HeaderConfig] = {
        "bilibili.com": HeaderConfig(
            sec_fetch_dest="document",
            sec_fetch_mode="navigate",
            sec_fetch_site="none",
            sec_fetch_user="?1",
            custom_headers={
                "Referer": "https://www.bilibili.com/",
            }
        ),
        "zhihu.com": HeaderConfig(
            sec_fetch_dest="document",
            sec_fetch_mode="navigate",
            sec_fetch_site="same-origin",
            sec_fetch_user="?1",
            custom_headers={
                "Referer": "https://www.zhihu.com/",
            }
        ),
        "jd.com": HeaderConfig(
            sec_fetch_dest="document",
            sec_fetch_mode="navigate",
            sec_fetch_site="same-origin",
            sec_fetch_user="?1",
            custom_headers={
                "Referer": "https://www.jd.com/",
            }
        ),
        "taobao.com": HeaderConfig(
            sec_fetch_dest="document",
            sec_fetch_mode="navigate",
            sec_fetch_site="same-origin",
            sec_fetch_user="?1",
            custom_headers={
                "Referer": "https://www.taobao.com/",
            }
        ),
        "weibo.com": HeaderConfig(
            sec_fetch_dest="document",
            sec_fetch_mode="navigate",
            sec_fetch_site="same-origin",
            sec_fetch_user="?1",
            custom_headers={
                "Referer": "https://weibo.com/",
            }
        ),
        "xueqiu.com": HeaderConfig(
            sec_fetch_dest="document",
            sec_fetch_mode="navigate",
            sec_fetch_site="same-origin",
            sec_fetch_user="?1",
            custom_headers={
                "Referer": "https://xueqiu.com/",
                "X-Requested-With": "XMLHttpRequest",
            }
        ),
        "douban.com": HeaderConfig(
            sec_fetch_dest="document",
            sec_fetch_mode="navigate",
            sec_fetch_site="same-origin",
            sec_fetch_user="?1",
            custom_headers={
                "Referer": "https://www.douban.com/",
            }
        ),
        "xiaohongshu.com": HeaderConfig(
            sec_fetch_dest="document",
            sec_fetch_mode="navigate",
            sec_fetch_site="same-origin",
            sec_fetch_user="?1",
            custom_headers={
                "Referer": "https://www.xiaohongshu.com/",
            }
        ),
        "github.com": HeaderConfig(
            sec_fetch_dest="document",
            sec_fetch_mode="navigate",
            sec_fetch_site="same-origin",
            sec_fetch_user="?1",
            custom_headers={
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
            }
        ),
        "36kr.com": HeaderConfig(
            sec_fetch_dest="document",
            sec_fetch_mode="navigate",
            sec_fetch_site="same-origin",
            sec_fetch_user="?1",
            custom_headers={
                "Referer": "https://36kr.com/",
            }
        ),
        "csdn.net": HeaderConfig(
            sec_fetch_dest="document",
            sec_fetch_mode="navigate",
            sec_fetch_site="same-origin",
            sec_fetch_user="?1",
            custom_headers={
                "Referer": "https://www.csdn.net/",
            }
        ),
        "juejin.cn": HeaderConfig(
            sec_fetch_dest="document",
            sec_fetch_mode="navigate",
            sec_fetch_site="same-origin",
            sec_fetch_user="?1",
            custom_headers={
                "Referer": "https://juejin.cn/",
            }
        ),
        "10jqka.com.cn": HeaderConfig(
            sec_fetch_dest="document",
            sec_fetch_mode="navigate",
            sec_fetch_site="same-origin",
            sec_fetch_user="?1",
            custom_headers={
                "Referer": "https://www.10jqka.com.cn/",
            }
        ),
        "iwencai.com": HeaderConfig(
            sec_fetch_dest="document",
            sec_fetch_mode="navigate",
            sec_fetch_site="same-origin",
            sec_fetch_user="?1",
            custom_headers={
                "Referer": "https://www.iwencai.com/",
            }
        ),
    }
    
    def __init__(self, config: HeaderConfig = None):
        self.config = config or HeaderConfig()
        self._headers: Dict[str, str] = {}
    
    def get_headers(self, url: str = None, site: str = None) -> Dict[str, str]:
        """
        获取请求头
        
        Args:
            url: 目标 URL
            site: 目标站点域名
            
        Returns:
            Dict[str, str]: 请求头字典
        """
        # 确定站点配置
        override_site = site or self._extract_site(url)
        
        # 合并配置
        headers = self._merge_headers(override_site)
        
        # 添加动态头
        headers.update(self._get_dynamic_headers(url))
        
        self._headers = headers
        logger.debug(f"生成请求头: {len(headers)} 个")
        return headers
    
    def _extract_site(self, url: str) -> Optional[str]:
        """从 URL 提取站点域名"""
        if not url:
            return None
        try:
            from urllib.parse import urlparse
            parsed = urlparse(url)
            return parsed.netloc
        except Exception:
            return None
    
    def _merge_headers(self, site: Optional[str]) -> Dict[str, str]:
        """合并基础配置和站点覆盖配置"""
        headers = {
            "Accept": self.config.accept,
            "Accept-Language": self.config.accept_language,
            "Accept-Encoding": self.config.accept_encoding,
            "Cache-Control": self.config.cache_control,
            "Sec-Fetch-Dest": self.config.sec_fetch_dest,
            "Sec-Fetch-Mode": self.config.sec_fetch_mode,
            "Sec-Fetch-Site": self.config.sec_fetch_site,
            "Sec-Fetch-User": self.config.sec_fetch_user,
            "DNT": self.config.dnt,
            "Connection": self.config.connection,
        }
        
        # 添加自定义头
        headers.update(self.config.custom_headers)
        
        # 添加站点覆盖
        if site:
            for pattern, override in self.config.site_overrides.items():
                if pattern in site:
                    headers.update(override)
                    logger.debug(f"应用站点覆盖: {pattern} -> {site}")
                    break
            
            # 检查预定义站点配置
            for pattern, site_config in self.SITE_CONFIGS.items():
                if pattern in site:
                    headers.update(site_config.custom_headers)
                    logger.debug(f"应用预定义站点配置: {pattern}")
                    break
        
        return headers
    
    def _get_dynamic_headers(self, url: Optional[str]) -> Dict[str, str]:
        """获取动态头（Referer 等）"""
        headers = {}
        
        if url:
            try:
                from urllib.parse import urlparse
                parsed = urlparse(url)
                referer = f"{parsed.scheme}://{parsed.netloc}/"
                headers["Referer"] = referer
            except Exception:
                pass
        
        return headers
    
    def update_config(self, **kwargs):
        """更新配置"""
        for key, value in kwargs.items():
            if hasattr(self.config, key):
                setattr(self.config, key, value)
                logger.debug(f"更新配置: {key} = {value}")
    
    def add_site_override(self, site: str, headers: Dict[str, str]):
        """添加站点覆盖配置"""
        self.config.site_overrides[site] = headers
        logger.debug(f"添加站点覆盖: {site}")
    
    def clear(self):
        """清空缓存的头"""
        self._headers.clear()
        logger.debug("清空请求头缓存")


# 全局单例
_header_manager: Optional[RequestHeaderManager] = None


def get_header_manager() -> RequestHeaderManager:
    """获取全局请求头管理器单例"""
    global _header_manager
    if _header_manager is None:
        _header_manager = RequestHeaderManager()
    return _header_manager


def set_header_manager(manager: RequestHeaderManager):
    """设置全局请求头管理器"""
    global _header_manager
    _header_manager = manager
    logger.debug("设置全局请求头管理器")


def reset_header_manager():
    """重置全局请求头管理器"""
    global _header_manager
    _header_manager = None
    logger.debug("重置全局请求头管理器")
