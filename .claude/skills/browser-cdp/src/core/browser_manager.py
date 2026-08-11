"""
browser_manager.py - 浏览器连接管理器

统一管理 CDP 和 Playwright 两种浏览器连接方式，提供：
- 连接池管理（复用已有浏览器实例）
- 自动故障恢复
- 多浏览器实例隔离
- 资源清理

用法示例：
  from src.core.browser_manager import BrowserManager
  
  manager = BrowserManager()
  
  # 方式1: 连接已有浏览器（CDP）
  cdp_session = manager.connect_cdp(port=9222)
  
  # 方式2: 启动新浏览器（Playwright）
  pw_session = manager.launch_playwright(headless=True)
  
  # 自动选择最佳连接
  session = manager.get_session(mode='auto')
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Union

logger = logging.getLogger(__name__)


@dataclass
class BrowserConfig:
    """浏览器配置"""
    # CDP 连接配置
    cdp_host: str = "127.0.0.1"
    cdp_port: int = 9222
    cdp_timeout: float = 15.0
    
    # Playwright 配置
    pw_headless: bool = True
    pw_timeout: int = 30000
    pw_viewport: Dict[str, int] = field(default_factory=lambda: {"width": 1920, "height": 1080})
    
    # 连接模式: 'cdp' | 'playwright' | 'auto'
    default_mode: str = "auto"
    
    # 实例管理
    max_instances: int = 3
    instance_ttl: float = 300.0  # 5分钟无活动则关闭


class CDPSession:
    """CDP 浏览器会话包装器"""
    
    def __init__(self, host: str, port: int, timeout: float = 15.0):
        self.host = host
        self.port = port
        self.timeout = timeout
        self._session = None
        self._last_active = time.time()
    
    def connect(self) -> bool:
        """尝试连接 CDP 端口"""
        from src.core.cdp_client import is_debug_port_alive, version_info
        
        if is_debug_port_alive(self.host, self.port, timeout=self.timeout):
            self._session = "connected"
            self._last_active = time.time()
            info = version_info(self.host, self.port)
            logger.info(f"CDP 连接成功: {self.host}:{self.port} - {info.get('Browser', 'Unknown')}")
            return True
        return False
    
    def is_alive(self) -> bool:
        """检查连接是否存活"""
        if self._session is None:
            return False
        from src.core.cdp_client import is_debug_port_alive
        return is_debug_port_alive(self.host, self.port, timeout=1.0)
    
    def get_version(self) -> Dict[str, Any]:
        """获取浏览器版本信息"""
        from src.core.cdp_client import version_info
        return version_info(self.host, self.port)
    
    def close(self):
        """关闭连接"""
        self._session = None
        logger.info(f"CDP 连接已关闭: {self.host}:{self.port}")


class PlaywrightSession:
    """Playwright 浏览器会话包装器"""
    
    def __init__(self, config: BrowserConfig):
        self.config = config
        self._session = None
        self._last_active = time.time()
    
    def launch(self) -> bool:
        """启动 Playwright 浏览器"""
        try:
            from src.core.playwright_session import PlaywrightSession as PWS
            self._session = PWS(config=None)  # 使用默认配置
            self._session.launch()
            self._last_active = time.time()
            logger.info("Playwright 浏览器已启动")
            return True
        except Exception as e:
            logger.error(f"Playwright 启动失败: {e}")
            return False
    
    def is_alive(self) -> bool:
        """检查会话是否存活"""
        return self._session is not None
    
    def get_page(self):
        """获取当前页面"""
        if self._session:
            return self._session._page
        return None
    
    def close(self):
        """关闭浏览器"""
        if self._session:
            self._session.close()
            self._session = None
            logger.info("Playwright 浏览器已关闭")


class BrowserManager:
    """
    浏览器连接管理器
    
    统一管理 CDP 和 Playwright 两种连接方式，提供连接池和自动故障恢复。
    """
    
    def __init__(self, config: BrowserConfig = None):
        self.config = config or BrowserConfig()
        self._cdp_sessions: Dict[str, CDPSession] = {}
        self._pw_sessions: List[PlaywrightSession] = []
        self._active_session = None
    
    def connect_cdp(self, port: int = None, host: str = None) -> Optional[CDPSession]:
        """
        连接 CDP 浏览器
        
        Args:
            port: 调试端口，默认使用配置中的端口
            host: 主机地址，默认使用配置中的主机
        
        Returns:
            CDPSession 对象，连接失败返回 None
        """
        port = port or self.config.cdp_port
        host = host or self.config.cdp_host
        key = f"{host}:{port}"
        
        # 检查是否已有连接
        if key in self._cdp_sessions:
            session = self._cdp_sessions[key]
            if session.is_alive():
                session._last_active = time.time()
                return session
            else:
                del self._cdp_sessions[key]
        
        # 创建新连接
        session = CDPSession(host, port, self.config.cdp_timeout)
        if session.connect():
            self._cdp_sessions[key] = session
            self._active_session = session
            return session
        
        logger.warning(f"CDP 连接失败: {key}")
        return None
    
    def launch_playwright(self, headless: bool = None) -> Optional[PlaywrightSession]:
        """
        启动 Playwright 浏览器
        
        Args:
            headless: 是否无头模式，默认使用配置
        
        Returns:
            PlaywrightSession 对象，启动失败返回 None
        """
        # 检查是否有存活会话
        for session in self._pw_sessions:
            if session.is_alive():
                session._last_active = time.time()
                self._active_session = session
                return session
        
        # 创建新会话
        if len(self._pw_sessions) >= self.config.max_instances:
            logger.warning(f"已达到最大实例数 {self.config.max_instances}")
            return None
        
        session = PlaywrightSession(self.config)
        if session.launch():
            self._pw_sessions.append(session)
            self._active_session = session
            return session
        
        return None
    
    def get_or_launch_playwright(self, headless: bool = None) -> Optional[PlaywrightSession]:
        """获取或启动 Playwright 会话（确保不在 asyncio 循环中调用）"""
        import asyncio
        try:
            asyncio.get_running_loop()
            # 在 asyncio 循环中，返回 None 让调用方处理
            return None
        except RuntimeError:
            pass
        return self.launch_playwright(headless)
    
    def get_session(self, mode: str = None) -> Optional[Union[CDPSession, PlaywrightSession]]:
        """
        获取活跃会话
        
        Args:
            mode: 连接模式 ('cdp' | 'playwright' | 'auto')
        
        Returns:
            活跃的浏览器会话
        """
        mode = mode or self.config.default_mode
        
        if mode == "cdp":
            return self.connect_cdp()
        elif mode == "playwright":
            return self.launch_playwright()
        else:  # auto
            # 优先使用已有会话
            if self._active_session:
                if isinstance(self._active_session, CDPSession) and self._active_session.is_alive():
                    return self._active_session
                if isinstance(self._active_session, PlaywrightSession) and self._active_session.is_alive():
                    return self._active_session
            
            # 尝试 CDP
            cdp = self.connect_cdp()
            if cdp:
                return cdp
            
            # 回退到 Playwright
            return self.launch_playwright()
    
    def cleanup_inactive(self, ttl: float = None):
        """
        清理不活跃的浏览器实例
        
        Args:
            ttl: 超时时间（秒），默认使用配置
        """
        ttl = ttl or self.config.instance_ttl
        now = time.time()
        
        # 清理 CDP 会话
        dead_cdp = [k for k, v in self._cdp_sessions.items() 
                    if now - v._last_active > ttl and not v.is_alive()]
        for k in dead_cdp:
            v = self._cdp_sessions.pop(k)
            v.close()
        
        # 清理 Playwright 会话
        dead_pw = [s for s in self._pw_sessions 
                   if now - s._last_active > ttl and not s.is_alive()]
        for s in dead_pw:
            self._pw_sessions.remove(s)
            s.close()
    
    def close_all(self):
        """关闭所有浏览器会话"""
        for session in self._cdp_sessions.values():
            session.close()
        for session in self._pw_sessions:
            session.close()
        self._cdp_sessions.clear()
        self._pw_sessions.clear()
        self._active_session = None
        logger.info("所有浏览器会话已关闭")
    
    def get_status(self) -> Dict[str, Any]:
        """获取浏览器连接状态"""
        return {
            "cdp_sessions": {
                k: {"alive": v.is_alive(), "last_active": v._last_active}
                for k, v in self._cdp_sessions.items()
            },
            "playwright_sessions": [
                {"alive": s.is_alive(), "last_active": s._last_active}
                for s in self._pw_sessions
            ],
            "active_session": type(self._active_session).__name__ if self._active_session else None,
        }


# 全局单例
_manager: Optional[BrowserManager] = None


def get_manager() -> BrowserManager:
    """获取全局浏览器管理器单例"""
    global _manager
    if _manager is None:
        _manager = BrowserManager()
    return _manager


def reset_manager():
    """重置全局管理器（用于测试）"""
    global _manager
    if _manager:
        _manager.close_all()
    _manager = None
