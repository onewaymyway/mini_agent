"""
reliability/searcher_utils.py 单元测试

测试覆盖：
- SearcherConfig 配置类
- run_cmd_with_retry 异步重试包装
- run_cmd_with_retry_sync 同步重试包装
- ElementLocator 元素定位器
- SearcherErrorProcessor 错误处理器
- SearcherMixin 搜索器 Mixin
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

from src.reliability.searcher_utils import (
    SEARCHER_DEFAULTS,
    SearcherConfig,
    SearcherMixin,
    ElementLocator,
    SearcherErrorProcessor,
    run_cmd_with_retry,
    run_cmd_with_retry_sync,
)
from src.reliability.error import (
    ElementNotFoundError,
    ElementIndexInvalidError,
    CDPConnectionLostError,
    ErrorCategory,
)
from src.reliability.retry import BackoffStrategy


class TestSearcherDefaults:
    """SEARCHER_DEFAULTS 测试"""
    
    def test_defaults_exist(self):
        """测试默认配置存在"""
        assert "max_retries" in SEARCHER_DEFAULTS
        assert "navigation_timeout" in SEARCHER_DEFAULTS
        assert "element_timeout" in SEARCHER_DEFAULTS
        assert "enable_stealth" in SEARCHER_DEFAULTS
        assert "smart_wait" in SEARCHER_DEFAULTS
        assert "circuit_breaker" in SEARCHER_DEFAULTS
    
    def test_defaults_values(self):
        """测试默认配置值"""
        assert SEARCHER_DEFAULTS["max_retries"] == 3
        assert SEARCHER_DEFAULTS["navigation_timeout"] == 30.0
        assert SEARCHER_DEFAULTS["element_timeout"] == 10.0
        assert SEARCHER_DEFAULTS["enable_stealth"] is True
        assert SEARCHER_DEFAULTS["smart_wait"] is True
        assert SEARCHER_DEFAULTS["circuit_breaker"] is True


class TestSearcherConfig:
    """SearcherConfig 配置类测试"""
    
    def test_default_config(self):
        """测试默认配置"""
        config = SearcherConfig()
        assert config.max_retries == 3
        assert config.navigation_timeout == 30.0
        assert config.element_timeout == 10.0
        assert config.enable_stealth is True
        assert config.smart_wait is True
    
    def test_custom_config(self):
        """测试自定义配置"""
        config = SearcherConfig(max_retries=5, navigation_timeout=60.0)
        assert config.max_retries == 5
        assert config.navigation_timeout == 60.0
        assert config.element_timeout == 10.0  # 保持默认
    
    def test_to_retry_config(self):
        """测试转换为 RetryConfig"""
        config = SearcherConfig(max_retries=5, circuit_breaker=True)
        retry_config = config.to_retry_config("test_op")
        assert retry_config.max_retries == 5
        assert retry_config.circuit_breaker is True


class MockCDPClient:
    """模拟 CDP 客户端"""
    
    def __init__(self, fail_count=0):
        self._fail_count = fail_count
        self._call_count = 0
        self._send_calls = []
    
    async def send(self, method: str, params: dict = None):
        """模拟异步 CDP 命令"""
        self._call_count += 1
        self._send_calls.append((method, params))
        if self._call_count <= self._fail_count:
            raise CDPConnectionLostError()
        return {"result": {"value": "success"}}
    
    def send_sync(self, method: str, params: dict = None):
        """模拟同步 CDP 命令"""
        self._call_count += 1
        self._send_calls.append((method, params))
        if self._call_count <= self._fail_count:
            raise CDPConnectionLostError()
        return {"result": {"value": "success"}}


class TestRunCmdWithRetry:
    """run_cmd_with_retry 异步重试包装测试"""
    
    @pytest.mark.asyncio
    async def test_success_on_first_try(self):
        """测试第一次尝试成功"""
        client = MockCDPClient(fail_count=0)
        result = await run_cmd_with_retry(
            client, "Runtime.evaluate", {"expression": "1+1"}, operation="test"
        )
        assert result["result"]["value"] == "success"
        assert client._call_count == 1
    
    @pytest.mark.asyncio
    async def test_retry_on_failure(self):
        """测试失败后重试"""
        client = MockCDPClient(fail_count=1)
        result = await run_cmd_with_retry(
            client, "Runtime.evaluate", {"expression": "1+1"}, operation="test"
        )
        assert result["result"]["value"] == "success"
        assert client._call_count == 2
    
    @pytest.mark.asyncio
    async def test_exhaust_retries(self):
        """测试重试耗尽"""
        client = MockCDPClient(fail_count=10)
        with pytest.raises(CDPConnectionLostError):
            await run_cmd_with_retry(
                client, "Runtime.evaluate", {"expression": "1+1"},
                operation="test",
                config=type('Config', (), {'max_retries': 2, 'base_delay': 0.01, 'max_delay': 1.0, 'circuit_breaker': False, 'retryable_exceptions': (Exception,), 'backoff_strategy': None, 'on_retry': None, 'on_exhausted': None})()
            )


class TestRunCmdWithRetrySync:
    """run_cmd_with_retry_sync 同步重试包装测试"""
    
    def test_success_on_first_try(self):
        """测试同步第一次成功"""
        client = MockCDPClient(fail_count=0)
        result = run_cmd_with_retry_sync(
            client, "Runtime.evaluate", {"expression": "1+1"}, operation="test"
        )
        assert result["result"]["value"] == "success"
    
    def test_retry_on_failure(self):
        """测试同步失败后重试"""
        client = MockCDPClient(fail_count=1)
        result = run_cmd_with_retry_sync(
            client, "Runtime.evaluate", {"expression": "1+1"}, operation="test"
        )
        assert result["result"]["value"] == "success"


class TestElementLocator:
    """ElementLocator 元素定位器测试"""
    
    def setup_method(self):
        """每个测试前初始化"""
        self.client = MockCDPClient()
        self.locator = ElementLocator(self.client)
    
    def test_find_by_selector(self):
        """测试通过选择器查找元素"""
        # 模拟元素存在
        with patch.object(self.locator, '_find_by_selector') as mock_find:
            mock_find.return_value = {"nodeId": 123}
            result = self.locator.find_element(selector="#btn")
            assert result is not None
    
    def test_find_by_index(self):
        """测试通过编号查找元素"""
        # 模拟元素存在
        with patch.object(self.locator, '_find_by_index') as mock_find:
            mock_find.return_value = {"nodeId": 456}
            result = self.locator.find_element(index=0)
            assert result is not None
    
    def test_find_element_no_match(self):
        """测试找不到元素"""
        with patch.object(self.locator, '_find_by_selector', return_value=None):
            with patch.object(self.locator, '_find_by_index', return_value=None):
                result = self.locator.find_element(selector="#missing", index=0)
                assert result is None
    
    def test_invalidate_cache(self):
        """测试清除缓存"""
        self.locator._element_cache["key"] = "value"
        self.locator.invalidate_cache()
        assert len(self.locator._element_cache) == 0
    
    @pytest.mark.asyncio
    async def test_find_element_async(self):
        """测试异步查找元素"""
        with patch.object(self.locator, '_find_by_selector_async') as mock_find:
            mock_find.return_value = {"nodeId": 789}
            result = await self.locator.find_element_async(selector="#btn")
            assert result is not None


class TestSearcherErrorProcessor:
    """SearcherErrorProcessor 错误处理器测试"""
    
    def setup_method(self):
        """每个测试前初始化"""
        self.processor = SearcherErrorProcessor("TestSearcher")
    
    def test_process_connection_error(self):
        """测试处理连接错误"""
        error = CDPConnectionLostError()
        result = self.processor.process_error(error)
        assert result["category"] == "connection"
        assert result["recoverable"] is True
        assert result["action"] == "reconnect_and_retry"
    
    def test_process_timeout_error(self):
        """测试处理超时错误"""
        error = ElementNotFoundError(selector="#btn")
        result = self.processor.process_error(error)
        assert result["category"] == "element"
        assert result["recoverable"] is True
        assert result["action"] == "rescan_or_wait"
    
    def test_process_captcha_error(self):
        """测试处理验证码错误"""
        from src.reliability.error import CaptchaDetectedError
        error = CaptchaDetectedError()
        result = self.processor.process_error(error)
        assert result["category"] == "content"
        assert result["recoverable"] is False
        assert result["action"] == "stop_and_notify"
    
    def test_get_error_summary(self):
        """测试错误统计"""
        self.processor.process_error(CDPConnectionLostError())
        self.processor.process_error(ElementNotFoundError(selector="#btn"))
        
        summary = self.processor.get_error_summary()
        assert summary["total_errors"] == 2
        assert summary["by_category"]["connection"] == 1
        assert summary["by_category"]["element"] == 1
        assert summary["recoverable_count"] == 2
    
    def test_clear_log(self):
        """测试清空错误日志"""
        self.processor.process_error(CDPConnectionLostError())
        self.processor.clear_log()
        assert len(self.processor._error_log) == 0


class TestSearcherMixin:
    """SearcherMixin 搜索器 Mixin 测试"""
    
    def test_mixin_initialization(self):
        """测试 Mixin 初始化"""
        class TestSearcher(SearcherMixin):
            def __init__(self):
                super().__init__()
        
        searcher = TestSearcher()
        assert searcher.config is not None
        assert searcher.error_processor is not None
    
    def test_get_element_locator(self):
        """测试获取元素定位器"""
        class TestSearcher(SearcherMixin):
            def __init__(self):
                super().__init__()
        
        searcher = TestSearcher()
        locator = searcher.get_element_locator(MockCDPClient())
        assert isinstance(locator, ElementLocator)
    
    def test_process_error(self):
        """测试错误处理"""
        class TestSearcher(SearcherMixin):
            def __init__(self):
                super().__init__()
        
        searcher = TestSearcher()
        error = CDPConnectionLostError()
        result = searcher.process_error(error)
        assert result["category"] == "connection"
    
    def test_should_retry(self):
        """测试重试判断"""
        class TestSearcher(SearcherMixin):
            def __init__(self):
                super().__init__()
        
        searcher = TestSearcher()
        assert searcher.should_retry(CDPConnectionLostError()) is True
        assert searcher.should_retry(ElementNotFoundError()) is True
    
    def test_get_error_summary(self):
        """测试错误统计"""
        class TestSearcher(SearcherMixin):
            def __init__(self):
                super().__init__()
        
        searcher = TestSearcher()
        searcher.process_error(CDPConnectionLostError())
        summary = searcher.get_error_summary()
        assert summary["total_errors"] == 1


if __name__ == "__main__":
    pytest.main([__file__, '-v'])
