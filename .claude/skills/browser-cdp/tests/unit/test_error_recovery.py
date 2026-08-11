# -*- coding: utf-8 -*-
"""
错误恢复模块单元测试
"""

import pytest
from unittest.mock import Mock, patch
from typing import Tuple, Any, Dict

from src.reliability.error_recovery import (
    RecoveryLevel,
    AutoRetryStrategy,
    DegradationStrategy,
    ManualInterventionStrategy,
    FailoverManager,
    SessionRecovery,
    MultiLevelRecovery,
    get_recovery_manager,
    reset_recovery_manager,
)
from src.reliability.error import (
    CDPConnectionLostError,
    CaptchaDetectedError,
    ElementNotFoundError,
    ReliabilityError,
    ErrorCategory,
)


class TestAutoRetryStrategy:
    """AutoRetryStrategy 测试"""
    
    def test_can_handle_recoverable_error(self):
        strategy = AutoRetryStrategy()
        error = CDPConnectionLostError()
        assert strategy.can_handle(error) == True
    
    def test_can_handle_non_recoverable_error(self):
        strategy = AutoRetryStrategy()
        error = CaptchaDetectedError()
        assert strategy.can_handle(error) == False
    
    def test_can_handle_generic_exception(self):
        strategy = AutoRetryStrategy()
        error = ValueError("test")
        assert strategy.can_handle(error) == True
    
    def test_execute_within_limit(self):
        strategy = AutoRetryStrategy(max_retries=3)
        error = CDPConnectionLostError()
        
        success, result = strategy.execute(error, {"operation": "test"})
        assert success == True
        assert strategy.retry_counts["CDPConnectionLostError"] == 1
    
    def test_execute_exhausted(self):
        strategy = AutoRetryStrategy(max_retries=2)
        error = CDPConnectionLostError()
        
        # First two attempts should succeed
        success1, _ = strategy.execute(error, {})
        success2, _ = strategy.execute(error, {})
        assert success1 == True
        assert success2 == True
        
        # Third attempt should fail
        success3, _ = strategy.execute(error, {})
        assert success3 == False


class TestDegradationStrategy:
    """DegradationStrategy 测试"""
    
    def test_can_handle_all_errors(self):
        strategy = DegradationStrategy()
        assert strategy.can_handle(CDPConnectionLostError()) == True
        # 不可恢复错误应交由人工介入处理
        assert strategy.can_handle(CaptchaDetectedError()) == False
        assert strategy.can_handle(ValueError("test")) == True
    
    def test_execute_with_fallback_handler(self):
        strategy = DegradationStrategy()
        
        def fallback_handler(error, context):
            return {"fallback": True, "error": str(error)}
        
        strategy.register_fallback("search", fallback_handler)
        
        success, result = strategy.execute(CDPConnectionLostError(), {"operation": "search"})
        assert success == True
        assert result["fallback"] == True
    
    def test_execute_without_fallback_handler(self):
        strategy = DegradationStrategy()
        
        success, result = strategy.execute(CDPConnectionLostError(), {"operation": "unknown"})
        assert success == True
        assert result["status"] == "degraded"
        assert result["data"] is None


class TestManualInterventionStrategy:
    """ManualInterventionStrategy 测试"""
    
    def test_can_handle_non_recoverable_error(self):
        strategy = ManualInterventionStrategy()
        error = CaptchaDetectedError()
        assert strategy.can_handle(error) == True
    
    def test_can_handle_recoverable_error(self):
        strategy = ManualInterventionStrategy()
        error = CDPConnectionLostError()
        assert strategy.can_handle(error) == False
    
    def test_execute_saves_context(self):
        strategy = ManualInterventionStrategy()
        error = CaptchaDetectedError()
        
        success, result = strategy.execute(error, {"operation": "search", "url": "https://example.com"})
        assert success == True
        assert result["status"] == "manual_intervention"
        assert "saved_context" in result
    
    def test_execute_with_alert_manager(self):
        strategy = ManualInterventionStrategy()
        alert_manager = Mock()
        strategy.set_alert_manager(alert_manager)
        
        error = CaptchaDetectedError()
        strategy.execute(error, {"operation": "test"})
        
        alert_manager.send_alert.assert_called_once()


class TestFailoverManager:
    """FailoverManager 测试"""
    
    def test_get_primary(self):
        manager = FailoverManager(primary="site_a", backups=["site_b", "site_c"])
        assert manager.get_next_site() == "site_b"
    
    def test_get_backup(self):
        manager = FailoverManager(primary="site_a", backups=["site_b", "site_c"])
        manager.get_next_site()  # site_b
        assert manager.get_next_site() == "site_c"
    
    def test_cycle_back_to_primary(self):
        manager = FailoverManager(primary="site_a", backups=["site_b"])
        manager.get_next_site()  # site_b
        assert manager.get_next_site() == "site_a"  # Back to primary
    
    def test_mark_failed(self):
        manager = FailoverManager(primary="site_a", backups=["site_b"])
        manager.mark_failed("site_b")
        assert "site_b" in manager.failed_sites
    
    def test_mark_success(self):
        manager = FailoverManager(primary="site_a", backups=["site_b"])
        manager.mark_failed("site_b")
        manager.mark_success("site_b")
        assert "site_b" not in manager.failed_sites
    
    def test_get_status(self):
        manager = FailoverManager(primary="site_a", backups=["site_b", "site_c"])
        manager.mark_failed("site_b")
        status = manager.get_status()
        assert status["primary"] == "site_a"
        assert "site_b" in status["failed_sites"]


class TestSessionRecovery:
    """SessionRecovery 测试"""
    
    def test_save_and_restore_session(self, tmp_path):
        session_dir = str(tmp_path / "session")
        recovery = SessionRecovery(session_dir)
        
        browser_state = {
            "cookies": [{"name": "session", "value": "123"}],
            "storage": {"key": "value"},
            "url": "https://example.com",
            "title": "Test Page",
        }
        
        recovery.save_session(browser_state)
        restored = recovery.restore_session()
        
        assert restored is not None
        assert restored["cookies"][0]["name"] == "session"
        assert restored["storage"]["key"] == "value"
    
    def test_restore_nonexistent_session(self, tmp_path):
        session_dir = str(tmp_path / "empty_session")
        recovery = SessionRecovery(session_dir)
        result = recovery.restore_session()
        assert result is None


class TestMultiLevelRecovery:
    """MultiLevelRecovery 测试"""
    
    def test_auto_retry_success(self):
        manager = MultiLevelRecovery()
        error = CDPConnectionLostError()
        
        success, result = manager.recover(error, {"operation": "test"}, max_level=RecoveryLevel.AUTO_RETRY)
        assert success == True
    
    def test_auto_retry_exhausted_falls_back(self):
        manager = MultiLevelRecovery()
        # Exhaust auto-retry
        error = CDPConnectionLostError()
        for _ in range(4):  # max_retries=3
            manager.recover(error, {"operation": "test"}, max_level=RecoveryLevel.AUTO_RETRY)
        
        # Now should fall back to degradation
        success, result = manager.recover(error, {"operation": "test"})
        assert success == True  # Degradation returns success with empty data
    
    def test_manual_intervention_for_captcha(self):
        manager = MultiLevelRecovery()
        error = CaptchaDetectedError()

        # Test manual level directly
        success, result = manager.recover(error, {"operation": "search"}, max_level=RecoveryLevel.MANUAL)
        assert success == True
        assert result["status"] == "manual_intervention"

    def test_register_fallback(self):
        manager = MultiLevelRecovery()

        def fallback(error, context):
            return {"fallback": True}

        manager.register_fallback("search", fallback)
        # Exhaust auto-retry first
        error = CDPConnectionLostError()
        for _ in range(4):
            manager.recover(error, {"operation": "search"}, max_level=RecoveryLevel.AUTO_RETRY)
        # Now test fallback
        success, result = manager.recover(error, {"operation": "search"})
        assert success == True
        assert result["fallback"] == True


class TestGlobalRecoveryManager:
    """全局恢复管理器测试"""
    
    def test_get_instance(self):
        reset_recovery_manager()
        manager1 = get_recovery_manager()
        manager2 = get_recovery_manager()
        assert manager1 is manager2
    
    def test_reset_instance(self):
        reset_recovery_manager()
        manager1 = get_recovery_manager()
        reset_recovery_manager()
        manager2 = get_recovery_manager()
        assert manager1 is not manager2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
