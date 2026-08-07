# -*- coding: utf-8 -*-
"""
会话恢复管理器

提供浏览器会话状态的保存和恢复能力，支持认证状态和页面状态的持久化。
"""

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class SessionRecovery:
    """会话恢复管理器"""
    
    def __init__(self, session_dir: str):
        self.session_dir = session_dir
        self.cookies_file = os.path.join(session_dir, "cookies.json")
        self.storage_file = os.path.join(session_dir, "storage.json")
        self.state_file = os.path.join(session_dir, "state.json")
        self.history_file = os.path.join(session_dir, "history.json")
    
    def save_session(self, browser_state: Dict[str, Any]):
        """保存当前会话状态"""
        Path(self.session_dir).mkdir(parents=True, exist_ok=True)
        
        # 保存 cookies
        with open(self.cookies_file, 'w', encoding='utf-8') as f:
            json.dump(browser_state.get("cookies", {}), f, ensure_ascii=False)
        
        # 保存 localStorage/sessionStorage
        with open(self.storage_file, 'w', encoding='utf-8') as f:
            json.dump(browser_state.get("storage", {}), f, ensure_ascii=False)
        
        # 保存页面状态
        state_data = {
            "timestamp": datetime.now().isoformat(),
            "url": browser_state.get("url", ""),
            "title": browser_state.get("title", ""),
            "tabs": browser_state.get("tabs", []),
        }
        with open(self.state_file, 'w', encoding='utf-8') as f:
            json.dump(state_data, f, ensure_ascii=False, indent=2)
        
        # 更新访问历史
        self._append_history(browser_state.get("url", ""), browser_state.get("title", ""))
        
        logger.info(f"Session saved to {self.session_dir}")
    
    def restore_session(self) -> Optional[Dict[str, Any]]:
        """恢复会话状态"""
        if not os.path.exists(self.cookies_file):
            logger.debug("No saved session found")
            return None
        
        try:
            with open(self.cookies_file, 'r', encoding='utf-8') as f:
                cookies = json.load(f)
            
            with open(self.storage_file, 'r', encoding='utf-8') as f:
                storage = json.load(f)
            
            logger.info(f"Session restored: {len(cookies)} cookies, {len(storage)} storage items")
            return {
                "cookies": cookies,
                "storage": storage,
                "restored_at": datetime.now().isoformat(),
            }
        except Exception as e:
            logger.error(f"Failed to restore session: {e}")
            return None
    
    def get_last_state(self) -> Optional[Dict[str, Any]]:
        """获取上次保存的页面状态"""
        if not os.path.exists(self.state_file):
            return None
        try:
            with open(self.state_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Failed to read state: {e}")
            return None
    
    def _append_history(self, url: str, title: str):
        """追加访问历史"""
        history = []
        if os.path.exists(self.history_file):
            try:
                with open(self.history_file, 'r', encoding='utf-8') as f:
                    history = json.load(f)
            except:
                history = []
        
        history.append({
            "timestamp": datetime.now().isoformat(),
            "url": url,
            "title": title,
        })
        
        # 只保留最近 50 条
        history = history[-50:]
        
        with open(self.history_file, 'w', encoding='utf-8') as f:
            json.dump(history, f, ensure_ascii=False, indent=2)
    
    def get_history(self) -> List[Dict[str, Any]]:
        """获取访问历史"""
        if not os.path.exists(self.history_file):
            return []
        try:
            with open(self.history_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            return []
    
    def clear_session(self):
        """清除会话数据"""
        for f in [self.cookies_file, self.storage_file, self.state_file, self.history_file]:
            if os.path.exists(f):
                os.remove(f)
        logger.info(f"Session cleared from {self.session_dir}")


# 别名，兼容导入
SessionRecoveryManager = SessionRecovery


class RecoveryStrategy:
    """恢复策略基类"""
    pass


_global_session_recovery: Optional[SessionRecovery] = None


def get_session_recovery(session_dir: str) -> SessionRecovery:
    """获取或创建会话恢复管理器"""
    global _global_session_recovery
    if _global_session_recovery is None or _global_session_recovery.session_dir != session_dir:
        _global_session_recovery = SessionRecovery(session_dir)
    return _global_session_recovery


def reset_session_recovery():
    """重置全局会话恢复管理器"""
    global _global_session_recovery
    _global_session_recovery = None
