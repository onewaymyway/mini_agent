"""
reliability/error.py 单元测试

测试覆盖：
- ErrorCategory 枚举
- ReliabilityError 基类
- 各类具体错误
- is_retryable 函数
- categorize_error 函数
- ERROR_RULES 规则表
"""
import pytest
import sys
from pathlib import Path

# 添加 skill 目录到路径
SKILL_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from src.reliability.error import (
    ErrorCategory,
    ReliabilityError,
    CDPConnectionLostError,
    CDPCommandTimeoutError,
    ElementNotFoundError,
    ElementIndexInvalidError,
    NavigationTimeoutError,
    CaptchaDetectedError,
    BlockedByAntiBotError,
    NetworkIdleTimeoutError,
    SmartWaitDegradedError,
    is_retryable,
    categorize_error,
    ERROR_RULES,
)


class TestErrorCategory:
    """ErrorCategory 枚举测试"""
    
    def test_all_categories_exist(self):
        """测试所有分类都存在"""
        assert hasattr(ErrorCategory, 'CONNECTION')
        assert hasattr(ErrorCategory, 'TIMEOUT')
        assert hasattr(ErrorCategory, 'ELEMENT')
        assert hasattr(ErrorCategory, 'NAVIGATION')
        assert hasattr(ErrorCategory, 'CONTENT')
        assert hasattr(ErrorCategory, 'PERMISSION')
        assert hasattr(ErrorCategory, 'UNKNOWN')
    
    def test_category_values(self):
        """测试分类值"""
        assert ErrorCategory.CONNECTION.value == "connection"
        assert ErrorCategory.TIMEOUT.value == "timeout"
        assert ErrorCategory.ELEMENT.value == "element"
        assert ErrorCategory.NAVIGATION.value == "navigation"
        assert ErrorCategory.CONTENT.value == "content"
        assert ErrorCategory.PERMISSION.value == "permission"
        assert ErrorCategory.UNKNOWN.value == "unknown"


class TestReliabilityError:
    """ReliabilityError 基类测试"""
    
    def test_init_default(self):
        """测试默认初始化"""
        error = ReliabilityError("test message", ErrorCategory.UNKNOWN)
        assert error.message == "test message"
        assert error.category == ErrorCategory.UNKNOWN
        assert error.recoverable is True
        assert error.details == {}
        assert error.timestamp > 0
    
    def test_init_with_details(self):
        """测试带 details 初始化"""
        details = {"key": "value", "count": 42}
        error = ReliabilityError("test", ErrorCategory.TIMEOUT, recoverable=False, details=details)
        assert error.recoverable is False
        assert error.details == details
    
    def test_to_dict(self):
        """测试序列化"""
        error = ReliabilityError("test", ErrorCategory.ELEMENT, recoverable=True, details={"selector": "#btn"})
        d = error.to_dict()
        assert d["type"] == "ReliabilityError"
        assert d["message"] == "test"
        assert d["category"] == "element"
        assert d["recoverable"] is True
        assert d["details"] == {"selector": "#btn"}
        assert "timestamp" in d
    
    def test_repr(self):
        """测试字符串表示"""
        error = ReliabilityError("test", ErrorCategory.CONNECTION)
        r = repr(error)
        assert "ReliabilityError" in r
        assert "connection" in r
        assert "test" in r


class TestSpecificErrors:
    """具体错误类测试"""
    
    def test_cdp_connection_lost(self):
        """测试 CDPConnectionLostError"""
        error = CDPConnectionLostError(details={"url": "https://example.com"})
        assert error.category == ErrorCategory.CONNECTION
        assert error.recoverable is True
        assert "CDP connection lost" in str(error)
    
    def test_cdp_command_timeout(self):
        """测试 CDPCommandTimeoutError"""
        error = CDPCommandTimeoutError("Runtime.evaluate", 30.0)
        assert error.category == ErrorCategory.TIMEOUT
        assert error.recoverable is True
        assert error.command == "Runtime.evaluate"
        assert error.timeout == 30.0
    
    def test_element_not_found(self):
        """测试 ElementNotFoundError"""
        error = ElementNotFoundError(selector="#btn", strategy="css")
        assert error.category == ErrorCategory.ELEMENT
        assert error.recoverable is True
        assert error.selector == "#btn"
        assert error.strategy == "css"
    
    def test_element_index_invalid(self):
        """测试 ElementIndexInvalidError"""
        error = ElementIndexInvalidError(index=5, available_count=3)
        assert error.category == ErrorCategory.ELEMENT
        assert error.recoverable is True
        assert error.index == 5
        assert error.available_count == 3
    
    def test_navigation_timeout(self):
        """测试 NavigationTimeoutError"""
        error = NavigationTimeoutError("https://example.com", 30.0)
        assert error.category == ErrorCategory.NAVIGATION
        assert error.recoverable is True
        assert error.url == "https://example.com"
        assert error.timeout == 30.0
    
    def test_captcha_detected(self):
        """测试 CaptchaDetectedError"""
        error = CaptchaDetectedError()
        assert error.category == ErrorCategory.CONTENT
        assert error.recoverable is False
    
    def test_blocked_by_anti_bot(self):
        """测试 BlockedByAntiBotError"""
        error = BlockedByAntiBotError()
        assert error.category == ErrorCategory.PERMISSION
        assert error.recoverable is False
    
    def test_network_idle_timeout(self):
        """测试 NetworkIdleTimeoutError"""
        error = NetworkIdleTimeoutError(timeout=15.0, pending_requests=3)
        assert error.category == ErrorCategory.TIMEOUT
        assert error.recoverable is True
        assert error.timeout == 15.0
        assert error.pending_requests == 3
    
    def test_smart_wait_degraded(self):
        """测试 SmartWaitDegradedError"""
        error = SmartWaitDegradedError(strategies_tried=["networkidle", "selector"], timeout=15.0)
        assert error.category == ErrorCategory.TIMEOUT
        assert error.recoverable is True
        assert error.strategies_tried == ["networkidle", "selector"]
        assert error.timeout == 15.0


class TestIsRetryable:
    """is_retryable 函数测试"""
    
    def test_reliability_error_recoverable(self):
        """测试可恢复的 ReliabilityError"""
        error = ReliabilityError("test", ErrorCategory.TIMEOUT, recoverable=True)
        assert is_retryable(error) is True
    
    def test_reliability_error_not_recoverable(self):
        """测试不可恢复的 ReliabilityError"""
        error = ReliabilityError("test", ErrorCategory.CONTENT, recoverable=False)
        assert is_retryable(error) is False
    
    def test_cdp_connection_lost(self):
        """测试 CDPConnectionLostError 可重试"""
        error = CDPConnectionLostError()
        assert is_retryable(error) is True
    
    def test_captcha_detected_not_retryable(self):
        """测试验证码错误不可重试"""
        error = CaptchaDetectedError()
        assert is_retryable(error) is False
    
    def test_blocked_by_anti_bot_not_retryable(self):
        """测试反爬拦截不可重试"""
        error = BlockedByAntiBotError()
        assert is_retryable(error) is False
    
    def test_generic_exception_not_retryable(self):
        """测试普通异常不可重试"""
        error = ValueError("test")
        assert is_retryable(error) is False
    
    def test_websocket_exception_retryable(self):
        """测试 websocket 相关异常可重试"""
        # is_retryable 检查异常类型名或消息中是否包含 "websocket"
        error = Exception("websocket connection lost")
        # 注意：is_retryable 检查 type(error).__name__ 是否包含 "CDP" 或 str(type(error)).lower() 是否包含 "websocket"
        # Exception 类型名是 "Exception"，不包含 "CDP" 或 "websocket"
        # 所以普通 Exception 不可重试，只有 ReliabilityError 子类或 CDP 相关异常才可重试
        assert is_retryable(error) is False

        # 测试真正的 websocket 相关异常
        class WebsocketError(Exception):
            pass
        ws_error = WebsocketError("websocket connection lost")
        # 类型名包含 "websocket"，所以可重试
        assert is_retryable(ws_error) is True

        # 测试包含 websocket 的异常类型名
        class websocket_error(Exception):
            pass
        ws_err = websocket_error("test")
        assert is_retryable(ws_err) is True  # str(type) 包含 "websocket"


class TestCategorizeError:
    """categorize_error 函数测试"""
    
    def test_cdp_connection_lost(self):
        """测试 CDPConnectionLostError 分类"""
        error = CDPConnectionLostError()
        assert categorize_error(error) == ErrorCategory.CONNECTION
    
    def test_cdp_command_timeout(self):
        """测试 CDPCommandTimeoutError 分类"""
        error = CDPCommandTimeoutError("test", 30.0)
        assert categorize_error(error) == ErrorCategory.TIMEOUT
    
    def test_network_idle_timeout(self):
        """测试 NetworkIdleTimeoutError 分类"""
        error = NetworkIdleTimeoutError(15.0)
        assert categorize_error(error) == ErrorCategory.TIMEOUT
    
    def test_smart_wait_degraded(self):
        """测试 SmartWaitDegradedError 分类"""
        error = SmartWaitDegradedError([], 15.0)
        assert categorize_error(error) == ErrorCategory.TIMEOUT
    
    def test_element_not_found(self):
        """测试 ElementNotFoundError 分类"""
        error = ElementNotFoundError(selector="#btn")
        assert categorize_error(error) == ErrorCategory.ELEMENT
    
    def test_element_index_invalid(self):
        """测试 ElementIndexInvalidError 分类"""
        error = ElementIndexInvalidError(5, 3)
        assert categorize_error(error) == ErrorCategory.ELEMENT
    
    def test_navigation_timeout(self):
        """测试 NavigationTimeoutError 分类"""
        error = NavigationTimeoutError("https://example.com", 30.0)
        assert categorize_error(error) == ErrorCategory.NAVIGATION
    
    def test_captcha_detected(self):
        """测试 CaptchaDetectedError 分类"""
        error = CaptchaDetectedError()
        assert categorize_error(error) == ErrorCategory.CONTENT
    
    def test_blocked_by_anti_bot(self):
        """测试 BlockedByAntiBotError 分类"""
        error = BlockedByAntiBotError()
        assert categorize_error(error) == ErrorCategory.PERMISSION
    
    def test_unknown_error(self):
        """测试未知错误分类"""
        error = ValueError("test")
        assert categorize_error(error) == ErrorCategory.UNKNOWN


class TestErrorRules:
    """ERROR_RULES 规则表测试"""
    
    def test_all_categories_have_rules(self):
        """测试所有分类都有规则"""
        # ERROR_RULES 的 key 是 ErrorCategory 枚举对象，不是字符串
        for category in ErrorCategory:
            assert category in ERROR_RULES
    
    def test_connection_rules(self):
        """测试 CONNECTION 规则"""
        rule = ERROR_RULES[ErrorCategory.CONNECTION]
        assert rule["recoverable"] is True
        assert "重建连接 + 重试" in rule["action"]
    
    def test_timeout_rules(self):
        """测试 TIMEOUT 规则"""
        rule = ERROR_RULES[ErrorCategory.TIMEOUT]
        assert rule["recoverable"] is True
        assert "重试" in rule["action"]
    
    def test_content_rules(self):
        """测试 CONTENT 规则"""
        rule = ERROR_RULES[ErrorCategory.CONTENT]
        assert rule["recoverable"] is False
        assert "停止" in rule["action"]
    
    def test_permission_rules(self):
        """测试 PERMISSION 规则"""
        rule = ERROR_RULES[ErrorCategory.PERMISSION]
        assert rule["recoverable"] is False
        assert "停止" in rule["action"]
    
    def test_unknown_rules(self):
        """测试 UNKNOWN 规则"""
        rule = ERROR_RULES[ErrorCategory.UNKNOWN]
        assert rule["recoverable"] == "视情况"
        assert "重试" in rule["action"]


if __name__ == "__main__":
    pytest.main([__file__, '-v'])
