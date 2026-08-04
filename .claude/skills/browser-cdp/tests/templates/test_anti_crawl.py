"""
test_anti_crawl.py - 反爬机制测试

覆盖 stealth.py、request_headers.py、rate_limiter.py、proxy_pool.py 的所有新增功能。
"""
import asyncio
import pytest
import time
from unittest.mock import AsyncMock, MagicMock, patch
import sys
import os

# 添加 skill 目录到路径
SKILL_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SRC_DIR = os.path.join(SKILL_DIR, "src")
sys.path.insert(0, SRC_DIR)

from src.core.stealth import StealthMode, StealthConfig
from src.core.request_headers import RequestHeaderManager, HeaderConfig
from src.core.rate_limiter import RateLimiter, RateLimitConfig, RateLimitAlgorithm
from src.core.proxy_pool import ProxyPool, ProxyInfo, ProxyType, ProxyPoolConfig


class MockSession:
    """模拟 CDP session"""
    def __init__(self):
        self.eval_js_calls = []
        self.send_calls = []
    
    async def eval_js(self, js: str) -> any:
        self.eval_js_calls.append(js)
        return True
    
    async def send(self, method: str, params: dict) -> any:
        self.send_calls.append((method, params))
        return True


# =========================================================================
# StealthMode 测试
# =========================================================================

class TestStealthMode:
    """StealthMode 测试"""
    
    @pytest.mark.asyncio
    async def test_apply_all_features(self):
        """测试应用所有 stealth 功能"""
        session = MockSession()
        config = StealthConfig(
            enable_webdriver_removal=True,
            enable_chrome_runtime=True,
            enable_permissions_mock=True,
            enable_language_mock=True,
            enable_platform_mock=True,
            enable_plugins_mock=True,
            enable_fingerprint_mock=True,
        )
        stealth = StealthMode(session, config)
        result = await stealth.apply()
        
        assert result is True
        assert stealth._applied is True
        assert len(session.eval_js_calls) >= 7  # 至少 7 个 JS 脚本
    
    @pytest.mark.asyncio
    async def test_apply_skips_when_already_applied(self):
        """测试已应用时跳过"""
        session = MockSession()
        stealth = StealthMode(session)
        await stealth.apply()
        
        calls_before = len(session.eval_js_calls)
        await stealth.apply()
        
        assert len(session.eval_js_calls) == calls_before
    
    @pytest.mark.asyncio
    async def test_remove_webdriver(self):
        """测试移除 webdriver 属性"""
        session = MockSession()
        config = StealthConfig(enable_webdriver_removal=True)
        stealth = StealthMode(session, config)
        await stealth._remove_webdriver()
        
        assert len(session.eval_js_calls) == 1
        assert "webdriver" in session.eval_js_calls[0]
    
    @pytest.mark.asyncio
    async def test_mock_device_fingerprint(self):
        """测试设备指纹模拟"""
        session = MockSession()
        config = StealthConfig(enable_fingerprint_mock=True)
        stealth = StealthMode(session, config)
        await stealth._mock_device_fingerprint()
        
        assert len(session.eval_js_calls) == 1
        js = session.eval_js_calls[0]
        assert "deviceMemory" in js
        assert "hardwareConcurrency" in js
        assert "connection" in js
    
    @pytest.mark.asyncio
    async def test_human_like_click(self):
        """测试人类化点击"""
        session = MockSession()
        config = StealthConfig(humanize_mouse=True)
        stealth = StealthMode(session, config)
        await stealth.human_like_click(100, 200, duration=0.1, steps=5)
        
        # 应该有 mouseMoved + mousePressed + mouseReleased
        assert len(session.send_calls) >= 3
    
    @pytest.mark.asyncio
    async def test_human_like_type(self):
        """测试人类化输入"""
        session = MockSession()
        config = StealthConfig(humanize_typing=True)
        stealth = StealthMode(session, config)
        await stealth.human_like_type("hello", min_delay=0.01, max_delay=0.02)
        
        # 每个字符应该有 keyDown + char + keyUp
        assert len(session.send_calls) >= 15  # 5 字符 * 3 事件
    
    @pytest.mark.asyncio
    async def test_random_delay(self):
        """测试随机延迟"""
        session = MockSession()
        stealth = StealthMode(session)
        
        start = time.monotonic()
        await stealth.random_delay(min_seconds=0.01, max_seconds=0.02)
        elapsed = time.monotonic() - start
        
        assert 0.01 <= elapsed <= 0.05
    
    def test_get_random_user_agent(self):
        """测试随机 UA 获取"""
        session = MockSession()
        stealth = StealthMode(session)
        
        ua = stealth.get_random_user_agent()
        assert isinstance(ua, str)
        assert len(ua) > 50
        assert "Mozilla" in ua
    
    @pytest.mark.asyncio
    async def test_set_user_agent(self):
        """测试设置 UA"""
        session = MockSession()
        stealth = StealthMode(session)
        await stealth.set_user_agent("Custom UA")
        
        assert len(session.eval_js_calls) == 1
        assert "Custom UA" in session.eval_js_calls[0]


# =========================================================================
# RequestHeaderManager 测试
# =========================================================================

class TestRequestHeaderManager:
    """RequestHeaderManager 测试"""
    
    def test_get_headers_basic(self):
        """测试基础请求头"""
        manager = RequestHeaderManager()
        headers = manager.get_headers("https://example.com")
        
        assert "Accept" in headers
        assert "Accept-Language" in headers
        assert "Sec-Fetch-Dest" in headers
        assert "Sec-Fetch-Mode" in headers
        assert "Sec-Fetch-Site" in headers
        assert "Sec-Fetch-User" in headers
    
    def test_get_headers_with_site(self):
        """测试带站点配置"""
        manager = RequestHeaderManager()
        headers = manager.get_headers("https://www.bilibili.com/video/BV123")

        # B站配置 sec_fetch_site 为 same-origin
        assert headers.get("Sec-Fetch-Site") == "same-origin"
        assert "Referer" in headers
    
    def test_get_headers_with_custom_override(self):
        """测试自定义站点覆盖"""
        manager = RequestHeaderManager()
        manager.add_site_override("custom.com", {
            "X-Custom-Header": "custom-value"
        })
        
        headers = manager.get_headers("https://custom.com/page")
        assert headers.get("X-Custom-Header") == "custom-value"
    
    def test_update_config(self):
        """测试更新配置"""
        manager = RequestHeaderManager()
        manager.update_config(accept_language="en-US,en;q=0.9")
        
        headers = manager.get_headers()
        assert headers.get("Accept-Language") == "en-US,en;q=0.9"
    
    def test_clear(self):
        """测试清空缓存"""
        manager = RequestHeaderManager()
        manager.get_headers("https://example.com")
        manager.clear()
        
        assert len(manager._headers) == 0


# =========================================================================
# RateLimiter 测试
# =========================================================================

class TestRateLimiter:
    """RateLimiter 测试"""
    
    @pytest.mark.asyncio
    async def test_token_bucket_acquire(self):
        """测试令牌桶获取"""
        config = RateLimitConfig(
            algorithm=RateLimitAlgorithm.TOKEN_BUCKET,
            token_rate=10.0,
            max_tokens=10.0
        )
        limiter = RateLimiter(config)
        
        wait_time = await limiter.acquire()
        assert wait_time >= 0
    
    @pytest.mark.asyncio
    async def test_leaky_bucket_acquire(self):
        """测试漏桶获取"""
        config = RateLimitConfig(
            algorithm=RateLimitAlgorithm.LEAKY_BUCKET,
            bucket_capacity=10.0,
            leak_rate=1.0
        )
        limiter = RateLimiter(config)
        
        wait_time = await limiter.acquire()
        assert wait_time >= 0
    
    @pytest.mark.asyncio
    async def test_fixed_window_acquire(self):
        """测试固定窗口获取"""
        config = RateLimitConfig(
            algorithm=RateLimitAlgorithm.FIXED_WINDOW,
            window_size=1.0,
            max_requests=10
        )
        limiter = RateLimiter(config)
        
        wait_time = await limiter.acquire()
        assert wait_time >= 0
    
    @pytest.mark.asyncio
    async def test_execute_with_retry(self):
        """测试带重试的执行"""
        config = RateLimitConfig(max_retries=2, base_delay=0.01)
        limiter = RateLimiter(config)

        call_count = 0

        async def flaky_func():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise Exception("Temporary error")
            return "success"

        result = await limiter.execute(flaky_func)
        assert result == "success"
        assert call_count == 2
    
    @pytest.mark.asyncio
    async def test_circuit_breaker(self):
        """测试熔断器"""
        config = RateLimitConfig(
            failure_threshold=2,
            recovery_timeout=0.1
        )
        limiter = RateLimiter(config)
        
        # 触发熔断
        await limiter._circuit_breaker.record_failure()
        await limiter._circuit_breaker.record_failure()
        
        assert limiter.is_circuit_open
        
        # 等待恢复
        await asyncio.sleep(0.15)
        
        # 半开状态允许请求
        allowed = await limiter._circuit_breaker.check()
        assert allowed is True
    
    @pytest.mark.asyncio
    async def test_circuit_breaker_recovery(self):
        """测试熔断器恢复"""
        config = RateLimitConfig(failure_threshold=2, recovery_timeout=0.1)
        limiter = RateLimiter(config)

        await limiter._circuit_breaker.record_failure()
        await limiter._circuit_breaker.record_failure()

        # 等待恢复超时
        await asyncio.sleep(0.15)

        # 半开状态允许请求
        allowed = await limiter._circuit_breaker.check()
        assert allowed is True

        # 成功恢复
        await limiter._circuit_breaker.record_success()

        assert not limiter.is_circuit_open


# =========================================================================
# ProxyPool 测试
# =========================================================================

class TestProxyPool:
    """ProxyPool 测试"""
    
    def test_add_proxy(self):
        """测试添加代理"""
        pool = ProxyPool()
        proxy = ProxyInfo(host="127.0.0.1", port=8080)
        pool.add_proxy(proxy)
        
        assert pool.proxy_count == 1
        assert pool.active_count == 1
    
    def test_add_proxies(self):
        """测试批量添加代理"""
        pool = ProxyPool()
        proxies = [
            {"host": "127.0.0.1", "port": 8080},
            {"host": "127.0.0.2", "port": 8081, "type": "socks5"},
        ]
        pool.add_proxies(proxies)
        
        assert pool.proxy_count == 2
    
    def test_remove_proxy(self):
        """测试移除代理"""
        pool = ProxyPool()
        proxy = ProxyInfo(host="127.0.0.1", port=8080)
        pool.add_proxy(proxy)
        pool.remove_proxy(proxy.url)
        
        assert pool.proxy_count == 0
    
    def test_get_proxy_by_health_score(self):
        """测试按健康度选择代理"""
        pool = ProxyPool()
        proxy1 = ProxyInfo(host="127.0.0.1", port=8080)
        proxy2 = ProxyInfo(host="127.0.0.2", port=8081)
        proxy2.mark_failure()  # 降低健康度
        
        pool.add_proxy(proxy1)
        pool.add_proxy(proxy2)
        
        selected = pool.get_proxy_by_health_score()
        assert selected == proxy1
    
    def test_get_proxy_by_round_robin(self):
        """测试轮询选择代理"""
        pool = ProxyPool()
        proxy1 = ProxyInfo(host="127.0.0.1", port=8080)
        proxy2 = ProxyInfo(host="127.0.0.2", port=8081)
        pool.add_proxy(proxy1)
        pool.add_proxy(proxy2)
        
        selected1 = pool.get_proxy_by_round_robin()
        selected2 = pool.get_proxy_by_round_robin()
        
        assert selected1 != selected2
    
    def test_get_proxy_by_random(self):
        """测试随机选择代理"""
        pool = ProxyPool()
        proxy1 = ProxyInfo(host="127.0.0.1", port=8080)
        proxy2 = ProxyInfo(host="127.0.0.2", port=8081)
        pool.add_proxy(proxy1)
        pool.add_proxy(proxy2)
        
        for _ in range(10):
            selected = pool.get_proxy_by_random()
            assert selected in [proxy1, proxy2]
    
    def test_proxy_health_score(self):
        """测试代理健康度计算"""
        proxy = ProxyInfo(host="127.0.0.1", port=8080)
        proxy.mark_success()
        proxy.mark_success()
        proxy.mark_failure()
        
        assert proxy.health_score == pytest.approx(2/3, abs=0.01)
    
    def test_proxy_mark_failure_threshold(self):
        """测试代理失败阈值"""
        proxy = ProxyInfo(host="127.0.0.1", port=8080)
        proxy.mark_failure()
        proxy.mark_failure()
        proxy.mark_failure()
        
        assert proxy.is_active is False
    
    def test_get_stats(self):
        """测试统计信息"""
        pool = ProxyPool()
        pool.add_proxy(ProxyInfo(host="127.0.0.1", port=8080))
        pool.add_proxy(ProxyInfo(host="127.0.0.2", port=8081))
        
        stats = pool.get_stats()
        assert stats["total"] == 2
        assert stats["active"] == 2
        assert len(stats["proxies"]) == 2
    
    @pytest.mark.asyncio
    async def test_get_next_proxy(self):
        """测试获取下一个代理"""
        pool = ProxyPool(config=ProxyPoolConfig(rotation_strategy="health_score"))
        proxy1 = ProxyInfo(host="127.0.0.1", port=8080)
        proxy2 = ProxyInfo(host="127.0.0.2", port=8081)
        pool.add_proxy(proxy1)
        pool.add_proxy(proxy2)
        
        proxy = await pool.get_next_proxy()
        assert proxy is not None
        assert proxy in [proxy1, proxy2]


# =========================================================================
# 集成测试
# =========================================================================

class TestAntiCrawlIntegration:
    """反爬机制集成测试"""
    
    @pytest.mark.asyncio
    async def test_full_stealth_workflow(self):
        """测试完整 stealth 工作流"""
        session = MockSession()
        config = StealthConfig()
        stealth = StealthMode(session, config)
        
        # 应用 stealth
        await stealth.apply()
        
        # 模拟人类行为
        await stealth.human_like_click(100, 200)
        await stealth.human_like_type("test input")
        await stealth.random_delay(min_seconds=0.01, max_seconds=0.02)
        
        # 验证所有操作都执行了
        assert len(session.eval_js_calls) >= 7
        assert len(session.send_calls) >= 3
    
    @pytest.mark.asyncio
    async def test_header_and_rate_limit_workflow(self):
        """测试请求头和速率限制工作流"""
        header_manager = RequestHeaderManager()
        rate_limiter = RateLimiter(RateLimitConfig(max_retries=1))
        
        # 获取请求头
        headers = header_manager.get_headers("https://example.com")
        assert "Accept" in headers
        
        # 执行带速率限制的请求
        async def mock_request():
            return {"status": 200}
        
        result = await rate_limiter.execute(mock_request)
        assert result["status"] == 200


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
