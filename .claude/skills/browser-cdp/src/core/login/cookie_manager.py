"""
Cookie 持久化管理模块

支持：
- 获取/设置/删除 Cookie
- Cookie 持久化到文件
- Cookie 自动恢复
- 按域名过滤 Cookie
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any

logger = logging.getLogger(__name__)


@dataclass
class CookieInfo:
    """Cookie 信息"""
    name: str
    value: str
    domain: str
    path: str = "/"
    expires: Optional[float] = None
    secure: bool = False
    http_only: bool = False
    same_site: str = "Lax"
    
    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "value": self.value,
            "domain": self.domain,
            "path": self.path,
            "expires": self.expires,
            "secure": self.secure,
            "httpOnly": self.http_only,
            "sameSite": self.same_site,
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> "CookieInfo":
        return cls(
            name=data["name"],
            value=data["value"],
            domain=data["domain"],
            path=data.get("path", "/"),
            expires=data.get("expires"),
            secure=data.get("secure", False),
            http_only=data.get("httpOnly", False),
            same_site=data.get("sameSite", "Lax"),
        )
    
    def is_expired(self) -> bool:
        if self.expires is None:
            return False
        return time.time() > self.expires


class CookieManager:
    """
    Cookie 管理器
    
    支持从 CDP session 获取/设置 Cookie，并持久化到本地文件。
    """
    
    def __init__(self, session, storage_dir: str = None):
        self.session = session
        self.storage_dir = storage_dir or os.path.join("temp_data", "cookies")
        os.makedirs(self.storage_dir, exist_ok=True)
        self._cookie_cache: Dict[str, List[CookieInfo]] = {}
    
    def get_cookies(self, url: str = None) -> List[CookieInfo]:
        """
        从浏览器获取 Cookie
        
        Args:
            url: 可选，只获取指定域名的 Cookie
        
        Returns:
            CookieInfo 列表
        """
        try:
            if url:
                result = self.session.send("Network.getCookies", {"urls": [url]})
            else:
                result = self.session.send("Network.getCookies", {})
            
            cookies = result.get("cookies", [])
            cookie_infos = [
                CookieInfo(
                    name=c["name"],
                    value=c["value"],
                    domain=c["domain"],
                    path=c.get("path", "/"),
                    expires=c.get("expires"),
                    secure=c.get("secure", False),
                    http_only=c.get("httpOnly", False),
                    same_site=c.get("sameSite", "Lax"),
                )
                for c in cookies
            ]
            
            if url:
                domain = self._extract_domain(url)
                self._cookie_cache[domain] = cookie_infos
            
            return cookie_infos
        except Exception as e:
            logger.error(f"获取 Cookie 失败: {e}")
            return []
    
    def set_cookies(self, cookies: List[CookieInfo], url: str = None) -> bool:
        """
        设置 Cookie 到浏览器
        
        Args:
            cookies: CookieInfo 列表
            url: 可选，目标 URL
        
        Returns:
            是否成功
        """
        try:
            cookie_dicts = [c.to_dict() for c in cookies]
            self.session.send("Network.setCookies", {"cookies": cookie_dicts})
            
            if url:
                domain = self._extract_domain(url)
                self._cookie_cache[domain] = cookies
            
            logger.info(f"已设置 {len(cookies)} 个 Cookie")
            return True
        except Exception as e:
            logger.error(f"设置 Cookie 失败: {e}")
            return False
    
    def delete_cookies(self, name: str = None, domain: str = None) -> bool:
        """
        删除 Cookie
        
        Args:
            name: Cookie 名称，None 表示删除所有
            domain: 域名，None 表示删除所有
        
        Returns:
            是否成功
        """
        try:
            if name and domain:
                # 删除指定域名的指定 Cookie
                cookies = self.get_cookies(f"https://{domain}")
                to_delete = [c for c in cookies if c.name == name]
                for c in to_delete:
                    self.session.send("Network.deleteCookies", {"name": c.name, "domain": c.domain})
            elif domain:
                # 删除指定域名的所有 Cookie
                cookies = self.get_cookies(f"https://{domain}")
                for c in cookies:
                    self.session.send("Network.deleteCookies", {"name": c.name, "domain": c.domain})
            else:
                # 删除所有 Cookie
                self.session.send("Network.clearBrowserCookies", {})
            
            logger.info(f"已删除 Cookie")
            return True
        except Exception as e:
            logger.error(f"删除 Cookie 失败: {e}")
            return False
    
    def save_cookies(self, cookies: List[CookieInfo], key: str) -> str:
        """
        持久化 Cookie 到文件
        
        Args:
            cookies: CookieInfo 列表
            key: 存储键（如域名）
        
        Returns:
            保存的文件路径
        """
        safe_key = self._safe_filename(key)
        file_path = os.path.join(self.storage_dir, f"{safe_key}.json")
        
        data = {
            "saved_at": time.time(),
            "domain": key,
            "cookies": [c.to_dict() for c in cookies],
        }
        
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        
        logger.info(f"Cookie 已保存到: {file_path}")
        return file_path
    
    def load_cookies(self, key: str) -> List[CookieInfo]:
        """
        从文件加载 Cookie
        
        Args:
            key: 存储键
        
        Returns:
            CookieInfo 列表
        """
        safe_key = self._safe_filename(key)
        file_path = os.path.join(self.storage_dir, f"{safe_key}.json")
        
        if not os.path.exists(file_path):
            logger.warning(f"Cookie 文件不存在: {file_path}")
            return []
        
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        
        cookies = [CookieInfo.from_dict(c) for c in data.get("cookies", [])]
        
        # 过滤已过期的 Cookie
        valid_cookies = [c for c in cookies if not c.is_expired()]
        expired_count = len(cookies) - len(valid_cookies)
        
        if expired_count > 0:
            logger.warning(f"已过滤 {expired_count} 个过期 Cookie")
        
        return valid_cookies
    
    def restore_cookies(self, key: str, url: str = None) -> int:
        """
        从文件加载并设置 Cookie
        
        Args:
            key: 存储键
            url: 目标 URL
        
        Returns:
            恢复的 Cookie 数量
        """
        cookies = self.load_cookies(key)
        if cookies:
            self.set_cookies(cookies, url)
        return len(cookies)
    
    def get_cookie_count(self, domain: str = None) -> int:
        """
        获取 Cookie 数量
        
        Args:
            domain: 可选，按域名过滤
        
        Returns:
            Cookie 数量
        """
        cookies = self.get_cookies(f"https://{domain}" if domain else None)
        return len(cookies)
    
    def has_cookie(self, name: str, domain: str = None) -> bool:
        """
        检查是否存在指定 Cookie
        
        Args:
            name: Cookie 名称
            domain: 可选，域名
        
        Returns:
            是否存在
        """
        cookies = self.get_cookies(f"https://{domain}" if domain else None)
        return any(c.name == name for c in cookies)
    
    def _extract_domain(self, url: str) -> str:
        """从 URL 提取域名"""
        if url.startswith("http://"):
            return url[7:].split("/")[0]
        elif url.startswith("https://"):
            return url[8:].split("/")[0]
        return url.split("/")[0]
    
    def _safe_filename(self, key: str) -> str:
        """将域名转换为安全的文件名"""
        return key.replace(".", "_").replace(":", "_").replace("/", "_")


# 便捷函数
def get_cookie_manager(session, storage_dir: str = None) -> CookieManager:
    """获取 Cookie 管理器实例"""
    return CookieManager(session, storage_dir)


def save_cookies_to_file(session, key: str, storage_dir: str = None) -> str:
    """保存当前 Cookie 到文件"""
    mgr = CookieManager(session, storage_dir)
    cookies = mgr.get_cookies()
    return mgr.save_cookies(cookies, key)


def restore_cookies_from_file(session, key: str, url: str = None, storage_dir: str = None) -> int:
    """从文件恢复 Cookie"""
    mgr = CookieManager(session, storage_dir)
    return mgr.restore_cookies(key, url)
