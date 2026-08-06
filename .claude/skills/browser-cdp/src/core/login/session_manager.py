"""
会话状态保持模块

支持：
- 登录会话持久化
- 会话状态检测
- 会话自动恢复
- 多会话管理
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any

from .cookie_manager import CookieManager, CookieInfo

logger = logging.getLogger(__name__)


@dataclass
class SessionInfo:
    """会话信息"""
    session_id: str
    url: str
    title: str
    created_at: float
    last_active: float
    cookies: List[CookieInfo] = field(default_factory=list)
    is_logged_in: bool = False
    user_info: Optional[Dict[str, Any]] = None
    
    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "url": self.url,
            "title": self.title,
            "created_at": self.created_at,
            "last_active": self.last_active,
            "cookies": [c.to_dict() for c in self.cookies],
            "cookies_count": len(self.cookies),
            "is_logged_in": self.is_logged_in,
            "user_info": self.user_info,
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> "SessionInfo":
        return cls(
            session_id=data["session_id"],
            url=data["url"],
            title=data["title"],
            created_at=data["created_at"],
            last_active=data["last_active"],
            cookies=[CookieInfo.from_dict(c) for c in data.get("cookies", [])],
            is_logged_in=data.get("is_logged_in", False),
            user_info=data.get("user_info"),
        )
    
    def is_expired(self, ttl_seconds: int = 86400) -> bool:
        """检查会话是否过期"""
        return time.time() - self.last_active > ttl_seconds


class SessionManager:
    """
    会话管理器
    
    管理浏览器会话的持久化、恢复和状态检测。
    """
    
    def __init__(self, session, storage_dir: str = None, default_ttl: int = 86400):
        self.session = session
        self.storage_dir = storage_dir or os.path.join("temp_data", "sessions")
        self.default_ttl = default_ttl
        os.makedirs(self.storage_dir, exist_ok=True)
        self._sessions: Dict[str, SessionInfo] = {}
        self._cookie_manager = CookieManager(session, self.storage_dir)
    
    def create_session(self, session_id: str, url: str = None, title: str = None) -> SessionInfo:
        """
        创建新会话
        
        Args:
            session_id: 会话 ID
            url: 当前 URL
            title: 页面标题
        
        Returns:
            SessionInfo 对象
        """
        now = time.time()
        session_info = SessionInfo(
            session_id=session_id,
            url=url or self._get_current_url(),
            title=title or self._get_current_title(),
            created_at=now,
            last_active=now,
        )
        self._sessions[session_id] = session_info
        self._save_session(session_info)
        logger.info(f"创建会话: {session_id}")
        return session_info
    
    def get_session(self, session_id: str) -> Optional[SessionInfo]:
        """
        获取会话信息
        
        Args:
            session_id: 会话 ID
        
        Returns:
            SessionInfo 对象，不存在返回 None
        """
        # 先尝试从缓存获取
        if session_id in self._sessions:
            session_info = self._sessions[session_id]
            if not session_info.is_expired(self.default_ttl):
                session_info.last_active = time.time()
                return session_info
            else:
                del self._sessions[session_id]
        
        # 从文件加载
        session_info = self._load_session(session_id)
        if session_info and not session_info.is_expired(self.default_ttl):
            self._sessions[session_id] = session_info
            session_info.last_active = time.time()
            return session_info
        
        return None
    
    def update_session(self, session_id: str, url: str = None, title: str = None,
                       is_logged_in: bool = None, user_info: Dict[str, Any] = None) -> SessionInfo:
        """
        更新会话信息
        
        Args:
            session_id: 会话 ID
            url: 新 URL
            title: 新标题
            is_logged_in: 登录状态
            user_info: 用户信息
        
        Returns:
            更新后的 SessionInfo
        """
        session_info = self.get_session(session_id)
        if not session_info:
            session_info = self.create_session(session_id)
        
        if url:
            session_info.url = url
        if title:
            session_info.title = title
        if is_logged_in is not None:
            session_info.is_logged_in = is_logged_in
        if user_info is not None:
            session_info.user_info = user_info
        session_info.last_active = time.time()
        
        self._save_session(session_info)
        self._sessions[session_id] = session_info
        return session_info
    
    def save_session_cookies(self, session_id: str) -> int:
        """
        保存当前会话的 Cookie
        
        Args:
            session_id: 会话 ID
        
        Returns:
            保存的 Cookie 数量
        """
        cookies = self._cookie_manager.get_cookies()
        session_info = self.get_session(session_id)
        if session_info:
            session_info.cookies = cookies
            self._save_session(session_info)
        return len(cookies)
    
    def restore_session_cookies(self, session_id: str, url: str = None) -> int:
        """
        恢复会话的 Cookie
        
        Args:
            session_id: 会话 ID
            url: 目标 URL
        
        Returns:
            恢复的 Cookie 数量
        """
        session_info = self.get_session(session_id)
        if session_info and session_info.cookies:
            self._cookie_manager.set_cookies(session_info.cookies, url)
            return len(session_info.cookies)
        return 0
    
    def delete_session(self, session_id: str) -> bool:
        """
        删除会话
        
        Args:
            session_id: 会话 ID
        
        Returns:
            是否成功
        """
        if session_id in self._sessions:
            del self._sessions[session_id]
        
        # 删除文件
        safe_id = self._safe_filename(session_id)
        file_path = os.path.join(self.storage_dir, f"{safe_id}.json")
        if os.path.exists(file_path):
            os.remove(file_path)
        
        logger.info(f"删除会话: {session_id}")
        return True
    
    def list_sessions(self) -> List[SessionInfo]:
        """
        列出所有有效会话
        
        Returns:
            SessionInfo 列表
        """
        valid_sessions = []
        for session_id in list(self._sessions.keys()):
            session_info = self.get_session(session_id)
            if session_info:
                valid_sessions.append(session_info)
        
        # 也扫描文件
        for filename in os.listdir(self.storage_dir):
            if filename.endswith(".json"):
                session_id = self._unsafe_filename(filename[:-5])
                if session_id not in self._sessions:
                    session_info = self._load_session(session_id)
                    if session_info:
                        valid_sessions.append(session_info)
        
        return valid_sessions
    
    def check_session_valid(self, session_id: str) -> bool:
        """
        检查会话是否有效
        
        Args:
            session_id: 会话 ID
        
        Returns:
            是否有效
        """
        session_info = self.get_session(session_id)
        if not session_info:
            return False
        
        # 检查 Cookie 是否过期
        for cookie in session_info.cookies:
            if cookie.is_expired():
                logger.warning(f"会话 {session_id} 的 Cookie 已过期: {cookie.name}")
                return False
        
        return True
    
    def auto_restore(self, session_id: str, url: str = None) -> bool:
        """
        自动恢复会话
        
        Args:
            session_id: 会话 ID
            url: 目标 URL
        
        Returns:
            是否成功恢复
        """
        session_info = self.get_session(session_id)
        if not session_info:
            logger.warning(f"会话不存在: {session_id}")
            return False
        
        # 恢复 Cookie
        cookies_restored = self.restore_session_cookies(session_id, url)
        if cookies_restored == 0:
            logger.warning(f"会话 {session_id} 无 Cookie 可恢复")
            return False
        
        logger.info(f"会话 {session_id} 恢复成功，恢复 {cookies_restored} 个 Cookie")
        return True
    
    def _get_current_url(self) -> str:
        """获取当前 URL"""
        try:
            result = self.session.send("Runtime.evaluate", {"expression": "location.href"})
            return result.get("result", {}).get("value", "")
        except Exception:
            return ""
    
    def _get_current_title(self) -> str:
        """获取当前页面标题"""
        try:
            result = self.session.send("Runtime.evaluate", {"expression": "document.title"})
            return result.get("result", {}).get("value", "")
        except Exception:
            return ""
    
    def _save_session(self, session_info: SessionInfo) -> None:
        """保存会话到文件"""
        safe_id = self._safe_filename(session_info.session_id)
        file_path = os.path.join(self.storage_dir, f"{safe_id}.json")
        
        data = session_info.to_dict()
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    
    def _load_session(self, session_id: str) -> Optional[SessionInfo]:
        """从文件加载会话"""
        safe_id = self._safe_filename(session_id)
        file_path = os.path.join(self.storage_dir, f"{safe_id}.json")
        
        if not os.path.exists(file_path):
            return None
        
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        
        return SessionInfo.from_dict(data)
    
    def _safe_filename(self, key: str) -> str:
        """将 ID 转换为安全的文件名"""
        return key.replace("/", "_").replace("\\", "_").replace(":", "_")
    
    def _unsafe_filename(self, key: str) -> str:
        """将文件名转换回 ID"""
        return key.replace("_", "/").replace("__", "\\").replace("___", ":")


# 便捷函数
def get_session_manager(session, storage_dir: str = None, default_ttl: int = 86400) -> SessionManager:
    """获取会话管理器实例"""
    return SessionManager(session, storage_dir, default_ttl)


def create_session(session, session_id: str, url: str = None, title: str = None) -> SessionInfo:
    """创建新会话"""
    mgr = get_session_manager(session)
    return mgr.create_session(session_id, url, title)


def restore_session(session, session_id: str, url: str = None) -> bool:
    """恢复会话"""
    mgr = get_session_manager(session)
    return mgr.auto_restore(session_id, url)
