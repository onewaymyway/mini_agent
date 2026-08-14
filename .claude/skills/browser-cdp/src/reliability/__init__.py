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
from .degradation import (
    DegradationMode,
    DegradationConfig,
    DegradationHandler,
    get_degradation_handler,
    reset_degradation_handler,
    degrade_skip,
    degrade_error,
    degrade_fallback,
)
from .failover_manager import (
    FailoverManager,
    FailoverStrategy,
    get_failover_manager,
    reset_failover_manager,
)
from .session_recovery import (
    SessionRecovery,
    get_session_recovery,
    reset_session_recovery,
)
from .exception_handler import (
    ExceptionHandler,
    get_exception_handler,
    reset_exception_handler,
    with_exception_handling,
    async_with_exception_handling,
)
from .enhanced_retry import (
    RetryStats,
    RetryConfig as EnhancedRetryConfig,
    CircuitBreaker as EnhancedCircuitBreaker,
    retry_operation as enhanced_retry_operation,
    retry_operation_async as enhanced_retry_operation_async,
    with_retry as enhanced_with_retry,
    with_retry_async as enhanced_with_retry_async,
    get_retry_config as get_enhanced_retry_config,
    get_config_for_error,
)
from .enhanced_timeout import (
    EnhancedTimeoutConfig,
    EnhancedTimeoutManager,
    get_enhanced_timeout_manager,
    with_enhanced_timeout,
    async_with_enhanced_timeout,
)
from .enhanced_exception_handler import (
    EnhancedExceptionHandler,
    get_enhanced_exception_handler,
    reset_enhanced_exception_handler,
    with_enhanced_exception_handling,
    async_with_enhanced_exception_handling,
)
from .adaptive_timeout import (
    AdaptiveTimeout,
    TimeoutPredictor,
    get_adaptive_timeout,
    get_timeout_predictor,
    record_response,
    get_optimal_timeout,
)
from .smart_wait_v2 import (
    SmartWaiterV2,
    WaitStrategy as WaitStrategyV2,
    WebsiteType,
    smart_wait_v2,
)
from .adaptive_backoff import (
    AdaptiveBackoff,
    BackoffStrategy as AdaptiveBackoffStrategy,
    FailurePattern,
    get_adaptive_backoff,
    record_failure,
    record_success,
    get_backoff_delay,
    get_failure_pattern,
)
from .predictive_retry import (
    PredictiveRetry,
    RetryPrediction,
    get_predictive_retry,
    record_attempt,
    predict_optimal_retries,
    get_retry_prediction,
)
from .operation_validator import (
    BaseValidator,
    ExistenceValidator,
    VisibilityValidator,
    TextContentValidator,
    URLMatchValidator,
    CustomValidator,
    OperationValidator,
    ValidationRule,
    ValidationContext,
    ValidationReport,
    ValidationResult,
    ValidatorType,
    VALIDATION_TEMPLATES,
    create_validator_from_template,
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
    # 降级策略
    "DegradationMode",
    "DegradationConfig",
    "DegradationHandler",
    "get_degradation_handler",
    "reset_degradation_handler",
    "degrade_skip",
    "degrade_error",
    "degrade_fallback",
    # 故障转移
    "FailoverManager",
    "FailoverStrategy",
    "get_failover_manager",
    "reset_failover_manager",
    # 会话恢复
    "SessionRecovery",
    "get_session_recovery",
    "reset_session_recovery",
    # 异常处理
    "ExceptionHandler",
    "get_exception_handler",
    "reset_exception_handler",
    "with_exception_handling",
    "async_with_exception_handling",
    # 增强重试
    "RetryStats",
    "EnhancedRetryConfig",
    "EnhancedCircuitBreaker",
    "enhanced_retry_operation",
    "enhanced_retry_operation_async",
    "enhanced_with_retry",
    "enhanced_with_retry_async",
    "get_enhanced_retry_config",
    # 增强超时
    "EnhancedTimeoutConfig",
    "EnhancedTimeoutManager",
    "get_enhanced_timeout_manager",
    "with_enhanced_timeout",
    "async_with_enhanced_timeout",
    # 增强异常处理
    "EnhancedExceptionHandler",
    "get_enhanced_exception_handler",
    "reset_enhanced_exception_handler",
    "with_enhanced_exception_handling",
    "async_with_enhanced_exception_handling",
    # 自适应超时
    "AdaptiveTimeout",
    "TimeoutPredictor",
    "get_adaptive_timeout",
    "get_timeout_predictor",
    "record_response",
    "get_optimal_timeout",
    # 增强智能等待
    "SmartWaiterV2",
    "WaitStrategyV2",
    "WebsiteType",
    "smart_wait_v2",
    # 自适应退避
    "AdaptiveBackoff",
    "AdaptiveBackoffStrategy",
    "FailurePattern",
    "get_adaptive_backoff",
    "record_failure",
    "record_success",
    "get_backoff_delay",
    "get_failure_pattern",
    # 预测性重试
    "PredictiveRetry",
    "RetryPrediction",
    "get_predictive_retry",
    "record_attempt",
    "predict_optimal_retries",
    "get_retry_prediction",
    # 操作验证
    "BaseValidator",
    "ExistenceValidator",
    "VisibilityValidator",
    "TextContentValidator",
    "URLMatchValidator",
    "CustomValidator",
    "OperationValidator",
    "ValidationRule",
    "ValidationContext",
    "ValidationReport",
    "ValidationResult",
    "ValidatorType",
    "VALIDATION_TEMPLATES",
    "create_validator_from_template",
]

__version__ = "1.2.0"
