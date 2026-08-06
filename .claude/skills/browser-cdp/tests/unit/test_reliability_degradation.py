"""
DegradationHandler 单元测试

测试覆盖：
- DegradationMode 枚举
- DegradationConfig 配置
- DegradationHandler 核心功能
- 四种降级模式
- 缓存机制
"""
import pytest
import sys
from pathlib import Path
from unittest.mock import Mock, patch

SKILL_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from src.reliability.degradation import (
    DegradationMode,
    DegradationConfig,
    DegradationHandler,
    get_degradation_handler,
    reset_degradation_handler,
    degrade_skip,
    degrade_error,
    degrade_fallback,
)
from src.reliability.error import (
    ReliabilityError,
    ErrorCategory,
    CaptchaDetectedError,
    BlockedByAntiBotError,
    ElementNotFoundError,
    CDPConnectionLostError,
)
from src.reliability.middleware import OperationType


class TestDegradationMode:
    """DegradationMode 枚举测试"""

    def test_all_modes_exist(self):
        assert hasattr(DegradationMode, 'SKIP')
        assert hasattr(DegradationMode, 'ERROR')
        assert hasattr(DegradationMode, 'FALLBACK')
        assert hasattr(DegradationMode, 'CACHED')

    def test_mode_values(self):
        assert DegradationMode.SKIP.value == "skip"
        assert DegradationMode.ERROR.value == "error"
        assert DegradationMode.FALLBACK.value == "fallback"
        assert DegradationMode.CACHED.value == "cached"


class TestDegradationConfig:
    """DegradationConfig 配置测试"""

    def test_default_config(self):
        config = DegradationConfig()
        assert config.mode == DegradationMode.ERROR
        assert config.default_value is None
        assert config.cache_key is None
        assert config.fallback_func is None
        assert ErrorCategory.CONTENT in config.skip_on_categories
        assert ErrorCategory.PERMISSION in config.skip_on_categories

    def test_custom_config(self):
        config = DegradationConfig(
            mode=DegradationMode.SKIP,
            default_value="fallback_data",
            cache_key="test_cache",
        )
        assert config.mode == DegradationMode.SKIP
        assert config.default_value == "fallback_data"
        assert config.cache_key == "test_cache"


class TestDegradationHandler:
    """DegradationHandler 核心功能测试"""

    def setup_method(self):
        reset_degradation_handler()

    def test_handle_unrecoverable_content_error(self):
        """测试不可恢复的 CONTENT 错误 - 应跳过"""
        handler = DegradationHandler()
        error = CaptchaDetectedError()

        result = handler.handle(error, "test_op", OperationType.NAVIGATION)
        assert result is None  # 默认跳过返回 None

    def test_handle_unrecoverable_permission_error(self):
        """测试不可恢复的 PERMISSION 错误 - 应跳过"""
        handler = DegradationHandler()
        error = BlockedByAntiBotError()

        result = handler.handle(error, "test_op", OperationType.NAVIGATION)
        assert result is None

    def test_handle_recoverable_error(self):
        """测试可恢复错误 - 应抛出错误"""
        handler = DegradationHandler()
        error = ElementNotFoundError(selector="#btn")

        with pytest.raises(ElementNotFoundError):
            handler.handle(error, "test_op", OperationType.CLICK)

    def test_handle_unknown_error(self):
        """测试未知错误 - 应抛出错误"""
        handler = DegradationHandler()
        error = ValueError("unexpected")

        with pytest.raises(ValueError):
            handler.handle(error, "test_op", OperationType.UNKNOWN)

    def test_skip_mode_with_default_value(self):
        """测试 SKIP 模式返回默认值"""
        handler = DegradationHandler(DegradationConfig(
            mode=DegradationMode.SKIP,
            default_value="cached_result",
        ))
        error = CaptchaDetectedError()
        result = handler.handle(error, "test_op", OperationType.NAVIGATION)
        assert result == "cached_result"

    def test_fallback_mode(self):
        """测试 FALLBACK 模式"""
        def fallback():
            return "fallback_data"

        handler = DegradationHandler(DegradationConfig(
            mode=DegradationMode.FALLBACK,
            fallback_func=fallback,
        ))
        error = ElementNotFoundError(selector="#data")
        result = handler.handle(error, "test_op", OperationType.EXTRACT)
        assert result == "fallback_data"

    def test_fallback_mode_fallback_fails(self):
        """测试 FALLBACK 模式备用策略也失败"""
        def fallback():
            raise RuntimeError("fallback failed")

        handler = DegradationHandler(DegradationConfig(
            mode=DegradationMode.FALLBACK,
            fallback_func=fallback,
        ))
        original_error = ElementNotFoundError(selector="#data")

        with pytest.raises(ElementNotFoundError):
            handler.handle(original_error, "test_op", OperationType.EXTRACT)

    def test_cached_mode_hit(self):
        """测试 CACHED 模式缓存命中"""
        handler = DegradationHandler(DegradationConfig(
            mode=DegradationMode.CACHED,
            cache_key="test_key",
            default_value="default",
        ))
        handler.set_cache("test_key", "cached_value")

        error = ElementNotFoundError(selector="#data")
        result = handler.handle(error, "test_op", OperationType.EXTRACT)
        assert result == "cached_value"

    def test_cached_mode_miss(self):
        """测试 CACHED 模式缓存未命中"""
        handler = DegradationHandler(DegradationConfig(
            mode=DegradationMode.CACHED,
            cache_key="missing_key",
            default_value="default",
        ))

        error = ElementNotFoundError(selector="#data")
        result = handler.handle(error, "test_op", OperationType.EXTRACT)
        assert result == "default"

    def test_clear_cache(self):
        """测试清空缓存"""
        handler = DegradationHandler(DegradationConfig(
            mode=DegradationMode.CACHED,
            cache_key="test_key",
        ))
        handler.set_cache("test_key", "value")
        handler.clear_cache()

        error = ElementNotFoundError(selector="#data")
        result = handler.handle(error, "test_op", OperationType.EXTRACT)
        assert result is None

    def test_connection_error_is_retryable(self):
        """测试连接错误可恢复 - 应抛出而非跳过"""
        handler = DegradationHandler()
        error = CDPConnectionLostError()

        # 连接错误是可恢复的，不应跳过
        with pytest.raises(CDPConnectionLostError):
            handler.handle(error, "test_op", OperationType.NAVIGATION)


class TestDegradationFunctions:
    """便捷函数测试"""

    def test_degrade_skip(self):
        """测试 degrade_skip 函数"""
        error = CaptchaDetectedError()
        result = degrade_skip("test_op", error, default="fallback")
        assert result == "fallback"

    def test_degrade_error(self):
        """测试 degrade_error 函数"""
        error = ElementNotFoundError(selector="#btn")

        with pytest.raises(ElementNotFoundError):
            degrade_error("test_op", error)

    def test_degrade_fallback(self):
        """测试 degrade_fallback 函数"""
        def fallback():
            return "fallback_result"

        error = ElementNotFoundError(selector="#data")
        result = degrade_fallback("test_op", error, fallback)
        assert result == "fallback_result"


class TestDegradationIntegration:
    """集成测试"""

    def test_handler_singleton(self):
        """测试降级处理器单例"""
        h1 = get_degradation_handler()
        h2 = get_degradation_handler()
        assert h1 is h2

    def test_reset_handler(self):
        """测试重置降级处理器"""
        h1 = get_degradation_handler()
        reset_degradation_handler()
        h2 = get_degradation_handler()
        assert h1 is not h2


if __name__ == "__main__":
    pytest.main([__file__, '-v'])
