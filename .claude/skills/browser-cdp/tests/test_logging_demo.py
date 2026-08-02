"""Demo test showing how to use the logging and exception handling framework."""
import pytest
import time
from pathlib import Path

from support import (
    TestLogger,
    create_test_logger,
    capture_exceptions,
    ErrorHandler,
    ErrorCategory,
    ErrorSeverity,
    BrowserErrorClassifier,
    RetryConfig,
    RetryStrategy,
    with_retry,
    TestReporter,
    TestResult,
    TestStep,
    log_info,
    log_error,
    log_action,
    log_assertion,
)


class TestLoggingDemo:
    """Demo tests for the logging framework."""
    
    @pytest.fixture(autouse=True)
    def setup_logger(self, tmp_path, request):
        """Setup test logger for each test."""
        test_name = request.node.name
        self.logger = create_test_logger(
            test_name,
            log_dir=tmp_path / "logs",
            level=10,  # DEBUG
            json_output=True
        )
        self._test_name = test_name
        # Start test context so get_test_report() returns correct test name
        self.logger.start_test(test_name, self.__class__.__name__)
        yield
        # Cleanup
        self.logger.end_test()
    
    def test_basic_logging(self, request):
        """Test basic logging functionality."""
        self.logger.info("Starting test")
        self.logger.debug("Debug message")
        self.logger.warning("Warning message")
        
        # Log actions
        self.logger.log_action("navigate", "https://example.com", "Initial page load")
        self.logger.log_action("click", "button#submit", "Submit form")
        
        # Log assertions
        self.logger.log_assertion("Page title contains 'Example'", True, "Example", "Example Domain")
        self.logger.log_assertion("Element is visible", False, True, False)
        
        # Log screenshot
        self.logger.log_screenshot("/path/to/screenshot.png", "After form submission")
        
        # Test step tracking
        with self.logger.step_context("Navigate to page", "Open example.com"):
            time.sleep(0.1)  # Simulate work
        
        with self.logger.step_context("Fill form", "Enter test data"):
            time.sleep(0.1)
        
        # Get test report
        report = self.logger.get_test_report()
        assert report is not None
        assert report['test_name'] == request.node.name
        assert len(report['steps']) == 2
    
    def test_exception_capture(self):
        """Test exception capture with context."""
        def failing_operation():
            raise ConnectionError("Failed to connect to browser")
        
        # Test capture_exceptions context manager
        with capture_exceptions(self.logger, reraise=False) as capture:
            failing_operation()
        
        assert capture.exception is not None
        assert isinstance(capture.exception, ConnectionError)
        assert capture.traceback_str is not None
        
        # Test with reraise=True (should raise)
        with pytest.raises(ConnectionError):
            with capture_exceptions(self.logger, reraise=True):
                failing_operation()
    
    def test_error_handler(self):
        """Test error handler with recovery."""
        handler = ErrorHandler(self.logger)
        
        # Test network error recovery
        error = BrowserErrorClassifier.classify_and_create(
            ConnectionError("Connection refused"),
            context={"operation": "navigate", "url": "https://example.com"}
        )
        
        recovered = handler.handle_error(error)
        assert recovered is True  # Network errors are recoverable
        
        # Test element error recovery
        error2 = BrowserErrorClassifier.classify_and_create(
            Exception("Element not found: button#submit"),
            context={"operation": "click", "selector": "button#submit"}
        )
        
        recovered2 = handler.handle_error(error2)
        assert recovered2 is True
        
        # Check error summary
        summary = handler.get_error_summary()
        assert summary['total'] == 2
        assert summary['by_category']['network'] == 1
        assert summary['by_category']['element'] == 1
    
    def test_retry_decorator(self):
        """Test retry decorator."""
        attempt_count = 0
        
        @with_retry(config=RetryConfig(max_attempts=3, strategy=RetryStrategy.FIXED, base_delay=0.1))
        def flaky_operation():
            nonlocal attempt_count
            attempt_count += 1
            if attempt_count < 3:
                raise ConnectionError(f"Attempt {attempt_count} failed")
            return "success"
        
        result = flaky_operation()
        assert result == "success"
        assert attempt_count == 3
    
    def test_retry_with_custom_config(self):
        """Test retry with custom configuration."""
        from support import RETRY_CONFIGS
        
        attempt_count = 0
        
        @with_retry(config=RETRY_CONFIGS["network"])
        def network_operation():
            nonlocal attempt_count
            attempt_count += 1
            if attempt_count < 2:
                raise TimeoutError("Network timeout")
            return "connected"
        
        result = network_operation()
        assert result == "connected"
        assert attempt_count == 2
    
    def test_error_boundary(self):
        """Test error boundary context manager."""
        from support import error_boundary
        
        handler = ErrorHandler(self.logger)
        
        # Test error that gets recovered
        with error_boundary(logger=self.logger, handler=handler, reraise=False) as boundary:
            raise ConnectionError("Network error")
        
        # Test error that is not recovered (browser crash)
        with pytest.raises(Exception):
            with error_boundary(
                logger=self.logger,
                handler=handler,
                reraise=True,
                category=ErrorCategory.BROWSER,
                severity=ErrorSeverity.CRITICAL
            ):
                raise Exception("Browser crashed")
    
    def test_test_reporter(self, tmp_path):
        """Test report generation."""
        reporter = TestReporter(output_dir=tmp_path / "reports")
        
        # Start suite
        reporter.start_suite("Demo Suite")
        
        # Add test results
        from support import TestStep
        
        step1 = TestStep(name="Navigate", description="Go to example.com")
        step1.finish("passed")
        
        step2 = TestStep(name="Click button", description="Submit form")
        step2.finish("passed")
        
        result = TestResult(
            test_name="test_example",
            test_class="TestLoggingDemo",
            status="passed",
            start_time=time.time() - 10,
            end_time=time.time(),
            duration=10.0,
            steps=[step1, step2],
            assertions=[
                {"message": "Title contains Example", "passed": True},
                {"message": "Button is clickable", "passed": True}
            ],
            screenshots=["screenshot1.png", "screenshot2.png"]
        )
        
        reporter.add_test_result(result)
        
        # Add a failed test
        step3 = TestStep(name="Navigate", description="Go to error page")
        step3.finish("failed", "Page not found")
        
        result2 = TestResult(
            test_name="test_error",
            test_class="TestLoggingDemo",
            status="failed",
            start_time=time.time() - 5,
            end_time=time.time(),
            duration=5.0,
            steps=[step3],
            errors=[],
            assertions=[
                {"message": "Page loads successfully", "passed": False}
            ]
        )
        
        reporter.add_test_result(result2)
        
        # End suite and generate reports
        reporter.end_suite()
        
        reports = reporter.generate_all_reports("demo_report")
        
        assert 'json' in reports
        assert 'junit' in reports
        assert 'html' in reports
        assert 'markdown' in reports
        
        # Verify files exist
        for path in reports.values():
            assert path.exists()
            assert path.stat().st_size > 0
        
        # Check summary
        summary = reporter.get_summary()
        assert summary['total_tests'] == 2
        assert summary['passed'] == 1
        assert summary['failed'] == 1
        assert summary['pass_rate'] == 50.0
    
    def test_integration_with_pytest_fixtures(self, mock_browser_instance, mock_page_content):
        """Test integration with pytest fixtures."""
        self.logger.set_context(browser="chrome", headless=False)
        
        self.logger.log_action("launch", "browser", "Starting Chrome")
        self.logger.info(f"Browser instance: {mock_browser_instance}")
        
        self.logger.log_action("navigate", "https://example.com", "Loading page")
        self.logger.info(f"Page content length: {len(mock_page_content)}")
        
        # Simulate some work
        with self.logger.step_context("Extract content", "Get page text"):
            time.sleep(0.05)
        
        self.logger.log_assertion("Page has title", True, "Example Domain", "Example Domain")
        
        # Verify test context
        report = self.logger.get_test_report()
        assert report['metadata'].get('browser') == "chrome"
        assert len(report['steps']) >= 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
