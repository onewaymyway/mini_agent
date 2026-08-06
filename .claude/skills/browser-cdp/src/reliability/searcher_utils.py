"""
搜索器标准化工具

提供统一的搜索器重试包装、配置管理、错误处理等能力。
所有搜索器应使用本模块提供的工具函数，而非各自独立实现重试逻辑。
"""

import logging
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from .error import (
    ElementNotFoundError,
    ElementIndexInvalidError,
    CDPConnectionLostError,
    is_retryable,
    categorize_error,
)
from .retry import (
    RetryConfig,
    CircuitBreaker,
    retry_operation,
    retry_operation_async,
    BackoffStrategy,
)

logger = logging.getLogger(__name__)

# 搜索器默认配置
SEARCHER_DEFAULTS = {
    "max_retries": 3,
    "retry_backoff": BackoffStrategy.EXPONENTIAL_JITTER,
    "navigation_timeout": 30.0,
    "element_timeout": 10.0,
    "enable_stealth": True,  # 默认启用反检测
    "smart_wait": True,      # 默认启用智能等待
    "circuit_breaker": True,
    "circuit_breaker_threshold": 5,
    "circuit_breaker_recovery": 30.0,
}


class SearcherConfig:
    """搜索器配置类"""

    def __init__(self, **overrides):
        self.config = {**SEARCHER_DEFAULTS, **overrides}

    @property
    def max_retries(self) -> int:
        return self.config.get("max_retries", 3)

    @property
    def navigation_timeout(self) -> float:
        return self.config.get("navigation_timeout", 30.0)

    @property
    def element_timeout(self) -> float:
        return self.config.get("element_timeout", 10.0)

    @property
    def enable_stealth(self) -> bool:
        return self.config.get("enable_stealth", True)

    @property
    def smart_wait(self) -> bool:
        return self.config.get("smart_wait", True)

    def to_retry_config(self, operation: str = "searcher") -> RetryConfig:
        """转换为 RetryConfig"""
        return RetryConfig(
            max_retries=self.max_retries,
            backoff_strategy=self.config.get("retry_backoff", BackoffStrategy.EXPONENTIAL_JITTER),
            circuit_breaker=self.config.get("circuit_breaker", True),
            circuit_breaker_threshold=self.config.get("circuit_breaker_threshold", 5),
            circuit_breaker_recovery=self.config.get("circuit_breaker_recovery", 30.0),
        )


def run_cmd_with_retry(
    cdp_client: Any,
    cmd_name: str,
    params: Dict[str, Any],
    config: Optional[RetryConfig] = None,
    operation: str = "searcher",
    **kwargs,
) -> Any:
    """
    搜索器统一重试包装：自动处理 CDP 命令重试。

    Args:
        cdp_client: CDP 客户端实例
        cmd_name: CDP 命令名称
        params: 命令参数
        config: 重试配置（可选，默认使用搜索器默认配置）
        operation: 操作类型名称
        **kwargs: 额外参数

    Returns:
        CDP 命令执行结果

    Raises:
        最后一次异常（重试耗尽后）
    """
    if config is None:
        config = RetryConfig.for_operation(operation)

    async def _execute():
        return await cdp_client.send(cmd_name, params)

    return retry_operation_async(
        _execute,
        config=config,
        operation=operation,
        **kwargs,
    )


def run_cmd_with_retry_sync(
    cdp_client: Any,
    cmd_name: str,
    params: Dict[str, Any],
    config: Optional[RetryConfig] = None,
    operation: str = "searcher",
    **kwargs,
) -> Any:
    """
    同步版搜索器重试包装。
    """
    if config is None:
        config = RetryConfig.for_operation(operation)

    def _execute():
        return cdp_client.send_sync(cmd_name, params) if hasattr(cdp_client, 'send_sync') else cdp_client.send(cmd_name, params)

    return retry_operation(
        _execute,
        config=config,
        operation=operation,
        **kwargs,
    )


class ElementLocator:
    """
    元素定位器：提供多策略元素查找和编号失效容错。
    """

    def __init__(self, cdp_client: Any, config: Optional[SearcherConfig] = None):
        self.cdp_client = cdp_client
        self.config = config or SearcherConfig()
        self._element_cache: Dict[str, List[Dict]] = {}
        self._cache_ttl = 5.0  # 缓存有效期 5 秒
        self._last_cache_time = 0.0

    def find_element(
        self,
        selector: Optional[str] = None,
        index: Optional[int] = None,
        timeout: Optional[float] = None,
    ) -> Optional[Dict]:
        """
        多策略元素查找：优先 selector，回退到 index。

        Args:
            selector: CSS 选择器
            index: 元素编号
            timeout: 超时时间

        Returns:
            元素信息字典或 None
        """
        strategies = []
        if selector:
            strategies.append(self._find_by_selector)
        if index is not None:
            strategies.append(self._find_by_index)

        timeout = timeout or self.config.element_timeout

        for strategy in strategies:
            try:
                result = retry_operation(
                    strategy,
                    config=self.config.to_retry_config("element_find"),
                    selector=selector,
                    index=index,
                    timeout=timeout,
                )
                if result:
                    return result
            except Exception as e:
                logger.debug(f"Strategy {strategy.__name__} failed: {e}")
                continue

        return None

    async def find_element_async(
        self,
        selector: Optional[str] = None,
        index: Optional[int] = None,
        timeout: Optional[float] = None,
    ) -> Optional[Dict]:
        """异步版多策略元素查找"""
        strategies = []
        if selector:
            strategies.append(self._find_by_selector_async)
        if index is not None:
            strategies.append(self._find_by_index_async)

        timeout = timeout or self.config.element_timeout

        for strategy in strategies:
            try:
                result = await retry_operation_async(
                    strategy,
                    config=self.config.to_retry_config("element_find"),
                    selector=selector,
                    index=index,
                    timeout=timeout,
                )
                if result:
                    return result
            except Exception as e:
                logger.debug(f"Strategy {strategy.__name__} failed: {e}")
                continue

        return None

    def _find_by_selector(self, selector: str, timeout: float, **kwargs) -> Optional[Dict]:
        """通过选择器查找元素"""
        # 实现依赖具体 CDP 客户端
        raise NotImplementedError

    async def _find_by_selector_async(self, selector: str, timeout: float, **kwargs) -> Optional[Dict]:
        """异步通过选择器查找元素"""
        raise NotImplementedError

    def _find_by_index(self, index: int, timeout: float, **kwargs) -> Optional[Dict]:
        """通过编号查找元素（含自动重扫容错）"""
        max_retries = 3
        for attempt in range(max_retries):
            try:
                return self._find_by_index_direct(index)
            except ElementIndexInvalidError:
                if attempt < max_retries - 1:
                    logger.warning(f"Element index {index} invalid, retrying ({attempt+1}/{max_retries})")
                    time.sleep(0.5)
                    continue
                raise
        return None

    async def _find_by_index_async(self, index: int, timeout: float, **kwargs) -> Optional[Dict]:
        """异步通过编号查找元素（含自动重扫容错）"""
        max_retries = 3
        for attempt in range(max_retries):
            try:
                return await self._find_by_index_direct_async(index)
            except ElementIndexInvalidError:
                if attempt < max_retries - 1:
                    logger.warning(f"Element index {index} invalid, retrying ({attempt+1}/{max_retries})")
                    await asyncio.sleep(0.5)
                    continue
                raise
        return None

    def _find_by_index_direct(self, index: int) -> Dict:
        """直接通过编号查找元素"""
        raise NotImplementedError

    async def _find_by_index_direct_async(self, index: int) -> Dict:
        """异步直接通过编号查找元素"""
        raise NotImplementedError

    def invalidate_cache(self):
        """使元素缓存失效"""
        self._element_cache.clear()
        self._last_cache_time = 0.0


class SearcherErrorProcessor:
    """
    搜索器错误处理器：统一错误分类、日志记录和恢复策略。
    """

    def __init__(self, searcher_name: str):
        self.searcher_name = searcher_name
        self._error_log: List[Dict[str, Any]] = []

    def process_error(self, error: Exception, context: Optional[Dict] = None) -> Dict[str, Any]:
        """
        处理搜索器错误。

        Returns:
            dict: 错误处理结果
                - category: ErrorCategory
                - recoverable: bool
                - action: str
                - error: Exception
        """
        category = categorize_error(error)
        recoverable = is_retryable(error)

        action = self._get_recommended_action(category, recoverable)

        entry = {
            "searcher": self.searcher_name,
            "timestamp": time.time(),
            "category": category.value,
            "recoverable": recoverable,
            "action": action,
            "error": str(error),
            "error_type": type(error).__name__,
            "context": context or {},
        }
        self._error_log.append(entry)

        logger.error(f"[{self.searcher_name}] Error: {error} (category={category.value}, recoverable={recoverable})")

        return entry

    def _get_recommended_action(self, category: Any, recoverable: bool) -> str:
        """获取推荐处理动作"""
        from .error import ErrorCategory
        if not recoverable:
            return "stop_and_notify"
        if category == ErrorCategory.CONNECTION:
            return "reconnect_and_retry"
        if category == ErrorCategory.TIMEOUT:
            return "retry_or_degrade"
        if category == ErrorCategory.ELEMENT:
            return "rescan_or_wait"
        if category == ErrorCategory.NAVIGATION:
            return "retry_navigation"
        return "log_and_retry_once"

    def get_error_summary(self) -> Dict[str, Any]:
        """获取错误统计摘要"""
        summary = {
            "searcher": self.searcher_name,
            "total_errors": len(self._error_log),
            "by_category": {},
            "recoverable_count": 0,
            "non_recoverable_count": 0,
        }
        for entry in self._error_log:
            cat = entry["category"]
            summary["by_category"][cat] = summary["by_category"].get(cat, 0) + 1
            if entry["recoverable"]:
                summary["recoverable_count"] += 1
            else:
                summary["non_recoverable_count"] += 1
        return summary

    def clear_log(self):
        """清空错误日志"""
        self._error_log.clear()


# 搜索器基类 mixin
class SearcherMixin:
    """
    搜索器 Mixin：提供重试、错误处理、元素定位等通用能力。
    所有搜索器应继承此 Mixin 以获得标准化能力。
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._config = SearcherConfig()
        self._error_processor = SearcherErrorProcessor(self.__class__.__name__)
        self._element_locator: Optional[ElementLocator] = None

    @property
    def config(self) -> SearcherConfig:
        return self._config

    @property
    def error_processor(self) -> SearcherErrorProcessor:
        return self._error_processor

    def get_element_locator(self, cdp_client: Any) -> ElementLocator:
        """获取元素定位器"""
        if self._element_locator is None:
            self._element_locator = ElementLocator(cdp_client, self._config)
        return self._element_locator

    def process_error(self, error: Exception, context: Optional[Dict] = None) -> Dict[str, Any]:
        """处理错误"""
        return self._error_processor.process_error(error, context)

    def should_retry(self, error: Exception) -> bool:
        """判断是否应该重试"""
        return is_retryable(error)

    def get_error_summary(self) -> Dict[str, Any]:
        """获取错误统计"""
        return self._error_processor.get_error_summary()
