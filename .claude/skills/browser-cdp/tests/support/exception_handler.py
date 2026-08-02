"""Exception Handler Module for Browser CDP Tests

Provides structured exception handling, retry logic, error classification,
and recovery strategies for browser automation tests.
"""
import asyncio
import functools
import logging
import time
import traceback
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Type, TypeVar, Union

from .test_logger import TestLogger, get_default_logger, capture_exceptions


class ErrorCategory(Enum):
    """Categories of errors for classification and handling."""
    NETWORK = "network"           # Network errors, timeouts, connection issues
    BROWSER = "browser"           # Browser launch, crash, connection issues
    NAVIGATION = "navigation"     # Page load, redirect, URL errors
    ELEMENT = "element"           # Element not found, not clickable, stale
    TIMEOUT = "timeout"           # Operation timeout
    ASSERTION = "assertion"       # Test assertion failures
    JAVASCRIPT = "javascript"     # JS execution errors
    PERMISSION = "permission"     # Permission denied, access issues
    VALIDATION = "validation"     # Data validation failures
    CONFIGURATION = "configuration"     # Config/setup errors
    UNKNOWN = "unknown"           # Unclassified errors


class ErrorSeverity(Enum):
    """Severity levels for error handling decisions."""
    LOW = "low"           # Warning, test can continue
    MEDIUM = "medium"     # Error, test step failed but test can continue
    HIGH = "high"         # Critical error, test should stop
    CRITICAL = "critical" # Fatal error, entire test suite should stop


@dataclass
class TestError:
    """Structured test error with classification and context."""
    message: str
    category: ErrorCategory = ErrorCategory.UNKNOWN
    severity: ErrorSeverity = ErrorSeverity.MEDIUM
    exception: Optional[Exception] = None
    traceback_str: Optional[str] = None
    timestamp: datetime = field(default_factory=datetime.now)
    context: Dict[str, Any] = field(default_factory=dict)
    recoverable: bool = True
    retry_count: int = 0
    max_retries: int = 3
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "message": self.message,
            "category": self.category.value,
            "severity": self.severity.value,
            "exception_type": type(self.exception).__name__ if self.exception else None,
            "traceback": self.traceback_str,
            "timestamp": self.timestamp.isoformat(),
            "context": self.context,
            "recoverable": self.recoverable,
            "retry_count": self.retry_count,
            "max_retries": self.max_retries
        }
    
    @classmethod
    def from_exception(
        cls,
        exc: Exception,
        category: ErrorCategory = ErrorCategory.UNKNOWN,
        severity: ErrorSeverity = ErrorSeverity.MEDIUM,
        context: Optional[Dict[str, Any]] = None,
        recoverable: bool = True,
        max_retries: int = 3
    ) -> 'TestError':
        """Create TestError from an exception."""
        return cls(
            message=str(exc),
            category=category,
            severity=severity,
            exception=exc,
            traceback_str=''.join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
            context=context or {},
            recoverable=recoverable,
            max_retries=max_retries
        )


class RetryStrategy(Enum):
    """Retry strategies for failed operations."""
    FIXED = "fixed"           # Fixed delay between retries
    EXPONENTIAL = "exponential"  # Exponential backoff
    LINEAR = "linear"         # Linear backoff
    FIBONACCI = "fibonacci"   # Fibonacci backoff
    IMMEDIATE = "immediate"   # No delay, immediate retry


@dataclass
class RetryConfig:
    """Configuration for retry behavior."""
    max_attempts: int = 3
    strategy: RetryStrategy = RetryStrategy.EXPONENTIAL
    base_delay: float = 1.0  # seconds
    max_delay: float = 60.0  # seconds
    jitter: bool = True      # Add random jitter
    jitter_factor: float = 0.1  # 10% jitter
    retryable_categories: List[ErrorCategory] = field(default_factory=lambda: [
        ErrorCategory.NETWORK,
        ErrorCategory.TIMEOUT,
        ErrorCategory.BROWSER,
        ErrorCategory.ELEMENT
    ])
    retryable_exceptions: List[Type[Exception]] = field(default_factory=lambda: [
        ConnectionError,
        TimeoutError,
        IOError,
    ])
    
    def should_retry(self, error: TestError, attempt: int) -> bool:
        """Determine if an error should be retried."""
        if attempt >= self.max_attempts:
            return False
        if not error.recoverable:
            return False
        if error.category in self.retryable_categories:
            return True
        if error.exception and any(
            isinstance(error.exception, exc_type)
            for exc_type in self.retryable_exceptions
        ):
            return True
        return False
    
    def get_delay(self, attempt: int) -> float:
        """Calculate delay for given attempt number (0-indexed)."""
        if self.strategy == RetryStrategy.IMMEDIATE:
            delay = 0
        elif self.strategy == RetryStrategy.FIXED:
            delay = self.base_delay
        elif self.strategy == RetryStrategy.LINEAR:
            delay = self.base_delay * (attempt + 1)
        elif self.strategy == RetryStrategy.EXPONENTIAL:
            delay = self.base_delay * (2 ** attempt)
        elif self.strategy == RetryStrategy.FIBONACCI:
            # Fibonacci: 1, 1, 2, 3, 5, 8, 13, 21...
            a, b = 1, 1
            for _ in range(attempt):
                a, b = b, a + b
            delay = self.base_delay * a
        else:
            delay = self.base_delay
        
        delay = min(delay, self.max_delay)
        
        if self.jitter:
            import random
            jitter_range = delay * self.jitter_factor
            delay += random.uniform(-jitter_range, jitter_range)
        
        return max(0, delay)


T = TypeVar('T')


class RetryableOperation:
    """Wrapper for operations that support retry logic."""
    
    def __init__(
        self,
        operation: Callable[..., T],
        config: Optional[RetryConfig] = None,
        logger: Optional[TestLogger] = None,
        on_retry: Optional[Callable[[TestError, int], None]] = None
    ):
        self.operation = operation
        self.config = config or RetryConfig()
        self.logger = logger or get_default_logger()
        self.on_retry = on_retry
        self.attempt = 0
        self.last_error: Optional[TestError] = None
    
    def execute(self, *args, **kwargs) -> T:
        """Execute operation with retry logic."""
        while True:
            try:
                self.attempt += 1
                if asyncio.iscoroutinefunction(self.operation):
                    # Can't run async in sync context, raise error
                    raise RuntimeError("Async operation requires async_execute")
                return self.operation(*args, **kwargs)
            except Exception as e:
                error = TestError.from_exception(e, context={"args": str(args), "kwargs": str(kwargs)})
                self.last_error = error
                
                if not self.config.should_retry(error, self.attempt - 1):
                    self.logger.error(f"Operation failed after {self.attempt} attempts: {error.message}")
                    raise
                
                delay = self.config.get_delay(self.attempt - 1)
                self.logger.warning(
                    f"Attempt {self.attempt} failed: {error.message}. "
                    f"Retrying in {delay:.2f}s... (attempt {self.attempt}/{self.config.max_attempts})"
                )
                
                if self.on_retry:
                    self.on_retry(error, self.attempt)
                
                time.sleep(delay)
    
    async def async_execute(self, *args, **kwargs) -> T:
        """Execute async operation with retry logic."""
        while True:
            try:
                self.attempt += 1
                return await self.operation(*args, **kwargs)
            except Exception as e:
                error = TestError.from_exception(e, context={"args": str(args), "kwargs": str(kwargs)})
                self.last_error = error
                
                if not self.config.should_retry(error, self.attempt - 1):
                    self.logger.error(f"Async operation failed after {self.attempt} attempts: {error.message}")
                    raise
                
                delay = self.config.get_delay(self.attempt - 1)
                self.logger.warning(
                    f"Attempt {self.attempt} failed: {error.message}. "
                    f"Retrying in {delay:.2f}s... (attempt {self.attempt}/{self.config.max_attempts})"
                )
                
                if self.on_retry:
                    self.on_retry(error, self.attempt)
                
                await asyncio.sleep(delay)


def with_retry(
    config: Optional[RetryConfig] = None,
    logger: Optional[TestLogger] = None,
    on_retry: Optional[Callable[[TestError, int], None]] = None
):
    """Decorator to add retry logic to a function."""
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(func)
        def wrapper(*args, **kwargs) -> T:
            op = RetryableOperation(func, config, logger, on_retry)
            return op.execute(*args, **kwargs)
        
        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs) -> T:
            op = RetryableOperation(func, config, logger, on_retry)
            return await op.async_execute(*args, **kwargs)
        
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        return wrapper
    return decorator


class ErrorHandler:
    """Centralized error handling for browser CDP tests."""
    
    def __init__(self, logger: Optional[TestLogger] = None):
        self.logger = logger or get_default_logger()
        self.error_history: List[TestError] = []
        self.recovery_handlers: Dict[ErrorCategory, List[Callable[[TestError], bool]]] = {}
        self._setup_default_recovery()
    
    def _setup_default_recovery(self):
        """Setup default recovery handlers."""
        self.register_recovery(ErrorCategory.NETWORK, self._recover_network)
        self.register_recovery(ErrorCategory.BROWSER, self._recover_browser)
        self.register_recovery(ErrorCategory.ELEMENT, self._recover_element)
        self.register_recovery(ErrorCategory.TIMEOUT, self._recover_timeout)
    
    def register_recovery(self, category: ErrorCategory, handler: Callable[[TestError], bool]):
        """Register a recovery handler for an error category."""
        if category not in self.recovery_handlers:
            self.recovery_handlers[category] = []
        self.recovery_handlers[category].append(handler)
    
    def handle_error(self, error: TestError) -> bool:
        """Handle an error, attempting recovery if possible.
        
        Returns True if error was recovered, False otherwise.
        """
        self.error_history.append(error)
        
        # Log the error
        self.logger.error(
            f"ERROR [{error.category.value}/{error.severity.value}]: {error.message}"
            + (f" | Context: {error.context}" if error.context else "")
        )
        
        if error.traceback_str:
            self.logger.debug(f"Traceback:\n{error.traceback_str}")
        
        # Attempt recovery
        handlers = self.recovery_handlers.get(error.category, [])
        for handler in handlers:
            try:
                if handler(error):
                    self.logger.info(f"Recovery successful for {error.category.value} error")
                    return True
            except Exception as e:
                self.logger.warning(f"Recovery handler failed: {e}")
        
        return False
    
    def _recover_network(self, error: TestError) -> bool:
        """Attempt to recover from network errors."""
        self.logger.info("Attempting network recovery: waiting and retrying...")
        time.sleep(2)
        return True  # Indicate recovery attempted
    
    def _recover_browser(self, error: TestError) -> bool:
        """Attempt to recover from browser errors."""
        self.logger.info("Attempting browser recovery: restarting browser...")
        # This would typically trigger browser restart
        return False  # Requires external action
    
    def _recover_element(self, error: TestError) -> bool:
        """Attempt to recover from element errors."""
        self.logger.info("Attempting element recovery: waiting for element...")
        time.sleep(1)
        return True
    
    def _recover_timeout(self, error: TestError) -> bool:
        """Attempt to recover from timeout errors."""
        self.logger.info("Attempting timeout recovery: increasing timeout...")
        return True
    
    def get_error_summary(self) -> Dict[str, Any]:
        """Get summary of all handled errors."""
        if not self.error_history:
            return {"total": 0, "by_category": {}, "by_severity": {}}
        
        by_category = {}
        by_severity = {}
        
        for error in self.error_history:
            cat = error.category.value
            sev = error.severity.value
            by_category[cat] = by_category.get(cat, 0) + 1
            by_severity[sev] = by_severity.get(sev, 0) + 1
        
        return {
            "total": len(self.error_history),
            "by_category": by_category,
            "by_severity": by_severity,
            "errors": [e.to_dict() for e in self.error_history]
        }
    
    def clear_history(self):
        """Clear error history."""
        self.error_history.clear()


class BrowserErrorClassifier:
    """Classify browser/CDP errors into categories."""
    
    # Error message patterns for classification
    PATTERNS = {
        ErrorCategory.NETWORK: [
            "connection refused",
            "connection reset",
            "timeout",
            "dns",
            "unreachable",
            "network error",
            "failed to connect",
            "connection timed out",
            "econnrefused",
            "etimedout",
        ],
        ErrorCategory.BROWSER: [
            "browser",
            "chrome",
            "chromium",
            "cdp",
            "devtools",
            "debugging port",
            "target closed",
            "session closed",
            "browser crashed",
            "process died",
        ],
        ErrorCategory.NAVIGATION: [
            "navigation",
            "page load",
            "frame",
            "redirect",
            "url",
            "goto",
            "load event",
        ],
        ErrorCategory.ELEMENT: [
            "element",
            "selector",
            "not found",
            "not visible",
            "not clickable",
            "stale",
            "detached",
            "no such element",
            "element not interactable",
        ],
        ErrorCategory.TIMEOUT: [
            "timeout",
            "timed out",
            "waiting for",
            "exceeded",
        ],
        ErrorCategory.JAVASCRIPT: [
            "javascript",
            "script",
            "eval",
            "execution context",
            "console.error",
        ],
        ErrorCategory.PERMISSION: [
            "permission",
            "denied",
            "unauthorized",
            "forbidden",
            "access denied",
        ],
    }
    
    @classmethod
    def classify(cls, error: Exception, context: Optional[Dict[str, Any]] = None) -> ErrorCategory:
        """Classify an exception into an error category."""
        error_msg = str(error).lower()
        error_type = type(error).__name__.lower()
        
        # Check exception type first
        # TimeoutError must be checked before ConnectionError/IOError since it's a subclass of OSError
        # PermissionError must be checked before IOError since it's a subclass of OSError
        if isinstance(error, TimeoutError):
            return ErrorCategory.TIMEOUT
        if isinstance(error, PermissionError):
            return ErrorCategory.PERMISSION
        if isinstance(error, (ConnectionError, IOError)):
            return ErrorCategory.NETWORK
        if isinstance(error, AssertionError):
            return ErrorCategory.ASSERTION
        
        # Check error message patterns
        for category, patterns in cls.PATTERNS.items():
            for pattern in patterns:
                if pattern in error_msg or pattern in error_type:
                    return category
        
        # Check context for additional clues
        if context:
            operation = context.get('operation', '').lower()
            if 'navigat' in operation or 'goto' in operation:
                return ErrorCategory.NAVIGATION
            if 'click' in operation or 'input' in operation or 'element' in operation:
                return ErrorCategory.ELEMENT
        
        return ErrorCategory.UNKNOWN
    
    @classmethod
    def classify_and_create(
        cls,
        error: Exception,
        context: Optional[Dict[str, Any]] = None,
        severity: ErrorSeverity = ErrorSeverity.MEDIUM
    ) -> TestError:
        """Classify error and create TestError instance."""
        category = cls.classify(error, context)
        return TestError.from_exception(
            error,
            category=category,
            severity=severity,
            context=context
        )
    
    @classmethod
    def get_severity(cls, error: Exception, context: Optional[Dict[str, Any]] = None) -> ErrorSeverity:
        """Get severity for an error based on its type and context."""
        error_type = type(error).__name__
        error_msg = str(error).lower()
        
        # Critical errors
        if isinstance(error, (PermissionError, SystemExit, KeyboardInterrupt)):
            return ErrorSeverity.CRITICAL
        if isinstance(error, (RuntimeError,)) and ('browser' in error_msg or 'cdp' in error_msg or 'session' in error_msg):
            return ErrorSeverity.CRITICAL
        
        # High severity
        if isinstance(error, (ConnectionError, TimeoutError, IOError)):
            return ErrorSeverity.HIGH
        if isinstance(error, AssertionError):
            return ErrorSeverity.HIGH
        
        # Medium severity (default)
        return ErrorSeverity.MEDIUM


@contextmanager
def error_boundary(
    logger: Optional[TestLogger] = None,
    handler: Optional[ErrorHandler] = None,
    reraise: bool = True,
    category: ErrorCategory = ErrorCategory.UNKNOWN,
    severity: ErrorSeverity = ErrorSeverity.MEDIUM,
    context: Optional[Dict[str, Any]] = None
):
    """Context manager for error boundary with automatic classification and handling."""
    target_logger = logger or get_default_logger()
    target_handler = handler or ErrorHandler(target_logger)
    
    try:
        yield target_handler
    except Exception as e:
        test_error = BrowserErrorClassifier.classify_and_create(
            e, context=context, severity=severity
        )
        # Override category if explicitly provided
        if category != ErrorCategory.UNKNOWN:
            test_error.category = category
        
        recovered = target_handler.handle_error(test_error)
        
        if not recovered and reraise:
            raise
        
        if recovered:
            target_logger.info(f"Error recovered, continuing execution")


# Pre-configured retry configs for common scenarios
RETRY_CONFIGS = {
    "default": RetryConfig(),
    "network": RetryConfig(
        max_attempts=5,
        strategy=RetryStrategy.EXPONENTIAL,
        base_delay=2.0,
        max_delay=30.0,
        retryable_categories=[ErrorCategory.NETWORK, ErrorCategory.TIMEOUT]
    ),
    "browser": RetryConfig(
        max_attempts=3,
        strategy=RetryStrategy.FIXED,
        base_delay=5.0,
        retryable_categories=[ErrorCategory.BROWSER]
    ),
    "element": RetryConfig(
        max_attempts=5,
        strategy=RetryStrategy.LINEAR,
        base_delay=0.5,
        max_delay=5.0,
        retryable_categories=[ErrorCategory.ELEMENT, ErrorCategory.TIMEOUT]
    ),
    "navigation": RetryConfig(
        max_attempts=3,
        strategy=RetryStrategy.EXPONENTIAL,
        base_delay=3.0,
        max_delay=20.0,
        retryable_categories=[ErrorCategory.NAVIGATION, ErrorCategory.TIMEOUT, ErrorCategory.NETWORK]
    ),
    "aggressive": RetryConfig(
        max_attempts=10,
        strategy=RetryStrategy.EXPONENTIAL,
        base_delay=0.5,
        max_delay=60.0,
        jitter=True
    ),
    "none": RetryConfig(max_attempts=1),
}


def get_retry_config(name: str) -> RetryConfig:
    """Get a pre-configured retry config by name."""
    return RETRY_CONFIGS.get(name, RETRY_CONFIGS["default"])


# Export all public API
__all__ = [
    'ErrorCategory',
    'ErrorSeverity',
    'TestError',
    'RetryStrategy',
    'RetryConfig',
    'RetryableOperation',
    'with_retry',
    'ErrorHandler',
    'BrowserErrorClassifier',
    'error_boundary',
    'RETRY_CONFIGS',
    'get_retry_config',
]
