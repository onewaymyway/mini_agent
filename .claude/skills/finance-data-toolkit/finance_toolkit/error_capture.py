# -*- coding: utf-8 -*-
"""
数据采集错误捕获模块

提供统一的数据抓取错误捕获、分类、日志记录和质量指标追踪。

设计目标：
1. 所有 fetcher 的错误统一在此模块捕获和分类
2. 结构化日志记录，便于问题追踪和分析
3. 实时质量指标统计，支持告警决策
4. 与 CircuitBreaker / HealthMonitor 联动

使用示例：
    from finance_toolkit.error_capture import ErrorCapture, ErrorType

    capture = ErrorCapture(source="akshare")
    try:
        data = fetch_data()
        capture.record_success()
    except Exception as e:
        error_type = capture.classify_error(e)
        capture.record_failure(error_type, e)
        raise  # 或降级到备用源
"""

import logging
import traceback
import time
import threading
import functools
from typing import Optional, Dict, Any, List, Tuple, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from time import time as now_time
from enum import Enum
from collections import deque, defaultdict
import re

logger = logging.getLogger(__name__)

# ============== 错误类型枚举 ==============

class ErrorType(Enum):
    """数据抓取错误类型"""
    # 网络层
    NETWORK_TIMEOUT = "network_timeout"
    NETWORK_CONNECTION = "network_connection"
    NETWORK_DNS = "network_dns"
    NETWORK_SSL = "network_ssl"

    # HTTP 层
    HTTP_4XX = "http_4xx"           # 客户端错误（401/403/404/429）
    HTTP_5XX = "http_5xx"           # 服务端错误（500/502/503）
    HTTP_EMPTY = "http_empty"        # 返回空内容
    HTTP_INVALID_JSON = "http_invalid_json"

    # 解析层
    PARSE_JSON = "parse_json"
    PARSE_HTML = "parse_html"
    PARSE_ENCODING = "parse_encoding"
    PARSE_FIELD_MISSING = "parse_field_missing"
    PARSE_FIELD_TYPE = "parse_field_type"

    # 业务逻辑
    BUSINESS_EMPTY = "business_empty"      # 返回空数据（非异常，如停牌）
    BUSINESS_INVALID = "business_invalid"  # 业务规则不满足
    DATA_QUALITY = "data_quality"          # 质量验证失败

    # 依赖层
    DEPENDENCY_MISSING = "dependency_missing"  # 依赖库缺失
    DEPENDENCY_VERSION = "dependency_version"    # 依赖版本不兼容

    # 系统层
    SYSTEM_INTERRUPT = "system_interrupt"   # 被中断
    SYSTEM_MEMORY = "system_memory"         # 内存不足
    UNKNOWN = "unknown"                     # 未知错误


# ============== 错误分类规则 ==============

_ERROR_CLASSIFICATION_RULES: List[Tuple[re.Pattern, ErrorType]] = [
    # 网络超时
    (re.compile(r'timeout|TimeoutException|TimeoutError', re.I), ErrorType.NETWORK_TIMEOUT),
    # 连接失败
    (re.compile(r'ConnectionError|connect.*fail|connection.*reset', re.I), ErrorType.NETWORK_CONNECTION),
    # DNS解析失败
    (re.compile(r'DNS|resolver|getaddrinfo', re.I), ErrorType.NETWORK_DNS),
    # SSL证书
    (re.compile(r'ssl|SSL|certificate|CERTIFICATE', re.I), ErrorType.NETWORK_SSL),
    # HTTP 4xx
    (re.compile(r'401|403|404|429|Unauthorized|Forbidden|Not Found|Too Many Requests', re.I), ErrorType.HTTP_4XX),
    # HTTP 5xx
    (re.compile(r'500|502|503|504|Bad Gateway|Service Unavailable', re.I), ErrorType.HTTP_5XX),
    # JSON解析
    (re.compile(r'JSONDecodeError|json.*decode|Expecting.*value', re.I), ErrorType.PARSE_JSON),
    # 编码问题
    (re.compile(r'UnicodeDecode|encoding|decode.*error', re.I), ErrorType.PARSE_ENCODING),
    # 索引/字段错误
    (re.compile(r'IndexError|KeyError|Field.*not found|missing.*field', re.I), ErrorType.PARSE_FIELD_MISSING),
    # 类型转换
    (re.compile(r'ValueError|TypeError|cannot.*convert', re.I), ErrorType.PARSE_FIELD_TYPE),
    # 依赖缺失
    (re.compile(r'ImportError|ModuleNotFoundError|no module named', re.I), ErrorType.DEPENDENCY_MISSING),
    # 中断
    (re.compile(r'KeyboardInterrupt|SystemExit', re.I), ErrorType.SYSTEM_INTERRUPT),
]


# ============== 数据结构 ==============

@dataclass
class ErrorRecord:
    """单次错误记录"""
    error_type: ErrorType
    source: str
    data_type: str
    symbol: Optional[str]
    message: str
    timestamp: datetime
    traceback_str: str = ""
    attempt_count: int = 0
    duration_ms: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            'error_type': self.error_type.value,
            'source': self.source,
            'data_type': self.data_type,
            'symbol': self.symbol,
            'message': self.message,
            'timestamp': self.timestamp.isoformat(),
            'traceback': self.traceback_str[:500] if self.traceback_str else '',
            'attempt_count': self.attempt_count,
            'duration_ms': round(self.duration_ms, 1),
            'metadata': self.metadata,
        }


@dataclass
class ErrorStats:
    """错误统计（滑动窗口）"""
    source: str
    window_seconds: int = 300  # 5分钟窗口

    _errors: deque = field(default_factory=deque)
    _type_counts: Dict[ErrorType, int] = field(default_factory=lambda: defaultdict(int))
    _total_requests: int = 0
    _success_count: int = 0
    _failure_count: int = 0

    def add_error(self, record: ErrorRecord):
        """添加错误记录"""
        self._errors.append(record)
        self._type_counts[record.error_type] += 1
        self._failure_count += 1
        self._prune_old_records()

    def add_success(self):
        """记录成功"""
        self._success_count += 1
        self._total_requests += 1
        self._prune_old_records()

    def _prune_old_records(self):
        """清理过期记录"""
        cutoff = time.time() - self.window_seconds
        while self._errors and self._errors[0].timestamp.timestamp() < cutoff:
            old = self._errors.popleft()
            self._type_counts[old.error_type] -= 1
            if self._type_counts[old.error_type] <= 0:
                del self._type_counts[old.error_type]
            self._failure_count = max(0, self._failure_count - 1)

    @property
    def success_rate(self) -> float:
        total = self._success_count + self._failure_count
        return (self._success_count / total * 100) if total > 0 else 100.0

    @property
    def recent_error_count(self) -> int:
        return len(self._errors)

    @property
    def error_rates_by_type(self) -> Dict[str, int]:
        return dict(self._type_counts)

    def to_dict(self) -> Dict[str, Any]:
        return {
            'source': self.source,
            'window_seconds': self.window_seconds,
            'total_requests': self._total_requests,
            'success_count': self._success_count,
            'failure_count': self._failure_count,
            'success_rate': round(self.success_rate, 2),
            'recent_error_count': self.recent_error_count,
            'error_rates_by_type': {k.value: v for k, v in self._type_counts.items()},
            'last_error': self._errors[-1].to_dict() if self._errors else None,
        }


# ============== 错误捕获器核心类 ==============

class ErrorCapture:
    """
    数据采集错误捕获器

    职责：
    1. 统一捕获所有数据抓取异常
    2. 自动分类错误类型
    3. 结构化日志记录
    4. 维护错误统计（滑动窗口）
    5. 提供告警决策接口

    线程安全：是（内部使用锁）
    """

    def __init__(
        self,
        source: str,
        data_type: str = "unknown",
        symbol: Optional[str] = None,
        max_retry: int = 3,
        log_level: int = logging.WARNING,
    ):
        self.source = source
        self.data_type = data_type
        self.symbol = symbol
        self.max_retry = max_retry
        self.log_level = log_level

        self._stats = ErrorStats(source=source)
        self._error_history: deque = deque(maxlen=100)  # 保留最近100条
        self._lock = threading.Lock() if 'threading' in dir() else None

    # ============== 核心方法 ==============

    def classify_error(self, exception: Exception) -> ErrorType:
        """
        根据异常类型和消息分类错误

        Args:
            exception: 捕获的异常对象

        Returns:
            ErrorType 枚举值
        """
        msg = str(exception)
        exc_type = type(exception).__name__
        combined = f"{exc_type}: {msg}"

        for pattern, error_type in _ERROR_CLASSIFICATION_RULES:
            if pattern.search(combined):
                return error_type

        # 检查响应状态码
        if hasattr(exception, 'response'):
            status = getattr(exception.response, 'status_code', None)
            if status:
                if 400 <= status < 500:
                    return ErrorType.HTTP_4XX
                elif 500 <= status < 600:
                    return ErrorType.HTTP_5XX

        return ErrorType.UNKNOWN

    def record_success(self, duration_ms: float = 0.0) -> None:
        """
        记录成功请求

        Args:
            duration_ms: 请求耗时（毫秒）
        """
        with self._lock if self._lock else _null_lock():
            self._stats.add_success()
        logger.debug(
            f"[{self.source}] 成功获取 {self.data_type} 数据 "
            f"(symbol={self.symbol}, duration={duration_ms:.1f}ms)"
        )

    def record_failure(
        self,
        error_type: ErrorType,
        exception: Optional[Exception] = None,
        message: Optional[str] = None,
        attempt: int = 1,
        duration_ms: float = 0.0,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> ErrorRecord:
        """
        记录失败请求

        Args:
            error_type: 错误类型
            exception: 原始异常（可选）
            message: 错误消息（可选，从 exception 提取）
            attempt: 当前重试次数
            duration_ms: 请求耗时
            metadata: 附加信息

        Returns:
            ErrorRecord 记录对象
        """
        if message is None and exception is not None:
            message = str(exception)
        if message is None:
            message = f"Unknown {error_type.value} error"

        tb_str = ""
        if exception is not None:
            tb_str = traceback.format_exc()[:1000]  # 截断避免日志过大

        record = ErrorRecord(
            error_type=error_type,
            source=self.source,
            data_type=self.data_type,
            symbol=self.symbol,
            message=message,
            timestamp=datetime.now(),
            traceback_str=tb_str,
            attempt_count=attempt,
            duration_ms=duration_ms,
            metadata=metadata or {},
        )

        with self._lock if self._lock else _null_lock():
            self._stats.add_error(record)
            self._error_history.append(record)

        # 分级日志
        if error_type in (
            ErrorType.NETWORK_TIMEOUT,
            ErrorType.NETWORK_CONNECTION,
            ErrorType.HTTP_5XX,
        ):
            logger.warning(
                f"[{self.source}] {error_type.value}: {message} "
                f"(attempt={attempt}/{self.max_retry}, duration={duration_ms:.0f}ms)"
            )
        elif error_type in (
            ErrorType.HTTP_4XX,
            ErrorType.PARSE_JSON,
            ErrorType.PARSE_ENCODING,
        ):
            logger.error(
                f"[{self.source}] {error_type.value}: {message}"
            )
        elif error_type == ErrorType.DATA_QUALITY:
            logger.warning(f"[{self.source}] 数据质量验证失败: {message}")
        else:
            logger.debug(f"[{self.source}] {error_type.value}: {message}")

        return record

    def should_skip(self) -> bool:
        """
        判断是否应跳过本次数据（基于错误率告警）

        Returns:
            True 表示错误率过高，建议跳过
        """
        rate = self._stats.success_rate
        recent = self._stats.recent_error_count

        # 5分钟内失败率 > 80% 或错误数 > 10 条 → 跳过
        if rate < 20.0 and recent > 5:
            logger.error(
                f"[{self.source}] 错误率过高 ({100-rate:.1f}%)，建议跳过后续请求"
            )
            return True
        return False

    def get_stats(self) -> Dict[str, Any]:
        """获取当前统计信息"""
        return self._stats.to_dict()

    def get_recent_errors(self, limit: int = 10) -> List[Dict[str, Any]]:
        """获取最近错误记录"""
        return [r.to_dict() for r in list(self._error_history)[-limit:]]

    def reset(self) -> None:
        """重置统计"""
        with self._lock if self._lock else _null_lock():
            self._stats = ErrorStats(source=self.source)
            self._error_history.clear()


# ============== 便捷装饰器 ==============

def capture_errors(
    source: str,
    data_type: str = "unknown",
    max_retry: int = 3,
    fallback_func: Optional[Callable] = None,
):
    """
    错误捕获装饰器

    使用示例：
        @capture_errors(source="akshare", data_type="quote")
        def fetch_quote(symbols):
            return ak.stock_zh_a_spot_em()

    Args:
        source: 数据源名称
        data_type: 数据类型
        max_retry: 最大重试次数
        fallback_func: 降级函数（失败时调用）

    Returns:
        装饰后的函数
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            capture = ErrorCapture(source=source, data_type=data_type)
            last_error = None

            for attempt in range(1, max_retry + 1):
                start = time.time()
                try:
                    result = func(*args, **kwargs)
                    duration_ms = (time.time() - start) * 1000
                    capture.record_success(duration_ms)
                    return result

                except Exception as e:
                    duration_ms = (time.time() - start) * 1000
                    error_type = capture.classify_error(e)
                    last_error = capture.record_failure(
                        error_type, e, attempt=attempt, duration_ms=duration_ms
                    )
                    # 保存原始异常供最后重抛
                    last_error.metadata['_original_exception'] = e

                    # 检查是否需要降级
                    if fallback_func and attempt == max_retry:
                        logger.warning(f"[{source}] 所有重试失败，尝试降级")
                        try:
                            return fallback_func(*args, **kwargs)
                        except Exception as fe:
                            logger.error(f"降级也失败: {fe}")
                            raise

                    # 检查是否继续重试
                    if capture.should_skip():
                        raise

            # 所有重试耗尽，用最后一个原始异常抛出
            if last_error and last_error.metadata.get('_original_exception'):
                raise last_error.metadata['_original_exception']
            raise

        return wrapper
    return decorator


def retry_on_error(
    max_retries: int = 3,
    backoff_factors: List[float] = None,
    error_types: Optional[List[ErrorType]] = None,
):
    """
    条件重试装饰器（仅对指定错误类型重试）

    使用示例：
        @retry_on_error(max_retries=3, error_types=[ErrorType.NETWORK_TIMEOUT])
        def fetch_with_retry():
            return do_fetch()
    """
    backoff = backoff_factors or [1, 2, 5]

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            source = kwargs.get('source', 'unknown')
            data_type = kwargs.get('data_type', 'unknown')
            capture = ErrorCapture(source=source, data_type=data_type)

            for attempt in range(1, max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    error_type = capture.classify_error(e)
                    # 仅对允许的错误类型重试
                    if error_types and error_type not in error_types:
                        raise
                    capture.record_failure(error_type, e, attempt=attempt)
                    if attempt < max_retries:
                        wait = backoff[min(attempt - 1, len(backoff) - 1)]
                        logger.debug(f"等待 {wait}s 后重试 ({attempt}/{max_retries})")
                        time.sleep(wait)
                    else:
                        raise
            raise
        return wrapper
    return decorator


# ============== 空上下文管理器（无锁场景） ==============

class _NullLock:
    """空锁，用于无 threading 环境"""
    def __enter__(self):
        return self
    def __exit__(self, *args):
        pass

def _null_lock():
    return _NullLock()


# ============== 便捷工厂函数 ==============

def create_capture(
    source: str,
    data_type: str = "unknown",
    symbol: Optional[str] = None,
) -> ErrorCapture:
    """创建错误捕获器实例"""
    return ErrorCapture(source=source, data_type=data_type, symbol=symbol)


# 预定义常用捕获器
AKSHARE_CAPTURE = ErrorCapture(source="akshare", data_type="default")
SINA_CAPTURE = ErrorCapture(source="sina", data_type="default")
EASTMONEY_CAPTURE = ErrorCapture(source="eastmoney", data_type="default")


# ============== 模块级日志配置 ==============

def setup_error_capture_logging(level: int = logging.INFO) -> None:
    """配置错误捕获模块的日志"""
    handler = logging.StreamHandler()
    handler.setLevel(level)
    formatter = logging.Formatter(
        '%(asctime)s [%(levelname)s] %(name)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(level)


__all__ = [
    'ErrorCapture',
    'ErrorType',
    'ErrorRecord',
    'ErrorStats',
    'capture_errors',
    'retry_on_error',
    'create_capture',
    'AKSHARE_CAPTURE',
    'SINA_CAPTURE',
    'EASTMONEY_CAPTURE',
    'setup_error_capture_logging',
]
