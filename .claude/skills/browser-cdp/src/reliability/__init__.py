"""
Browser-CDP 可靠性保障层

提供统一的重试框架、错误分类、智能等待、连接健康检查等能力。
"""

from .error import (
    ErrorCategory,
    ReliabilityError,
    CDPConnectionLostError,
    CDPCommandTimeoutError,
    ElementNotFoundError,
    ElementIndexInvalidError,
    NavigationTimeoutError,
    CaptchaDetectedError,
    BlockedByAntiBotError,
    is_retryable,
    categorize_error,
)
from .retry import (
    BackoffStrategy,
    RetryConfig,
    CircuitBreaker,
    retry_operation,
    retry_operation_async,
    with_retry,
    with_retry_async,
    get_retry_config,
)
from .wait import SmartWaiter, WaitStrategy, smart_wait
from .health import ConnectionHealthChecker, ConnectionPoolHealthChecker
from .searcher_utils import (
    SEARCHER_DEFAULTS,
    SearcherConfig,
    SearcherMixin,
    ElementLocator,
    SearcherErrorProcessor,
    run_cmd_with_retry,
    run_cmd_with_retry_sync,
)
from .metrics import ReliabilityMetrics, get_metrics, reset_metrics
from .logging import OperationLogger, get_logger, reset_logger
from .dashboard import ReliabilityDashboard, get_dashboard, reset_dashboard
from .alert import (
    AlertRule,
    AlertSeverity,
    AlertManager,
    AlertNotification,
    WebhookNotification,
    EmailNotification,
    get_alert_manager,
    reset_alert_manager,
)
from .log_query import LogQuery, get_log_query, reset_log_query
from .middleware import (
    ErrorMiddleware,
    ErrorContext,
    OperationType,
    get_middleware,
    reset_middleware,
    with_error_handling,
    with_error_handling_async,
)

__all__ = [
    # 错误分类
    "ErrorCategory",
    "ReliabilityError",
    "CDPConnectionLostError",
    "CDPCommandTimeoutError",
    "ElementNotFoundError",
    "ElementIndexInvalidError",
    "NavigationTimeoutError",
    "CaptchaDetectedError",
    "BlockedByAntiBotError",
    "is_retryable",
    "categorize_error",
    # 重试框架
    "BackoffStrategy",
    "RetryConfig",
    "CircuitBreaker",
    "retry_operation",
    "retry_operation_async",
    "with_retry",
    "with_retry_async",
    "get_retry_config",
    # 智能等待
    "SmartWaiter",
    "WaitStrategy",
    "smart_wait",
    # 连接健康
    "ConnectionHealthChecker",
    "ConnectionPoolHealthChecker",
    # 搜索器工具
    "SEARCHER_DEFAULTS",
    "SearcherConfig",
    "SearcherMixin",
    "ElementLocator",
    "SearcherErrorProcessor",
    "run_cmd_with_retry",
    "run_cmd_with_retry_sync",
    # 监控指标
    "ReliabilityMetrics",
    "get_metrics",
    "reset_metrics",
    # 操作日志
    "OperationLogger",
    "get_logger",
    "reset_logger",
    # 监控面板
    "ReliabilityDashboard",
    "get_dashboard",
    "reset_dashboard",
    # 告警系统
    "AlertRule",
    "AlertSeverity",
    "AlertManager",
    "AlertNotification",
    "WebhookNotification",
    "EmailNotification",
    "get_alert_manager",
    "reset_alert_manager",
    # 日志查询
    "LogQuery",
    "get_log_query",
    "reset_log_query",
    # 错误处理中间件
    "ErrorMiddleware",
    "ErrorContext",
    "OperationType",
    "get_middleware",
    "reset_middleware",
    "with_error_handling",
    "with_error_handling_async",
]

__version__ = "1.2.0"
