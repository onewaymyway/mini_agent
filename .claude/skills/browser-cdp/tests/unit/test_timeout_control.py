# -*- coding: utf-8 -*-
"""
超时控制模块单元测试
"""

import pytest
import asyncio
from unittest.mock import Mock, patch

from src.reliability.timeout_control import (
    TimeoutConfig,
    TimeoutManager,
    SmartWait,
    get_timeout_manager,
    async_with_timeout,
)


class TestTimeoutConfig:
    """TimeoutConfig 测试"""
    
    def test_default_config(self):
        config = TimeoutConfig()
        assert config.connect_timeout == 5.0
        assert config.read_timeout == 20.0
        assert config.total_timeout == 30.0
    
    def test_for_operation_navigation(self):
        config = TimeoutConfig.for_operation("navigation")
        assert config.connect_timeout == 10.0
        assert config.read_timeout == 30.0
        assert config.total_timeout == 60.0
    
    def test_for_operation_search(self):
        config = TimeoutConfig.for_operation("search")
        assert config.connect_timeout == 5.0
        assert config.total_timeout == 30.0
    
    def test_for_operation_unknown(self):
        config = TimeoutConfig.for_operation("unknown_operation")
        assert config.connect_timeout == 5.0  # Falls back to search config
    
    def test_to_dict(self):
        config = TimeoutConfig(connect_timeout=3.0, read_timeout=10.0, total_timeout=15.0)
        d = config.to_dict()
        assert d["connect"] == 3.0
        assert d["read"] == 10.0
        assert d["total"] == 15.0


class TestTimeoutManager:
    """TimeoutManager 测试"""
    
    def test_register_and_get(self):
        manager = TimeoutManager()
        config = TimeoutConfig(total_timeout=45.0)
        manager.register_timeout("custom_op", config)
        retrieved = manager.get_timeout("custom_op")
        assert retrieved.total_timeout == 45.0
    
    def test_default_timeout(self):
        manager = TimeoutManager()
        config = manager.get_timeout("search")
        assert config.total_timeout == 30.0
    
    def test_timeout_circuit(self):
        manager = TimeoutManager()
        manager.record_timeout("op1")
        manager.record_timeout("op1")
        assert manager.check_timeout_circuit("op1") == False  # 2 < 3
        manager.record_timeout("op1")
        assert manager.check_timeout_circuit("op1") == True  # 3 >= 3
    
    def test_record_success_resets_counter(self):
        manager = TimeoutManager()
        manager.record_timeout("op1")
        manager.record_timeout("op1")
        manager.record_success("op1")
        assert manager.check_timeout_circuit("op1") == False
    
    def test_get_status(self):
        manager = TimeoutManager()
        manager.register_timeout("search", TimeoutConfig())
        manager.record_timeout("search")
        status = manager.get_status()
        assert "search" in status["registered_operations"]
        assert status["timeout_failures"]["search"] == 1


class TestSmartWait:
    """SmartWait 测试"""
    
    def test_wait_for_true_condition(self):
        wait = SmartWait(default_timeout=1.0)
        result = wait.wait_for(lambda: True)
        assert result == True
    
    def test_wait_for_false_condition_timeout(self):
        wait = SmartWait(default_timeout=0.1, poll_interval=0.05)
        result = wait.wait_for(lambda: False)
        assert result == False
    
    def test_wait_for_eventually_true(self):
        call_count = [0]
        def condition():
            call_count[0] += 1
            return call_count[0] >= 3
        
        wait = SmartWait(default_timeout=1.0, poll_interval=0.05)
        result = wait.wait_for(condition)
        assert result == True
        assert call_count[0] >= 3
    
    @pytest.mark.asyncio
    async def test_async_wait_for(self):
        wait = SmartWait(default_timeout=1.0)
        result = await wait.async_wait_for(lambda: True)
        assert result == True


class TestAsyncWithTimeout:
    """async_with_timeout 测试"""
    
    @pytest.mark.asyncio
    async def test_success_within_timeout(self):
        async def fast_func():
            return "done"
        
        result = await async_with_timeout(fast_func, "search", timeout=5.0)
        assert result == "done"
    
    @pytest.mark.asyncio
    async def test_timeout_exceeded(self):
        async def slow_func():
            await asyncio.sleep(10)
            return "done"
        
        with pytest.raises(TimeoutError):
            await async_with_timeout(slow_func, "search", timeout=0.1)
    
    @pytest.mark.asyncio
    async def test_exception_propagated(self):
        async def failing_func():
            raise ValueError("test error")
        
        with pytest.raises(ValueError, match="test error"):
            await async_with_timeout(failing_func, "search", timeout=5.0)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
