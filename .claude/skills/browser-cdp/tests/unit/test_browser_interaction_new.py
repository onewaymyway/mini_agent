"""
test_browser_interaction_new.py - New Browser Interaction Module Tests

Tests for new interaction methods: drag_and_drop, hover_and_click, right_click,
double_click, select_dropdown, upload_file, scroll_to_element, fill_and_submit,
take_screenshot_with_annotation, execute_javascript.
"""
import asyncio
import sys
import logging
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.core.browser_interaction import (
    BrowserInteraction,
    InteractionResult,
    drag_and_drop,
    hover_and_click,
    right_click,
    double_click,
    select_dropdown,
    upload_file,
    scroll_to_element,
    fill_and_submit,
    take_screenshot_with_annotation,
    execute_javascript,
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
# Test drag_and_drop
# =========================================================================

async def test_drag_and_drop():
    """Test drag and drop functionality"""
    logger.info("=== Test Drag and Drop ===")
    
    session = create_mock_session()
    interaction = BrowserInteraction(session)
    
    # Mock successful drag
    async def mock_eval_js(script):
        if "drag" in script.lower() or "mousedown" in script:
            return {"success": True}
        return "0"
    
    session.eval_js = AsyncMock(side_effect=mock_eval_js)
    
    result = await interaction.drag_and_drop(
        from_selector="#draggable",
        to_selector="#droppable",
        duration=1.0,
    )
    
    assert result is not None
    assert result.success is True
    assert result.operation == "drag_and_drop"
    logger.info("Drag and drop test passed")
    return True


async def test_drag_and_drop_element_not_found():
    """Test drag and drop with missing element"""
    logger.info("=== Test Drag and Drop - Element Not Found ===")
    
    session = create_mock_session()
    interaction = BrowserInteraction(session)
    
    async def mock_eval_js(script):
        return {"success": False, "error": "Element not found"}
    
    session.eval_js = AsyncMock(side_effect=mock_eval_js)
    
    result = await interaction.drag_and_drop(
        from_selector="#missing",
        to_selector="#also-missing",
    )
    
    assert result is not None
    assert result.success is False
    logger.info("Drag and drop element not found test passed")
    return True


# =========================================================================
# Test hover_and_click
# =========================================================================

async def test_hover_and_click():
    """Test hover and click functionality"""
    logger.info("=== Test Hover and Click ===")
    
    session = create_mock_session()
    interaction = BrowserInteraction(session)
    
    async def mock_eval_js(script):
        if "mouseover" in script or "click" in script:
            return {"success": True}
        return "0"
    
    session.eval_js = AsyncMock(side_effect=mock_eval_js)
    
    result = await interaction.hover_and_click(
        selector="#menu-item",
        hover_duration=0.5,
    )
    
    assert result is not None
    assert result.success is True
    assert result.operation == "hover_and_click"
    logger.info("Hover and click test passed")
    return True


# =========================================================================
# Test right_click
# =========================================================================

async def test_right_click():
    """Test right click functionality"""
    logger.info("=== Test Right Click ===")
    
    session = create_mock_session()
    interaction = BrowserInteraction(session)
    
    async def mock_eval_js(script):
        if "contextmenu" in script:
            return {"success": True}
        return "0"
    
    session.eval_js = AsyncMock(side_effect=mock_eval_js)
    
    result = await interaction.right_click(selector="#context-menu-target")
    
    assert result is not None
    assert result.success is True
    assert result.operation == "right_click"
    logger.info("Right click test passed")
    return True


# =========================================================================
# Test double_click
# =========================================================================

async def test_double_click():
    """Test double click functionality"""
    logger.info("=== Test Double Click ===")
    
    session = create_mock_session()
    interaction = BrowserInteraction(session)
    
    async def mock_eval_js(script):
        if "dblclick" in script or "mousedown" in script:
            return {"success": True}
        return "0"
    
    session.eval_js = AsyncMock(side_effect=mock_eval_js)
    
    result = await interaction.double_click(selector="#editable")
    
    assert result is not None
    assert result.success is True
    assert result.operation == "double_click"
    logger.info("Double click test passed")
    return True


# =========================================================================
# Test select_dropdown
# =========================================================================

async def test_select_dropdown():
    """Test dropdown selection"""
    logger.info("=== Test Select Dropdown ===")
    
    session = create_mock_session()
    interaction = BrowserInteraction(session)
    
    async def mock_eval_js(script):
        if "SELECT" in script and "change" in script:
            return {"success": True, "selected": "option1"}
        return "0"
    
    session.eval_js = AsyncMock(side_effect=mock_eval_js)
    
    result = await interaction.select_dropdown(
        selector="#country-select",
        value="China",
    )
    
    assert result is not None
    assert result.success is True
    assert result.operation == "select_dropdown"
    logger.info("Select dropdown test passed")
    return True


async def test_select_dropdown_not_found():
    """Test dropdown selection with missing element"""
    logger.info("=== Test Select Dropdown - Not Found ===")
    
    session = create_mock_session()
    interaction = BrowserInteraction(session)
    
    async def mock_eval_js(script):
        return {"success": False, "error": "Option not found"}
    
    session.eval_js = AsyncMock(side_effect=mock_eval_js)
    
    result = await interaction.select_dropdown(
        selector="#missing-select",
        value="Unknown",
    )
    
    assert result is not None
    assert result.success is False
    logger.info("Select dropdown not found test passed")
    return True


# =========================================================================
# Test upload_file
# =========================================================================

async def test_upload_file():
    """Test file upload"""
    logger.info("=== Test Upload File ===")
    
    session = create_mock_session()
    interaction = BrowserInteraction(session)
    
    async def mock_eval_js(script):
        if "file" in script.lower() and "change" in script:
            return {"success": True, "message": "File input triggered"}
        return "0"
    
    session.eval_js = AsyncMock(side_effect=mock_eval_js)
    
    result = await interaction.upload_file(
        selector="#file-input",
        file_path="/path/to/file.txt",
    )
    
    assert result is not None
    assert result.success is True
    assert result.operation == "upload_file"
    logger.info("Upload file test passed")
    return True


# =========================================================================
# Test scroll_to_element
# =========================================================================

async def test_scroll_to_element():
    """Test scroll to element"""
    logger.info("=== Test Scroll to Element ===")
    
    session = create_mock_session()
    interaction = BrowserInteraction(session)
    
    async def mock_eval_js(script):
        if "scrollIntoView" in script:
            return {"success": True}
        return "0"
    
    session.eval_js = AsyncMock(side_effect=mock_eval_js)
    
    result = await interaction.scroll_to_element(
        selector="#target-section",
        behavior="smooth",
    )
    
    assert result is not None
    assert result.success is True
    assert result.operation == "scroll_to_element"
    logger.info("Scroll to element test passed")
    return True


# =========================================================================
# Test fill_and_submit
# =========================================================================

async def test_fill_and_submit():
    """Test fill and submit form"""
    logger.info("=== Test Fill and Submit ===")
    
    session = create_mock_session()
    interaction = BrowserInteraction(session)
    
    async def mock_eval_js(script):
        if "input" in script or "change" in script:
            return True
        if "submit" in script.lower():
            return True
        return "0"
    
    session.eval_js = AsyncMock(side_effect=mock_eval_js)
    session.wait_for_selector = AsyncMock(return_value=True)
    
    result = await interaction.fill_and_submit(
        form_selector="#login-form",
        fields={"username": "test", "password": "123456"},
        wait_for_response=False,
    )
    
    assert result is not None
    assert result.success is True
    assert result.operation == "fill_and_submit"
    logger.info("Fill and submit test passed")
    return True


# =========================================================================
# Test take_screenshot_with_annotation
# =========================================================================

async def test_take_screenshot_with_annotation():
    """Test screenshot with annotation"""
    logger.info("=== Test Take Screenshot with Annotation ===")
    
    session = create_mock_session()
    interaction = BrowserInteraction(session)
    
    async def mock_eval_js(script):
        if "getBoundingClientRect" in script:
            return {
                "success": True,
                "x": 100,
                "y": 200,
                "width": 50,
                "height": 30,
                "annotation": "target element",
            }
        return "0"
    
    session.eval_js = AsyncMock(side_effect=mock_eval_js)
    
    result = await interaction.take_screenshot_with_annotation(
        selector="#annotated-element",
        annotation="target element",
    )
    
    assert result is not None
    assert result.success is True
    assert result.operation == "take_screenshot_with_annotation"
    assert result.data.get("x") == 100
    assert result.data.get("y") == 200
    logger.info("Screenshot with annotation test passed")
    return True


# =========================================================================
# Test execute_javascript
# =========================================================================

async def test_execute_javascript():
    """Test custom JavaScript execution"""
    logger.info("=== Test Execute JavaScript ===")
    
    session = create_mock_session()
    interaction = BrowserInteraction(session)
    
    async def mock_eval_js(script, timeout=10.0):
        if "console.log" in script:
            return "logged"
        return {"result": "success"}
    
    session.eval_js = AsyncMock(side_effect=mock_eval_js)
    
    result = await interaction.execute_javascript(
        js_code="console.log('test');",
        timeout=5.0,
    )
    
    assert result is not None
    assert result.success is True
    assert result.operation == "execute_javascript"
    logger.info("Execute JavaScript test passed")
    return True


# =========================================================================
# Test Convenience Functions
# =========================================================================

async def test_convenience_functions():
    """Test new convenience functions"""
    logger.info("=== Test Convenience Functions ===")
    
    session = create_mock_session()
    
    # Test drag_and_drop function
    async def mock_drag(script):
        return {"success": True}
    session.eval_js = AsyncMock(side_effect=mock_drag)
    result = await drag_and_drop(session, "#from", "#to")
    assert result is not None
    logger.info("drag_and_drop() function test passed")
    
    # Test hover_and_click function
    async def mock_hover(script):
        return {"success": True}
    session.eval_js = AsyncMock(side_effect=mock_hover)
    result = await hover_and_click(session, "#menu")
    assert result is not None
    logger.info("hover_and_click() function test passed")
    
    # Test right_click function
    async def mock_right(script):
        return {"success": True}
    session.eval_js = AsyncMock(side_effect=mock_right)
    result = await right_click(session, "#target")
    assert result is not None
    logger.info("right_click() function test passed")
    
    # Test double_click function
    async def mock_double(script):
        return {"success": True}
    session.eval_js = AsyncMock(side_effect=mock_double)
    result = await double_click(session, "#editable")
    assert result is not None
    logger.info("double_click() function test passed")
    
    # Test select_dropdown function
    async def mock_select(script):
        return {"success": True, "selected": "option1"}
    session.eval_js = AsyncMock(side_effect=mock_select)
    result = await select_dropdown(session, "#select", "option1")
    assert result is not None
    logger.info("select_dropdown() function test passed")
    
    # Test upload_file function
    async def mock_upload(script):
        return {"success": True, "message": "File input triggered"}
    session.eval_js = AsyncMock(side_effect=mock_upload)
    result = await upload_file(session, "#file", "/path/to/file.txt")
    assert result is not None
    logger.info("upload_file() function test passed")
    
    # Test scroll_to_element function
    async def mock_scroll(script):
        return {"success": True}
    session.eval_js = AsyncMock(side_effect=mock_scroll)
    result = await scroll_to_element(session, "#target")
    assert result is not None
    logger.info("scroll_to_element() function test passed")
    
    # Test fill_and_submit function
    async def mock_submit(script):
        return True
    session.eval_js = AsyncMock(side_effect=mock_submit)
    result = await fill_and_submit(session, "#form", {"field": "value"})
    assert result is not None
    logger.info("fill_and_submit() function test passed")
    
    # Test execute_javascript function
    async def mock_js(script, timeout=10.0):
        return {"result": "success"}
    session.eval_js = AsyncMock(side_effect=mock_js)
    result = await execute_javascript(session, "console.log('test')")
    assert result is not None
    logger.info("execute_javascript() function test passed")
    
    return True


# =========================================================================
# Main Test Runner
# =========================================================================

async def run_all_tests():
    """Run all new browser interaction tests"""
    tests = [
        ("Drag and Drop", test_drag_and_drop),
        ("Drag and Drop - Element Not Found", test_drag_and_drop_element_not_found),
        ("Hover and Click", test_hover_and_click),
        ("Right Click", test_right_click),
        ("Double Click", test_double_click),
        ("Select Dropdown", test_select_dropdown),
        ("Select Dropdown - Not Found", test_select_dropdown_not_found),
        ("Upload File", test_upload_file),
        ("Scroll to Element", test_scroll_to_element),
        ("Fill and Submit", test_fill_and_submit),
        ("Take Screenshot with Annotation", test_take_screenshot_with_annotation),
        ("Execute JavaScript", test_execute_javascript),
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
