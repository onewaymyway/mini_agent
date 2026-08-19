"""
session_manager_v2.py - 增强版会话管理器

修复:
- PROFILE-004: Chrome 分区 Cookie 支持（Storage API）
- PROFILE-005: Profile 健康检查与自动修复
- 带验证的会话恢复
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any

from .session_manager import SessionManager, SessionInfo, CookieManager, CookieInfo

logger = logging.getLogger(__name__)


# Profile 锁文件列表
PROFILE_LOCK_FILES = [
    "SingletonLock",
    "SingletonSocket",
    "SingletonCookie",
    "SingletonPipe",
    "Singletonshm",
]


class SessionManagerV2(SessionManager):
    """
    增强版会话管理器
    
    改进:
    - Profile 健康检查和自动修复
    - Chrome 分区 Cookie 支持
    - 会话状态智能恢复
    """
    
    def __init__(self, session, storage_dir: str = None, default_ttl: int = 86400):
        super().__init__(session, storage_dir, default_ttl)
        self._profile_path: Optional[str] = None
        self._cookie_version: Optional[str] = None
    
    def set_profile_path(self, profile_path: str) -> None:
        """设置 Chrome 用户数据目录路径"""
        self._profile_path = profile_path
        logger.info(f"设置 Profile 路径: {profile_path}")
    
    async def ensure_profile_health(self) -> bool:
        """
        检查并修复 Chrome 用户数据目录
        
        修复内容:
        - 清理 SingletonLock/SingletonSocket/SingletonCookie 等锁文件
        - 检查 Cookies 数据库是否可读
        - 检查 Local State 文件是否损坏
        
        Returns:
            bool: 是否健康（或已修复）
        """
        if not self._profile_path:
            logger.warning("未设置 Profile 路径，跳过健康检查")
            return True
        
        if not os.path.exists(self._profile_path):
            logger.error(f"Profile 目录不存在: {self._profile_path}")
            return False
        
        cleaned = 0
        
        # 1. 清理锁文件
        for lock_file in PROFILE_LOCK_FILES:
            lock_path = os.path.join(self._profile_path, lock_file)
            if os.path.exists(lock_path):
                try:
                    os.remove(lock_path)
                    cleaned += 1
                    logger.info(f"清理锁文件: {lock_file}")
                except Exception as e:
                    logger.warning(f"清理锁文件失败 {lock_file}: {e}")
        
        # 2. 检查 Cookies 数据库
        cookies_db = os.path.join(self._profile_path, "Cookies")
        if os.path.exists(cookies_db):
            try:
                with open(cookies_db, 'rb') as f:
                    header = f.read(100)
                    if not header or len(header) < 13:
                        logger.error("Cookies 数据库文件过小，可能损坏")
                        return False
            except Exception as e:
                logger.error(f"Cookies 数据库读取失败: {e}")
                return False
        
        # 3. 检查 Local State
        local_state = os.path.join(self._profile_path, "Local State")
        if os.path.exists(local_state):
            try:
                with open(local_state, 'r', encoding='utf-8') as f:
                    json.load(f)
            except json.JSONDecodeError as e:
                logger.error(f"Local State 文件损坏: {e}")
                return False
            except Exception as e:
                logger.warning(f"Local State 读取失败: {e}")
        
        if cleaned > 0:
            logger.info(f"Profile 健康检查完成，清理了 {cleaned} 个锁文件")
        else:
            logger.debug("Profile 健康检查通过，无需修复")
        
        return True
    
    async def get_cookies_v2(self) -> List[CookieInfo]:
        """
        使用 Storage API 获取 Cookie（支持 Partitioned Cookies）
        
        替代 Network.getCookies，支持更多 Cookie 类型
        """
        # 尝试 Storage.getCookies (支持 Partitioned)
        try:
            result = self.session.send("Storage.getCookies", {
                "sources": ["regular", "protected_origin"]
            })
            cookies = result.get("cookies", [])
            if cookies:
                logger.debug(f"Storage.getCookies 返回 {len(cookies)} 个 Cookie")
                return [CookieInfo.from_cdp(cookie) for cookie in cookies]
        except Exception as e:
            logger.debug(f"Storage.getCookies 不可用: {e}，降级到 Network.getCookies")
        
        # 降级到父类的 Network.getCookies
        return await super().get_cookies_v2()
    
    async def restore_with_validation(
        self,
        session_id: str,
        url: str = None,
        validate_login: bool = True,
    ) -> bool:
        """
        带验证的会话恢复
        
        1. 恢复 Cookie
        2. 导航到目标 URL
        3. 检测登录状态
        4. 如果未登录，标记为需要重新登录
        
        Returns:
            bool: 是否成功恢复
        """
        # 恢复 Cookie
        cookies_restored = self.restore_session_cookies(session_id, url)
        if cookies_restored == 0:
            logger.warning(f"会话 {session_id} 无 Cookie 可恢复")
            return False
        
        logger.info(f"会话 {session_id} 恢复了 {cookies_restored} 个 Cookie")
        
        # 导航到目标页面
        target_url = url or self._get_current_url()
        try:
            self.session.send("Page.navigate", {"url": target_url})
        except Exception as e:
            logger.error(f"导航失败: {e}")
            return False
        
        # 等待页面加载
        time.sleep(2.0)
        
        if not validate_login:
            logger.info(f"会话 {session_id} 恢复成功（跳过登录验证）")
            return True
        
        # 检测登录状态
        from .login_state_detector import LoginStateDetector
        detector = LoginStateDetector(self.session)
        state = detector.check_login_state()
        
        if state.is_logged_in and state.confidence >= 0.7:
            logger.info(f"会话 {session_id} 恢复成功，登录态有效 (confidence={state.confidence:.2f})")
            # 更新会话信息
            self.update_session(session_id, url=target_url, is_logged_in=True)
            return True
        elif state.login_required if hasattr(state, 'login_required') else state.confidence < 0.5:
            logger.warning(f"会话 {session_id} 恢复后需要重新登录")
            self._mark_needs_relogin(session_id)
            return False
        else:
            logger.info(f"会话 {session_id} 恢复成功（免登录站点，confidence={state.confidence:.2f}）")
            return True
    
    def _mark_needs_relogin(self, session_id: str) -> None:
        """标记会话需要重新登录"""
        session_info = self.get_session(session_id)
        if session_info:
            session_info.is_logged_in = False
            if 'details' not in session_info.__dict__:
                session_info.__dict__['details'] = {}
            session_info.__dict__['details']['needs_relogin'] = True
            self._save_session(session_info)
    
    async def health_check(self, session_id: str) -> Dict[str, Any]:
        """
        会话健康检查
        
        Returns:
            dict: 健康状态报告
        """
        report = {
            "session_id": session_id,
            "timestamp": time.time(),
            "profile_health": False,
            "cookies_valid": False,
            "login_valid": False,
            "issues": [],
        }
        
        # 检查 Profile
        if self._profile_path:
            profile_ok = await self.ensure_profile_health()
            report["profile_health"] = profile_ok
            if not profile_ok:
                report["issues"].append("Profile 目录不健康")
        
        # 检查 Cookie
        session_info = self.get_session(session_id)
        if session_info and session_info.cookies:
            expired_count = sum(1 for c in session_info.cookies if c.is_expired())
            report["cookies_count"] = len(session_info.cookies)
            report["cookies_expired"] = expired_count
            report["cookies_valid"] = expired_count == 0
            if expired_count > 0:
                report["issues"].append(f"{expired_count} 个 Cookie 已过期")
        else:
            report["issues"].append("无 Cookie")
        
        return report


# 便捷函数
def create_session_manager_v2(session, storage_dir=None, default_ttl=86400) -> SessionManagerV2:
    """创建增强版会话管理器实例"""
    return SessionManagerV2(session, storage_dir, default_ttl)


def ensure_profile_clean(profile_path: str) -> bool:
    """
    确保 Profile 目录无锁文件（可在浏览器启动前调用）
    
    Args:
        profile_path: Chrome 用户数据目录路径
    
    Returns:
        bool: 是否清理成功
    """
    if not os.path.exists(profile_path):
        return True
    
    cleaned = 0
    for lock_file in PROFILE_LOCK_FILES:
        lock_path = os.path.join(profile_path, lock_file)
        if os.path.exists(lock_path):
            try:
                os.remove(lock_path)
                cleaned += 1
                logger.info(f"启动前清理锁文件: {lock_file}")
            except Exception as e:
                logger.warning(f"启动前清理失败 {lock_file}: {e}")
    
    return cleaned >= 0  # 即使没清理也返回 True（不阻断启动）
