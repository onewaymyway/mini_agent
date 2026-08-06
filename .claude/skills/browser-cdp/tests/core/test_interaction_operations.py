"""
测试交互操作模块
"""
import asyncio
import sys
import logging
from pathlib import Path

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

SKILL_DIR = Path(__file__).parent.parent.parent
sys.path.insert(0, str(SKILL_DIR / "src"))

from src.core.interaction_operations import (
    InteractionOperations,
    ClickType,
    ScrollDirection,
    ClickResult,
    InputResult,
    ScrollResultData,
    DragResult,
    ScreenshotResult,
)


class MockSession:
    """模拟 CDP Session"""
    async def eval_js(self, js_code: str):
        if "querySelector" in js_code and "getBoundingClientRect" in js_code:
            return {"success": True, "x": 100.0, "y": 200.0}
        if "scrollIntoView" in js_code:
            return {"success": True, "position": 500}
        if "scrollBy" in js_code:
            return {"success": True}
        if "click()" in js_code or "dispatchEvent" in js_code:
            return {"success": True}
        if "selectionStart" in js_code:
            return {"success": True}
        if "KeyboardEvent" in js_code:
            return {"success": True}
        return {"success": True}

    async def query_selector(self, selector):
        return None

    async def query_selector_all(self, selector):
        return []


async def test_click_operations():
    """测试点击操作"""
    logger.info("=== 测试点击操作 ===")
    session = MockSession()
    ops = InteractionOperations(session)

    # 单击
    result = await ops.click("#btn")
    assert result.success is True
    assert result.click_type == "single"
    assert result.selector == "#btn"
    logger.info(f"单击: success={result.success}, type={result.click_type}")

    # 双击
    result = await ops.double_click("#btn")
    assert result.success is True
    assert result.click_type == "double"
    logger.info(f"双击: success={result.success}")

    # 右键
    result = await ops.right_click("#btn")
    assert result.success is True
    assert result.click_type == "right"
    logger.info(f"右键: success={result.success}")

    # 智能点击
    result = await ops.smart_click(text="提交")
    assert result.success is True
    assert result.click_type == "smart"
    logger.info(f"智能点击: success={result.success}")

    # 带重试的点击
    result = await ops.click("#btn", retry=3)
    assert result.success is True
    logger.info(f"带重试点击: success={result.success}")


async def test_input_operations():
    """测试输入操作"""
    logger.info("=== 测试输入操作 ===")
    session = MockSession()
    ops = InteractionOperations(session)

    # 文本输入
    result = await ops.type_text("#input", "hello world")
    assert result.success is True
    assert result.text == "hello world"
    assert result.selector == "#input"
    logger.info(f"文本输入: success={result.success}, text={result.text}")

    # 按键
    result = await ops.press_key("Enter")
    assert result.success is True
    logger.info(f"按键: success={result.success}")

    # 不支持的按键
    result = await ops.press_key("F123")
    assert result.success is False
    assert "不支持" in result.error
    logger.info(f"不支持的按键: success={result.success}, error={result.error}")

    # 表单填写
    result = await ops.fill_form("#form", {"name": "test", "email": "test@example.com"})
    assert result.success is True
    logger.info(f"表单填写: success={result.success}")


async def test_scroll_operations():
    """测试滚动操作"""
    logger.info("=== 测试滚动操作 ===")
    session = MockSession()
    ops = InteractionOperations(session)

    # 向下滚动
    result = await ops.scroll_down(800)
    assert result.success is True
    assert result.direction == "down"
    assert result.distance == 800
    logger.info(f"向下滚动: success={result.success}, distance={result.distance}")

    # 向上滚动
    result = await ops.scroll_up(800)
    assert result.success is True
    logger.info(f"向上滚动: success={result.success}")

    # 滚动到元素
    result = await ops.scroll_to_element("#bottom")
    assert result.success is True
    logger.info(f"滚动到元素: success={result.success}")

    # 滚动到顶部
    result = await ops.scroll_to_top()
    assert result.success is True
    logger.info(f"滚动到顶部: success={result.success}")

    # 滚动到底部
    result = await ops.scroll_to_bottom()
    assert result.success is True
    logger.info(f"滚动到底部: success={result.success}")

    # 通用滚动
    result = await ops.scroll(ScrollDirection.RIGHT, distance=500)
    assert result.success is True
    assert result.direction == "right"
    logger.info(f"通用滚动: success={result.success}, direction={result.direction}")


async def test_drag_operations():
    """测试拖拽操作"""
    logger.info("=== 测试拖拽操作 ===")
    session = MockSession()
    ops = InteractionOperations(session)

    # 拖拽到元素
    result = await ops.drag("#drag", "#drop")
    assert result.success is True
    assert result.mode == "to_element"
    assert result.source == "#drag"
    assert result.target == "#drop"
    logger.info(f"拖拽到元素: success={result.success}")

    # 拖拽到坐标
    result = await ops.drag_to("#drag", x=500, y=300)
    assert result.success is True
    assert result.mode == "to_coords"
    assert result.x == 500
    assert result.y == 300
    logger.info(f"拖拽到坐标: success={result.success}, x={result.x}, y={result.y}")


async def test_screenshot_operations():
    """测试截图操作（MockSession 不支持，预期失败）"""
    logger.info("=== 测试截图操作 ===")
    session = MockSession()
    ops = InteractionOperations(session)

    # 截图需要真实 CDP Session，MockSession 缺少 send 方法
    result = await ops.screenshot("test_shot.png")
    assert result.success is False
    assert "send" in result.error
    logger.info(f"截图: success={result.success}, error={result.error}")


async def test_stats():
    """测试统计"""
    logger.info("=== 测试统计 ===")
    session = MockSession()
    ops = InteractionOperations(session)

    # 执行一些操作
    await ops.click("#btn")
    await ops.type_text("#input", "test")
    await ops.scroll_down(800)

    stats = ops.get_stats()
    assert "click" in stats
    assert "type_text" in stats
    assert "scroll" in stats
    assert stats["click"]["success"] == 1
    assert stats["click"]["total"] == 1
    assert stats["click"]["success_rate"] == 100.0
    logger.info(f"操作统计: {stats}")


async def test_batch_operations():
    """测试批量操作"""
    logger.info("=== 测试批量操作 ===")
    session = MockSession()
    ops = InteractionOperations(session)

    operations = [
        {"type": "click", "selector": "#btn1"},
        {"type": "type", "selector": "#input", "text": "hello"},
        {"type": "scroll", "direction": "DOWN", "distance": 800},
    ]

    results = await ops.batch_operations(operations, pause_between=0.01)
    assert len(results) == 3
    assert results[0]["operation"] == "click"
    assert results[1]["operation"] == "type"
    assert results[2]["operation"] == "scroll"
    logger.info(f"批量操作: {len(results)} 个操作完成")


async def test_wait_and_click():
    """测试等待后点击"""
    logger.info("=== 测试等待后点击 ===")
    session = MockSession()
    ops = InteractionOperations(session)

    result = await ops.wait_and_click("#btn", wait_for="networkidle")
    assert result.success is True
    logger.info(f"等待后点击: success={result.success}")


async def test_wait_and_type():
    """测试等待后输入"""
    logger.info("=== 测试等待后输入 ===")
    session = MockSession()
    ops = InteractionOperations(session)

    result = await ops.wait_and_type("#input", "hello", wait_for="networkidle")
    assert result.success is True
    logger.info(f"等待后输入: success={result.success}")


async def main():
    logger.info("开始测试交互操作模块")
    try:
        await test_click_operations()
        await test_input_operations()
        await test_scroll_operations()
        await test_drag_operations()
        await test_screenshot_operations()
        await test_stats()
        await test_batch_operations()
        await test_wait_and_click()
        await test_wait_and_type()
        logger.info("所有测试完成")
    except Exception as e:
        logger.error(f"测试失败: {e}", exc_info=True)
        raise


if __name__ == "__main__":
    asyncio.run(main())
