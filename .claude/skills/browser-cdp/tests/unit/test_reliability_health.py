"""
reliability/health.py 单元测试

测试覆盖：
- ConnectionHealthChecker 连接健康检查器
- ConnectionPoolHealthChecker 连接池健康检查器
- 自动重连逻辑
- 后台健康检查循环
"""
import pytest
import sys
import asyncio
import time
from pathlib import Path
from unittest.mock import patch, Mock, MagicMock, AsyncMock

# 添加 skill 目录到路径
SKILL_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from src.reliability.health import ConnectionHealthChecker, ConnectionPoolHealthChecker
from src.reliability.error import CDPConnectionLostError


class MockCDPClient:
    """模拟 CDP 客户端"""
    
    def __init__(self, fail_count=0):
        self._fail_count = fail_count
        self._call_count = 0
        self._reconnect_called = False
    
    async def send(self, method: str, params: dict = None):
        """模拟 CDP 命令发送"""
        self._call_count += 1
        if self._call_count <= self._fail_count:
            raise Exception("Connection lost")
        return {"result": {"result": {"value": 2}}}
    
    async def reconnect(self):
        """模拟重连"""
        self._reconnect_called = True
        self._fail_count = 0
        self._call_count = 0
    
    @property
    def reconnect_called(self):
        return self._reconnect_called


class MockConnectionPool:
    """模拟连接池"""

    def __init__(self, session=None):
        self._session = session or MockCDPClient()
        self.sessions = {"https://example.com": self._session}

    async def get_session(self, url: str):
        """获取会话"""
        return self.sessions.get(url, self._session)

    async def send(self, method: str, params: dict = None):
        """转发到 session"""
        return await self._session.send(method, params)


class TestConnectionHealthChecker:
    """ConnectionHealthChecker 测试"""
    
    def setup_method(self):
        """每个测试前初始化"""
        self.client = MockCDPClient()
        self.checker = ConnectionHealthChecker(
            self.client,
            ping_interval=0.1,
            ping_timeout=0.5,
            max_reconnect_attempts=3,
            reconnect_delay=0.1,
        )
    
    @pytest.mark.asyncio
    async def test_health_check_success(self):
        """测试健康检查成功"""
        result = await self.checker.health_check()
        assert result["healthy"] is True
        assert result["error"] is None
        # latency_ms 可能为 0（Mock 响应很快）
    
    @pytest.mark.asyncio
    async def test_health_check_failure(self):
        """测试健康检查失败"""
        client = MockCDPClient(fail_count=1)
        checker = ConnectionHealthChecker(client)
        result = await checker.health_check()
        assert result["healthy"] is False
        assert result["error"] is not None
    
    @pytest.mark.asyncio
    async def test_auto_reconnect_success(self):
        """测试自动重连成功"""
        result = await self.checker.auto_reconnect()
        assert result is True
        assert self.client.reconnect_called
    
    @pytest.mark.asyncio
    async def test_auto_reconnect_max_attempts(self):
        """测试达到最大重连次数"""
        # 模拟没有 reconnect 方法的客户端
        class NoReconnectClient:
            pass
        checker = ConnectionHealthChecker(
            NoReconnectClient(),
            max_reconnect_attempts=1,
        )
        # 第一次重连（没有 reconnect 方法，返回 False）
        result = await checker.auto_reconnect()
        assert result is False
        # 第二次应该被阻止（达到 max_reconnect_attempts）
        result = await checker.auto_reconnect()
        assert result is False
    
    def test_get_status(self):
        """测试状态获取"""
        status = self.checker.get_status()
        assert "healthy" in status
        assert "last_ping_latency_ms" in status
        assert "reconnect_attempts" in status
        assert "total_failures" in status
        assert "total_successes" in status
    
    @pytest.mark.asyncio
    async def test_start_stop_background_check(self):
        """测试后台健康检查启动和停止"""
        await self.checker.start_background_check()
        assert self.checker._running is True
        assert self.checker._health_task is not None
        
        await self.checker.stop_background_check()
        assert self.checker._running is False
    
    def test_reset_stats(self):
        """测试重置统计信息"""
        self.checker._total_failures = 5
        self.checker._total_successes = 10
        self.checker.reset_stats()
        assert self.checker._total_failures == 0
        assert self.checker._total_successes == 0


class TestConnectionPoolHealthChecker:
    """ConnectionPoolHealthChecker 测试"""
    
    def setup_method(self):
        """每个测试前初始化"""
        self.pool = MockConnectionPool(MockCDPClient())
        self.checker = ConnectionPoolHealthChecker(self.pool)
    
    @pytest.mark.asyncio
    async def test_get_healthy_session(self):
        """测试获取健康会话"""
        session = await self.checker.get_healthy_session("https://example.com")
        assert session is not None
    
    @pytest.mark.asyncio
    async def test_get_healthy_session_reconnect(self):
        """测试获取会话时自动重连"""
        # 模拟连接不健康但有 reconnect 方法
        class ReconnectableClient(MockCDPClient):
            async def reconnect(self):
                self._fail_count = 0
                self._call_count = 0

        unhealthy_pool = MockConnectionPool(ReconnectableClient(fail_count=1))
        checker = ConnectionPoolHealthChecker(unhealthy_pool)

        # 重连成功
        session = await checker.get_healthy_session("https://example.com")
        assert session is not None

    @pytest.mark.asyncio
    async def test_get_healthy_session_reconnect_failure(self):
        """测试获取会话时重连失败"""
        # 模拟连接不健康且无 reconnect 方法
        class NoReconnectClient(MockCDPClient):
            pass

        unhealthy_pool = MockConnectionPool(NoReconnectClient(fail_count=1))
        checker = ConnectionPoolHealthChecker(unhealthy_pool)

        # 重连失败应抛出异常
        with pytest.raises(CDPConnectionLostError):
            await checker.get_healthy_session("https://example.com")
    
    @pytest.mark.asyncio
    async def test_check_all_sessions(self):
        """测试检查所有会话健康状态"""
        results = await self.checker.check_all_sessions()
        assert "https://example.com" in results
        assert results["https://example.com"]["healthy"] is True


class TestHealthCheckerIntegration:
    """健康检查器集成测试"""
    
    @pytest.mark.asyncio
    async def test_health_check_loop(self):
        """测试健康检查循环"""
        client = MockCDPClient()
        checker = ConnectionHealthChecker(client, ping_interval=0.05)
        
        await checker.start_background_check()
        await asyncio.sleep(0.15)  # 等待几次检查
        await checker.stop_background_check()
        
        # 验证至少执行了一次健康检查
        assert checker._total_successes > 0 or checker._total_failures > 0
    
    @pytest.mark.asyncio
    async def test_auto_reconnect_on_unhealthy(self):
        """测试连接不健康时自动重连"""
        client = MockCDPClient(fail_count=1)
        checker = ConnectionHealthChecker(
            client,
            ping_interval=0.05,
            max_reconnect_attempts=3,
        )
        
        await checker.start_background_check()
        await asyncio.sleep(0.2)  # 等待检查和重连
        await checker.stop_background_check()
        
        # 验证重连被尝试
        assert client.reconnect_called


if __name__ == "__main__":
    pytest.main([__file__, '-v'])
