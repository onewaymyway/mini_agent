import pytest
from pathlib import Path
import sys

SKILL_DIR = Path(__file__).resolve().parent.parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))


def pytest_configure(config):
    """Register custom pytest marks to avoid warnings."""
    config.addinivalue_line("markers", "unit: Unit tests (mock CDP session)")
    config.addinivalue_line("markers", "integration: Integration tests (require real browser)")
    config.addinivalue_line("markers", "e2e: End-to-end tests (require real browser)")
    config.addinivalue_line("markers", "browser: Tests that require a browser instance")
    config.addinivalue_line("markers", "slow: Slow running tests")

@pytest.fixture(scope="session")
def test_session():
    print("\n=== Browser CDP Test Session Started ===")
    yield
    print("\n=== Browser CDP Test Session Ended ===")

@pytest.fixture(scope="function")
def mock_browser_instance():
    from unittest.mock import Mock, patch
    from src.core import browser_launch
    mock_tab = {"id": "test-tab-1", "url": "about:blank", "title": "Test Page", "is_active": True}
    mock_browser = {"tabs": [mock_tab], "active_tab_id": mock_tab["id"]}
    with patch.object(browser_launch, "cmd_dedicated") as mock_get:
        mock_get.return_value = {"port": 9333, "tab_id": "test-tab-1"}
        yield mock_browser

@pytest.fixture(scope="function")
def mock_page_content():
    return "<html><head><title>Test Page</title></head><body><h1>Hello World</h1></body></html>"

@pytest.fixture(scope="function")
def setup_test_environment():
    import logging
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger(__name__)
    logger.info("Test environment setup completed")
    yield
    logger.info("Test environment cleanup completed")

def pytest_addoption(parser):
    parser.addoption("--browser-type", action="store", default="chrome", help="Browser type: chrome or edge")
    parser.addoption("--headless", action="store_true", help="Run tests in headless mode")
    parser.addoption("--slow-mode", action="store_true", help="Enable slower execution for debugging")

@pytest.fixture(scope="session")
def browser_type(request):
    return request.config.getoption("--browser-type")

@pytest.fixture(scope="session")
def is_headless(request):
    return request.config.getoption("--headless")

@pytest.fixture(scope="session")
def slow_mode(request):
    return request.config.getoption("--slow-mode")