# -*- coding: utf-8 -*-
"""
错误恢复模块

提供多级恢复策略：自动重试 → 降级处理 → 人工介入
支持故障转移和会话恢复。
"""

import json
import logging
import os
from abc import ABC, abstractmethod
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from .error import ReliabilityError, categorize_error, ErrorCategory

logger = logging.getLogger(__name__)


class RecoveryLevel(Enum):
    """恢复级别"""
    AUTO_RETRY = "auto_retry"
    DEGRADATION = "degradation"
    MANUAL = "manual"


class RecoveryStrategy(ABC):
    """恢复策略基类"""
    
    @abstractmethod
    def can_handle(self, error: Exception) -> bool:
        pass
    
    @abstractmethod
    def execute(self, error: Exception, context: Dict[str, Any]) -> Tuple[bool, Any]:
        pass


class AutoRetryStrategy(RecoveryStrategy):
    """Level 1: 自动重试策略"""
    
    def __init__(self, max_retries: int = 3):
        self.max_retries = max_retries
        self.retry_counts: Dict[str, int] = {}
    
    def can_handle(self, error: Exception) -> bool:
        if isinstance(error, ReliabilityError):
            return error.recoverable
        return True
    
    def execute(self, error: Exception, context: Dict[str, Any]) -> Tuple[bool, Any]:
        error_type = type(error).__name__
        self.retry_counts[error_type] = self.retry_counts.get(error_type, 0) + 1
        if self.retry_counts[error_type] > self.max_retries:
            logger.warning(f"Auto-retry exhausted for {error_type}: {self.retry_counts[error_type]}/{self.max_retries}")
            return False, None
        logger.info(f"Auto-retry attempt {self.retry_counts[error_type]} for {error_type}")
        return True, None


class DegradationStrategy(RecoveryStrategy):
    """Level 2: 降级处理策略"""
    
    def __init__(self):
        self._fallback_handlers: Dict[str, Callable] = {}
    
    def register_fallback(self, operation: str, handler: Callable):
        """注册降级处理器"""
        self._fallback_handlers[operation] = handler
        logger.info(f"Registered fallback handler for {operation}")
    
    def can_handle(self, error: Exception) -> bool:
        # 不可恢复的错误应交由人工介入处理
        if isinstance(error, ReliabilityError) and not error.recoverable:
            return False
        return True
    
    def execute(self, error: Exception, context: Dict[str, Any]) -> Tuple[bool, Any]:
        operation = context.get("operation", "unknown")
        if operation in self._fallback_handlers:
            try:
                logger.info(f"Executing fallback handler for {operation}")
                result = self._fallback_handlers[operation](error, context)
                return True, result
            except Exception as e:
                logger.error(f"Fallback handler failed for {operation}: {e}")
                return False, None
        logger.warning(f"No fallback handler for {operation}, returning empty result")
        return True, {"status": "degraded", "data": None, "error": str(error)}


class ManualInterventionStrategy(RecoveryStrategy):
    """Level 3: 人工介入策略"""
    
    def __init__(self, alert_manager=None):
        self.alert_manager = alert_manager
        self._saved_contexts: List[Dict] = []
    
    def can_handle(self, error: Exception) -> bool:
        if isinstance(error, ReliabilityError):
            return not error.recoverable
        return False
    
    def execute(self, error: Exception, context: Dict[str, Any]) -> Tuple[bool, Any]:
        saved = self._save_context(error, context)
        if self.alert_manager:
            self.alert_manager.send_alert(error, context)
        logger.error(f"Manual intervention required, context saved: {saved}")
        return True, {"status": "manual_intervention", "saved_context": saved}
    
    def _save_context(self, error: Exception, context: Dict[str, Any]) -> str:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"error_context_{timestamp}.json"
        filepath = Path(".claude/skills/browser-cdp/output/errors") / filename
        filepath.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "timestamp": timestamp,
            "error_type": type(error).__name__,
            "error_message": str(error),
            "category": categorize_error(error).value if isinstance(error, ReliabilityError) else "unknown",
            "context": context,
        }
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        self._saved_contexts.append(str(filepath))
        return str(filepath)
    
    def set_alert_manager(self, alert_manager):
        self.alert_manager = alert_manager


class FailoverManager:
    """故障转移管理器 - 支持健康检查和自动切换"""
    
    def __init__(self, primary: str, backups: List[str]):
        self.primary = primary
        self.backups = backups
        self.current_index = 0
        self.failed_sites: set = set()
        self._health_checks: Dict[str, Callable] = {}
        self._last_check_time: Dict[str, float] = {}
        self._check_interval = 60.0  # 健康检查间隔（秒）
    
    def register_health_check(self, site: str, check_fn: Callable):
        """注册健康检查函数"""
        self._health_checks[site] = check_fn
    
    def _check_health(self, site: str) -> bool:
        """执行健康检查"""
        now = time.time()
        last_check = self._last_check_time.get(site, 0)
        
        # 缓存检查结果，避免频繁检查
        if now - last_check < self._check_interval and site in self._health_checks:
            return True  # 使用缓存结果
        
        self._last_check_time[site] = now
        
        if site not in self._health_checks:
            return True  # 无检查函数，默认健康
        
        try:
            result = self._health_checks[site]()
            if result:
                self.failed_sites.discard(site)
            else:
                self.failed_sites.add(site)
            return result
        except Exception as e:
            logger.warning(f"Health check failed for {site}: {e}")
            self.failed_sites.add(site)
            return False
    
    def get_next_site(self) -> str:
        """获取下一个可用网站"""
        # 检查当前站点健康状态
        current_site = self.backups[self.current_index - 1] if self.current_index > 0 else self.primary
        if self._check_health(current_site):
            return current_site
        
        # 尝试备用站点
        for i in range(len(self.backups)):
            idx = (self.current_index + i) % len(self.backups)
            site = self.backups[idx]
            if self._check_health(site):
                self.current_index = idx + 1
                logger.info(f"Failover to backup site: {site}")
                return site
        
        # 所有备用站点都失败，回到主站点
        logger.warning("All backup sites failed, returning to primary")
        self.current_index = 0
        return self.primary
    
    def mark_failed(self, site: str):
        """标记网站失败"""
        self.failed_sites.add(site)
        logger.warning(f"Site marked as failed: {site}")
    
    def mark_success(self, site: str):
        """标记网站成功"""
        self.failed_sites.discard(site)
    
    def get_status(self) -> Dict[str, Any]:
        return {
            "primary": self.primary,
            "backups": self.backups,
            "current_site": self.get_next_site(),
            "failed_sites": list(self.failed_sites),
            "health_checks_registered": len(self._health_checks),
        }


class SessionRecovery:
    """会话恢复管理器"""
    
    def __init__(self, session_dir: str):
        self.session_dir = session_dir
        self.cookies_file = os.path.join(session_dir, "cookies.json")
        self.storage_file = os.path.join(session_dir, "storage.json")
        self.state_file = os.path.join(session_dir, "state.json")
    
    def save_session(self, browser_state: Dict[str, Any]):
        Path(self.session_dir).mkdir(parents=True, exist_ok=True)
        with open(self.cookies_file, 'w', encoding='utf-8') as f:
            json.dump(browser_state.get("cookies", {}), f, ensure_ascii=False)
        with open(self.storage_file, 'w', encoding='utf-8') as f:
            json.dump(browser_state.get("storage", {}), f, ensure_ascii=False)
        with open(self.state_file, 'w', encoding='utf-8') as f:
            json.dump({"timestamp": datetime.now().isoformat(), "url": browser_state.get("url"), "title": browser_state.get("title")}, f, ensure_ascii=False, indent=2)
        logger.info(f"Session saved to {self.session_dir}")
    
    def restore_session(self) -> Optional[Dict[str, Any]]:
        if not os.path.exists(self.cookies_file):
            logger.debug("No saved session found")
            return None
        try:
            with open(self.cookies_file, 'r', encoding='utf-8') as f:
                cookies = json.load(f)
            with open(self.storage_file, 'r', encoding='utf-8') as f:
                storage = json.load(f)
            logger.info(f"Session restored: {len(cookies)} cookies, {len(storage)} storage items")
            return {"cookies": cookies, "storage": storage}
        except Exception as e:
            logger.error(f"Failed to restore session: {e}")
            return None


class MultiLevelRecovery:
    """
    多级恢复管理器
    
    恢复流程：
    Level 1: 自动重试（最多 3 次）
        ↓ 失败
    Level 2: 降级处理（使用缓存/备用方案）
        ↓ 失败
    Level 3: 人工介入（告警 + 保存现场）
    """
    
    def __init__(self):
        self.auto_retry = AutoRetryStrategy()
        self.degradation = DegradationStrategy()
        self.manual = ManualInterventionStrategy()
        self._strategies = [self.auto_retry, self.degradation, self.manual]
    
    def recover(self, error: Exception, context: Dict[str, Any], max_level: RecoveryLevel = RecoveryLevel.MANUAL) -> Tuple[bool, Any]:
        level_order = [RecoveryLevel.AUTO_RETRY, RecoveryLevel.DEGRADATION, RecoveryLevel.MANUAL]
        max_idx = level_order.index(max_level)
        for i, level in enumerate(level_order[:max_idx + 1]):
            strategy = self._strategies[i]
            if not strategy.can_handle(error):
                continue
            logger.info(f"Attempting recovery at level: {level.value}")
            success, result = strategy.execute(error, context)
            if success:
                logger.info(f"Recovery successful at level: {level.value}")
                return True, result
            logger.warning(f"Recovery failed at level: {level.value}, trying next level")
        logger.error("All recovery levels exhausted")
        return False, None
    
    def register_fallback(self, operation: str, handler: Callable):
        self.degradation.register_fallback(operation, handler)
    
    def set_alert_manager(self, alert_manager):
        self.manual.set_alert_manager(alert_manager)


_global_recovery_manager: Optional[MultiLevelRecovery] = None


def get_recovery_manager() -> MultiLevelRecovery:
    global _global_recovery_manager
    if _global_recovery_manager is None:
        _global_recovery_manager = MultiLevelRecovery()
    return _global_recovery_manager


def reset_recovery_manager():
    global _global_recovery_manager
    _global_recovery_manager = None
