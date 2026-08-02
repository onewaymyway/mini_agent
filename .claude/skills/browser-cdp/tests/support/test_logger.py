"""Test Logger Module for Browser CDP Tests

Provides structured logging with context, test step tracking, and integration
with pytest reporting.
"""
import logging
import sys
import time
import traceback
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Callable
import json


class LogLevel(Enum):
    """Log levels matching standard logging levels."""
    DEBUG = logging.DEBUG
    INFO = logging.INFO
    WARNING = logging.WARNING
    ERROR = logging.ERROR
    CRITICAL = logging.CRITICAL


@dataclass
class TestStep:
    """Represents a single test step with timing and status."""
    name: str
    description: str = ""
    start_time: float = field(default_factory=time.time)
    end_time: Optional[float] = None
    status: str = "running"  # running, passed, failed, skipped
    error: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    @property
    def duration(self) -> float:
        """Get step duration in seconds."""
        end = self.end_time or time.time()
        return end - self.start_time
    
    def finish(self, status: str = "passed", error: Optional[str] = None):
        """Mark step as finished."""
        self.end_time = time.time()
        self.status = status
        self.error = error
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "name": self.name,
            "description": self.description,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "duration": self.duration,
            "status": self.status,
            "error": self.error,
            "metadata": self.metadata
        }


@dataclass
class TestContext:
    """Context for a test execution, tracking steps and metadata."""
    test_name: str
    test_class: str = ""
    start_time: float = field(default_factory=time.time)
    end_time: Optional[float] = None
    steps: List[TestStep] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    status: str = "running"  # running, passed, failed, error, skipped
    error: Optional[str] = None
    
    @property
    def duration(self) -> float:
        """Get test duration in seconds."""
        end = self.end_time or time.time()
        return end - self.start_time
    
    def add_step(self, name: str, description: str = "") -> TestStep:
        """Add a new test step."""
        step = TestStep(name=name, description=description)
        self.steps.append(step)
        return step
    
    def finish(self, status: str = "passed", error: Optional[str] = None):
        """Mark test as finished."""
        self.end_time = time.time()
        self.status = status
        self.error = error
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "test_name": self.test_name,
            "test_class": self.test_class,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "duration": self.duration,
            "status": self.status,
            "error": self.error,
            "steps": [s.to_dict() for s in self.steps],
            "metadata": self.metadata
        }


class TestLogger:
    """Enhanced logger for browser CDP tests with step tracking and context."""
    
    def __init__(
        self,
        name: str,
        level: int = logging.INFO,
        log_file: Optional[Path] = None,
        json_output: bool = False,
        include_timestamp: bool = True,
        include_context: bool = True
    ):
        self.name = name
        self.logger = logging.getLogger(name)
        self.logger.setLevel(level)
        self.logger.propagate = False
        
        # Clear existing handlers
        self.logger.handlers.clear()
        
        # Console handler
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(level)
        console_formatter = logging.Formatter(
            '%(asctime)s | %(levelname)-8s | %(name)s | %(message)s' if include_timestamp
            else '%(levelname)-8s | %(name)s | %(message)s'
        )
        console_handler.setFormatter(console_formatter)
        self.logger.addHandler(console_handler)
        
        # File handler (optional)
        if log_file:
            log_file.parent.mkdir(parents=True, exist_ok=True)
            file_handler = logging.FileHandler(log_file, encoding='utf-8')
            file_handler.setLevel(logging.DEBUG)
            file_formatter = logging.Formatter(
                '%(asctime)s | %(levelname)-8s | %(name)s | %(message)s'
            )
            file_handler.setFormatter(file_formatter)
            self.logger.addHandler(file_handler)
        
        # JSON handler (optional)
        if json_output and log_file:
            json_file = log_file.with_suffix('.jsonl')
            json_handler = logging.FileHandler(json_file, encoding='utf-8')
            json_handler.setLevel(logging.DEBUG)
            json_handler.setFormatter(JsonFormatter())
            self.logger.addHandler(json_handler)
        
        self._context: Dict[str, Any] = {}
        self._include_context = include_context
        self._test_context: Optional[TestContext] = None
    
    def set_context(self, **kwargs):
        """Set context variables to include in all log messages and test metadata."""
        self._context.update(kwargs)
        if self._test_context:
            self._test_context.metadata.update(kwargs)
    
    def clear_context(self):
        """Clear context variables."""
        self._context.clear()
    
    def _format_message(self, message: str) -> str:
        """Format message with context if enabled."""
        if not self._include_context or not self._context:
            return message
        ctx_str = ' | '.join(f'{k}={v}' for k, v in self._context.items())
        return f'{message} | {ctx_str}'
    
    def debug(self, message: str, **kwargs):
        """Log debug message."""
        self.logger.debug(self._format_message(message), **kwargs)
    
    def info(self, message: str, **kwargs):
        """Log info message."""
        self.logger.info(self._format_message(message), **kwargs)
    
    def warning(self, message: str, **kwargs):
        """Log warning message."""
        self.logger.warning(self._format_message(message), **kwargs)
    
    def error(self, message: str, **kwargs):
        """Log error message."""
        self.logger.error(self._format_message(message), **kwargs)
    
    def critical(self, message: str, **kwargs):
        """Log critical message."""
        self.logger.critical(self._format_message(message), **kwargs)
    
    def exception(self, message: str, **kwargs):
        """Log exception with traceback."""
        self.logger.exception(self._format_message(message), **kwargs)
    
    # Test context management
    def start_test(self, test_name: str, test_class: str = "", **metadata) -> TestContext:
        """Start a new test context."""
        self._test_context = TestContext(
            test_name=test_name,
            test_class=test_class,
            metadata=metadata
        )
        self.info(f"TEST START: {test_name}" + (f" ({test_class})" if test_class else ""))
        return self._test_context
    
    def end_test(self, status: str = "passed", error: Optional[str] = None):
        """End the current test context."""
        if self._test_context:
            self._test_context.finish(status, error)
            self.info(
                f"TEST END: {self._test_context.test_name} | "
                f"Status: {status} | Duration: {self._test_context.duration:.3f}s"
                + (f" | Error: {error}" if error else "")
            )
            ctx = self._test_context
            self._test_context = None
            return ctx
        return None
    
    @contextmanager
    def test(self, test_name: str, test_class: str = "", **metadata):
        """Context manager for test execution."""
        ctx = self.start_test(test_name, test_class, **metadata)
        try:
            yield ctx
            self.end_test("passed")
        except Exception as e:
            self.end_test("failed", str(e))
            raise
    
    def step(self, name: str, description: str = "") -> TestStep:
        """Start a new test step."""
        if not self._test_context:
            self._test_context = TestContext(test_name="unknown", test_class="")
        step = self._test_context.add_step(name, description)
        self.info(f"STEP START: {name}" + (f" - {description}" if description else ""))
        return step
    
    def end_step(self, step: TestStep, status: str = "passed", error: Optional[str] = None):
        """End a test step."""
        step.finish(status, error)
        self.info(
            f"STEP END: {step.name} | Status: {status} | Duration: {step.duration:.3f}s"
            + (f" | Error: {error}" if error else "")
        )
    
    @contextmanager
    def step_context(self, name: str, description: str = ""):
        """Context manager for a test step."""
        step = self.step(name, description)
        try:
            yield step
            self.end_step(step, "passed")
        except Exception as e:
            self.end_step(step, "failed", str(e))
            raise
    
    def log_action(self, action: str, target: str = "", details: str = ""):
        """Log a test action (click, input, navigate, etc.)."""
        msg = f"ACTION: {action}"
        if target:
            msg += f" -> {target}"
        if details:
            msg += f" | {details}"
        self.info(msg)
    
    def log_assertion(self, assertion: str, passed: bool, expected: Any = None, actual: Any = None):
        """Log an assertion result."""
        status = "PASS" if passed else "FAIL"
        msg = f"ASSERTION {status}: {assertion}"
        if expected is not None:
            msg += f" | Expected: {expected}"
        if actual is not None:
            msg += f" | Actual: {actual}"
        if passed:
            self.info(msg)
        else:
            self.error(msg)
    
    def log_screenshot(self, path: str, description: str = ""):
        """Log screenshot capture."""
        self.info(f"SCREENSHOT: {path}" + (f" | {description}" if description else ""))
    
    def log_network_request(self, method: str, url: str, status: int = None, duration: float = None):
        """Log network request."""
        msg = f"NETWORK: {method} {url}"
        if status:
            msg += f" | Status: {status}"
        if duration:
            msg += f" | Duration: {duration:.3f}s"
        self.debug(msg)
    
    def log_console_message(self, level: str, text: str, source: str = ""):
        """Log browser console message."""
        msg = f"CONSOLE [{level.upper()}]: {text}"
        if source:
            msg += f" | Source: {source}"
        if level in ('error', 'warning'):
            self.warning(msg)
        else:
            self.debug(msg)
    
    def get_test_report(self) -> Optional[Dict[str, Any]]:
        """Get current test context as report dict."""
        if self._test_context:
            return self._test_context.to_dict()
        return None


class JsonFormatter(logging.Formatter):
    """JSON formatter for structured logging."""
    
    def format(self, record: logging.LogRecord) -> str:
        log_data = {
            "timestamp": datetime.fromtimestamp(record.created).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno
        }
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_data, ensure_ascii=False)


class ExceptionCapture:
    """Context manager for capturing and handling exceptions with context."""
    
    def __init__(
        self,
        logger: TestLogger,
        reraise: bool = True,
        capture_locals: bool = True,
        screenshot_on_error: Optional[Callable[[], str]] = None
    ):
        self.logger = logger
        self.reraise = reraise
        self.capture_locals = capture_locals
        self.screenshot_on_error = screenshot_on_error
        self.exception: Optional[Exception] = None
        self.traceback_str: Optional[str] = None
        self.locals_snapshot: Dict[str, Any] = {}
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None:
            self.exception = exc_val
            self.traceback_str = ''.join(traceback.format_exception(exc_type, exc_val, exc_tb))
            
            if self.capture_locals:
                # Capture local variables from the frame
                frame = exc_tb.tb_frame
                while frame.f_back:
                    frame = frame.f_back
                self.locals_snapshot = {
                    k: repr(v) for k, v in frame.f_locals.items()
                    if not k.startswith('_')
                }
            
            # Log the exception with full context
            self.logger.error(
                f"EXCEPTION: {exc_type.__name__}: {exc_val}"
                + (f"\nTraceback:\n{self.traceback_str}" if self.traceback_str else "")
            )
            
            if self.locals_snapshot:
                self.logger.debug(f"Local variables at error: {self.locals_snapshot}")
            
            # Take screenshot if callback provided
            if self.screenshot_on_error:
                try:
                    screenshot_path = self.screenshot_on_error()
                    self.logger.log_screenshot(screenshot_path, "Error screenshot")
                except Exception as e:
                    self.logger.warning(f"Failed to capture error screenshot: {e}")
            
            if self.reraise:
                return False  # Re-raise
            return True  # Suppress
        return False


def create_test_logger(
    test_name: str,
    log_dir: Optional[Path] = None,
    level: int = logging.INFO,
    json_output: bool = True
) -> TestLogger:
    """Factory function to create a test logger with standard configuration."""
    if log_dir:
        log_file = log_dir / f"{test_name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    else:
        log_file = None
    
    return TestLogger(
        name=f"test.{test_name}",
        level=level,
        log_file=log_file,
        json_output=json_output
    )


# Default logger instance for module-level use
_default_logger: Optional[TestLogger] = None


def get_default_logger() -> TestLogger:
    """Get or create default test logger."""
    global _default_logger
    if _default_logger is None:
        _default_logger = TestLogger("browser_cdp_test", level=logging.INFO)
    return _default_logger


def set_default_logger(logger: TestLogger):
    """Set the default test logger."""
    global _default_logger
    _default_logger = logger


# Convenience functions using default logger
def log_info(message: str, **kwargs):
    get_default_logger().info(message, **kwargs)


def log_error(message: str, **kwargs):
    get_default_logger().error(message, **kwargs)


def log_warning(message: str, **kwargs):
    get_default_logger().warning(message, **kwargs)


def log_debug(message: str, **kwargs):
    get_default_logger().debug(message, **kwargs)


def log_action(action: str, target: str = "", details: str = ""):
    get_default_logger().log_action(action, target, details)


def log_assertion(assertion: str, passed: bool, expected: Any = None, actual: Any = None):
    get_default_logger().log_assertion(assertion, passed, expected, actual)


@contextmanager
def capture_exceptions(
    logger: Optional[TestLogger] = None,
    reraise: bool = True,
    screenshot_on_error: Optional[Callable[[], str]] = None
):
    """Context manager for capturing exceptions with logging."""
    target_logger = logger or get_default_logger()
    with ExceptionCapture(target_logger, reraise, screenshot_on_error=screenshot_on_error) as capture:
        yield capture


# Pytest integration
def pytest_configure(config):
    """Pytest hook to configure logging."""
    # Add custom markers
    config.addinivalue_line("markers", "slow: marks tests as slow")
    config.addinivalue_line("markers", "browser: marks tests requiring browser")
    config.addinivalue_line("markers", "integration: marks integration tests")
    config.addinivalue_line("markers", "e2e: marks end-to-end tests")


def pytest_runtest_setup(item):
    """Pytest hook called before each test."""
    logger = create_test_logger(item.name)
    item._test_logger = logger
    logger.start_test(item.name, item.cls.__name__ if item.cls else "")


def pytest_runtest_teardown(item, nextitem):
    """Pytest hook called after each test."""
    if hasattr(item, '_test_logger'):
        status = "passed"
        error = None
        if hasattr(item, '_test_failed') and item._test_failed:
            status = "failed"
            error = str(item._test_failed)
        item._test_logger.end_test(status, error)


def pytest_runtest_call(item):
    """Pytest hook called during test execution."""
    # This is where the actual test runs
    pass


def pytest_exception_interact(node, call, report):
    """Pytest hook when exception occurs."""
    if hasattr(node, '_test_logger'):
        node._test_logger.error(f"Test exception: {call.excinfo.value}")
        node._test_failed = call.excinfo.value


# Export all public API
__all__ = [
    'LogLevel',
    'TestStep',
    'TestContext',
    'TestLogger',
    'JsonFormatter',
    'ExceptionCapture',
    'create_test_logger',
    'get_default_logger',
    'set_default_logger',
    'log_info',
    'log_error',
    'log_warning',
    'log_debug',
    'log_action',
    'log_assertion',
    'capture_exceptions',
    'pytest_configure',
    'pytest_runtest_setup',
    'pytest_runtest_teardown',
    'pytest_runtest_call',
    'pytest_exception_interact',
]
