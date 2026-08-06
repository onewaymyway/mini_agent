"""
test_browser_interaction.py - Browser Interaction Module Tests

Tests for infinite scroll, form submission, popup handling, AJAX wait,
page state management, and error recovery.
"""
import asyncio
import sys
import logging
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.core.browser_interaction import (
    BrowserInteraction,
    ErrorRecoveryManager,
    ErrorRecoveryStrategy,
    PopupType,
    infinite_scroll,
    submit_form,
    handle_popup,
    wait_for_ajax,
    capture_page_state,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# =========================================================================
# Mock Session
# =========================================================================

def create_mock_session():
    """Create a mock CDP session for testing"""
    session = MagicMock()
    session.execute_cdp_cmd = AsyncMock(return_value={"result": {"frameId": "test-frame"}})
    session.eval_js = AsyncMock(return_value="0")
    session.get_page_content = AsyncMock(return_value="<html><body>test</body></html>")
    session.click = AsyncMock(return_value=True)
    session.type_text = AsyncMock(return_value=True)
    session.wait_for_selector = AsyncMock(return_value=True)
    session.wait_for_network_idle = AsyncMock(return_value=True)
    session.send = MagicMock()
    return session


# =========================================================================
# Test BrowserInteraction
# =========================================================================

async def test_browser_interaction_init():
    """Test BrowserInteraction initialization"""
    logger.info("=== Test BrowserInteraction Init ===")
    
    session = create_mock_session()
    interaction = BrowserInteraction(session)
    
    assert interaction.session == session
    assert interaction._popup_handlers is not None
    assert interaction._ajax_requests == []
    assert interaction._page_states == []
    
    logger.info("BrowserInteraction initialization test passed")
    return True


async def test_infinite_scroll():
    """Test infinite scroll functionality"""
    logger.info("=== Test Infinite Scroll ===")
    
    session = create_mock_session()
    interaction = BrowserInteraction(session)
    
    # Mock scroll height changes - return plain integers
    scroll_heights = [0, 500, 1000, 1500, 2000, 2000, 2000]
    call_index = [0]
    
    async def mock_eval_js(script):
        if "scrollHeight" in script:
            result = scroll_heights[call_index[0]]
            call_index[0] += 1
            return result
        return "0"
    
    session.eval_js = AsyncMock(side_effect=mock_eval_js)
    
    # Test with max_items limit
    result = await interaction.infinite_scroll(
        item_selector=".item",
        max_items=5,
        scroll_delay=0.1
    )
    
    assert result is not None
    logger.info(f"Infinite scroll result: {result}")
    logger.info("Infinite scroll test passed")
    return True


async def test_submit_form():
    """Test form submission"""
    logger.info("=== Test Submit Form ===")
    
    session = create_mock_session()
    interaction = BrowserInteraction(session)
    
    # Test successful form submission
    result = await interaction.submit_form(
        form_selector="#search-form",
        fields={"keyword": "AI", "category": "tech"}
    )
    
    assert result.success is True
    logger.info("Form submission test passed")
    return True


async def test_handle_popup():
    """Test popup handling"""
    logger.info("=== Test Handle Popup ===")
    
    session = create_mock_session()
    interaction = BrowserInteraction(session)
    
    # Mock no popup detected
    session.eval_js = AsyncMock(return_value=None)
    result = await interaction.handle_popup(popup_type=PopupType.ALERT)
    assert result.success is True
    logger.info("Popup handling test passed (no popup)")
    
    return True


async def test_wait_for_ajax():
    """Test AJAX wait functionality"""
    logger.info("=== Test Wait for AJAX ===")
    
    session = create_mock_session()
    interaction = BrowserInteraction(session)
    
    # Mock AJAX state
    async def mock_eval_js(script):
        if "ajaxActive" in script:
            return "false"
        return "0"
    
    session.eval_js = AsyncMock(side_effect=mock_eval_js)
    
    result = await interaction.wait_for_ajax(timeout=5)
    assert result is not None
    logger.info("AJAX wait test passed")
    return True


async def test_capture_page_state():
    """Test page state capture"""
    logger.info("=== Test Capture Page State ===")
    
    session = create_mock_session()
    interaction = BrowserInteraction(session)
    
    # Mock page content - return flat dict
    session.eval_js = AsyncMock(return_value={
        "url": "http://test.com",
        "title": "Test Page",
        "scrollPosition": 0,
        "pageHeight": 2000,
        "elementCount": 100,
    })
    
    state = await interaction.capture_page_state()
    
    assert state is not None
    assert state.url == "http://test.com"
    assert state.title == "Test Page"
    assert state.timestamp > 0
    logger.info(f"Page state captured: {state}")
    logger.info("Page state capture test passed")
    return True


async def test_error_recovery():
    """Test error recovery strategies"""
    logger.info("=== Test Error Recovery ===")
    
    session = create_mock_session()
    interaction = BrowserInteraction(session)
    
    # Test RETRY strategy
    manager = ErrorRecoveryManager(interaction)
    
    # Test SKIP strategy (simplest)
    success, msg = await manager.recover(
        ConnectionError("network error"),
        strategy=ErrorRecoveryStrategy.SKIP
    )
    assert success is True
    logger.info("Error recovery SKIP strategy test passed")
    
    # Test ABORT strategy
    success, msg = await manager.recover(
        ConnectionError("permanent error"),
        strategy=ErrorRecoveryStrategy.ABORT
    )
    assert success is False
    logger.info("Error recovery ABORT strategy test passed")
    
    return True


async def test_search_and_collect():
    """Test search and collect combination operation"""
    logger.info("=== Test Search and Collect ===")
    
    session = create_mock_session()
    interaction = BrowserInteraction(session)
    
    # Mock search and collect
    session.eval_js = AsyncMock(return_value=True)
    session.wait_for_selector = AsyncMock(return_value=True)
    
    result = await interaction.search_and_collect(
        search_url="http://test.com/search",
        query="AI news",
        item_selector=".result-item",
        max_items=10
    )
    
    assert result is not None
    logger.info("Search and collect test passed")
    return True


# =========================================================================
# Test Convenience Functions
# =========================================================================

async def test_convenience_functions():
    """Test convenience functions"""
    logger.info("=== Test Convenience Functions ===")
    
    session = create_mock_session()
    
    # Test infinite_scroll function
    result = await infinite_scroll(session, item_selector=".item", max_items=5)
    assert result is not None
    logger.info("infinite_scroll() function test passed")
    
    # Test submit_form function
    result = await submit_form(session, "#form", {"field": "value"})
    assert result.success is True
    logger.info("submit_form() function test passed")
    
    # Test handle_popup function
    result = await handle_popup(session, popup_type=PopupType.ALERT)
    assert result.success is True
    logger.info("handle_popup() function test passed")
    
    # Test wait_for_ajax function
    result = await wait_for_ajax(session, timeout=5)
    assert result is not None
    logger.info("wait_for_ajax() function test passed")
    
    # Test capture_page_state function - need to mock eval_js to return dict
    async def mock_page_state(script):
        if "location.href" in script:
            return {
                "url": "http://test.com",
                "title": "Test Page",
                "scrollPosition": 0,
                "pageHeight": 2000,
                "elementCount": 100,
            }
        return "0"
    
    session.eval_js = AsyncMock(side_effect=mock_page_state)
    result = await capture_page_state(session)
    assert result is not None
    logger.info("capture_page_state() function test passed")
    
    return True


# =========================================================================
# Main Test Runner
# =========================================================================

async def run_all_tests():
    """Run all browser interaction tests"""
    tests = [
        ("BrowserInteraction Init", test_browser_interaction_init),
        ("Infinite Scroll", test_infinite_scroll),
        ("Submit Form", test_submit_form),
        ("Handle Popup", test_handle_popup),
        ("Wait for AJAX", test_wait_for_ajax),
        ("Capture Page State", test_capture_page_state),
        ("Error Recovery", test_error_recovery),
        ("Search and Collect", test_search_and_collect),
        ("Convenience Functions", test_convenience_functions),
    ]
    
    results = []
    for name, test_func in tests:
        try:
            result = await test_func()
            results.append((name, True, None))
            logger.info(f"PASS: {name}")
        except Exception as e:
            results.append((name, False, str(e)))
            logger.error(f"FAIL: {name} - {e}")
    
    # Summary
    passed = sum(1 for _, ok, _ in results if ok)
    total = len(results)
    
    logger.info(f"\n{'='*50}")
    logger.info(f"Test Results: {passed}/{total} passed")
    logger.info(f"{'='*50}")
    
    for name, ok, err in results:
        status = "PASS" if ok else "FAIL"
        logger.info(f"  [{status}] {name}")
        if err:
            logger.info(f"         Error: {err}")
    
    return 0 if passed == total else 1


if __name__ == "__main__":
    exit_code = asyncio.run(run_all_tests())
    sys.exit(exit_code)
