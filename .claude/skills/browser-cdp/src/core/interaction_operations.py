"""
interaction_operations.py - 交互操作模块（增强版）

提供统一的交互操作接口，整合：
1. 点击操作（单击、双击、右键、智能点击）
2. 输入操作（文本输入、键盘操作、表单填写）
3. 拖拽操作（拖拽元素、拖拽到指定位置）
4. 滚动操作（智能滚动、无限滚动、滚动到元素）
5. 截图操作（可视区域、整页、区域截图）
6. 组合操作（等待+操作、批量操作）

Usage:
    from src.core.interaction_operations import InteractionOperations
    
    ops = InteractionOperations(session)
    
    # 点击操作
    await ops.click("#submit")
    await ops.double_click(".item")
    await ops.smart_click(text="提交")
    
    # 输入操作
    await ops.type_text("#input", "hello world")
    await ops.press_key("Enter")
    await ops.fill_form("#form", {"name": "test"})
    
    # 拖拽操作
    await ops.drag("#drag", "#drop")
    await ops.drag_to("#drag", x=500, y=300)
    
    # 滚动操作
    await ops.scroll_down(800)
    await ops.scroll_to_element("#bottom")
    await ops.infinite_scroll(".item", max_items=100)
    
    # 截图操作
    await ops.screenshot("shot.png")
    await ops.screenshot_full_page("full.png")
    await ops.screenshot_region("main.png", region="main")
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, List, Dict, Any, Callable, Tuple, Union

from src.core.smart_wait import SmartWait, WaitConfig
from src.core.dynamic_page_support import DynamicPageSupport
from src.core.enhanced_dynamic_loader import EnhancedDynamicLoader, ScrollConfig, ScrollResult

logger = logging.getLogger(__name__)


class ClickType(Enum):
    """点击类型"""
    SINGLE = "single"
    DOUBLE = "double"
    RIGHT = "right"
    MIDDLE = "middle"


class ScrollDirection(Enum):
    """滚动方向"""
    DOWN = "down"
    UP = "up"
    LEFT = "left"
    RIGHT = "right"
    TO_ELEMENT = "to_element"
    TO_TOP = "to_top"
    TO_BOTTOM = "to_bottom"


class DragMode(Enum):
    """拖拽模式"""
    TO_ELEMENT = "to_element"
    TO_COORDS = "to_coords"


@dataclass
class ClickResult:
    """点击操作结果"""
    success: bool
    click_type: str
    selector: Optional[str] = None
    x: Optional[float] = None
    y: Optional[float] = None
    error: Optional[str] = None
    elapsed: float = 0.0


@dataclass
class InputResult:
    """输入操作结果"""
    success: bool
    operation: str
    selector: Optional[str] = None
    text: Optional[str] = None
    error: Optional[str] = None
    elapsed: float = 0.0


@dataclass
class ScrollResultData:
    """滚动操作结果"""
    success: bool
    direction: str
    distance: int = 0
    final_position: int = 0
    error: Optional[str] = None
    elapsed: float = 0.0


@dataclass
class DragResult:
    """拖拽操作结果"""
    success: bool
    mode: str
    source: Optional[str] = None
    target: Optional[str] = None
    x: Optional[float] = None
    y: Optional[float] = None
    error: Optional[str] = None
    elapsed: float = 0.0


@dataclass
class ScreenshotResult:
    """截图操作结果"""
    success: bool
    path: str
    format: str = "png"
    size_bytes: int = 0
    error: Optional[str] = None
    elapsed: float = 0.0


class InteractionOperations:
    """
    交互操作类
    
    提供统一的点击、输入、拖拽、滚动、截图操作接口
    """
    
    def __init__(self, session, config: Optional[Dict[str, Any]] = None):
        self.session = session
        self.config = config or {}
        self._smart_wait = SmartWait(session)
        self._dynamic_support = DynamicPageSupport(session)
        self._dynamic_loader = EnhancedDynamicLoader(session)
        self._operation_stats: Dict[str, Dict[str, int]] = {}
    
    def _record_operation(self, operation: str, success: bool, elapsed: float):
        """记录操作统计"""
        if operation not in self._operation_stats:
            self._operation_stats[operation] = {"success": 0, "failure": 0, "total_time": 0.0}
        self._operation_stats[operation]["total_time"] += elapsed
        if success:
            self._operation_stats[operation]["success"] += 1
        else:
            self._operation_stats[operation]["failure"] += 1
    
    # =========================================================================
    # 点击操作
    # =========================================================================
    
    async def click(
        self,
        selector: str,
        click_type: ClickType = ClickType.SINGLE,
        wait_for: Optional[str] = None,
        timeout: float = 10.0,
        retry: int = 3,
    ) -> ClickResult:
        """点击元素"""
        start_time = time.time()
        for attempt in range(retry):
            try:
                if wait_for:
                    await self._smart_wait.wait_for(wait_for, timeout=timeout)
                else:
                    await self._smart_wait.wait_for("selector", selector=selector, timeout=timeout)
                
                js = f"""
                (() => {{
                    const el = document.querySelector({selector!r});
                    if (!el) return {{ success: false, error: 'element not found' }};
                    const rect = el.getBoundingClientRect();
                    const x = rect.left + rect.width / 2 + window.scrollX;
                    const y = rect.top + rect.height / 2 + window.scrollY;
                    const events = ['mousedown', 'mouseup', 'click'];
                    events.forEach(eventName => {{
                        el.dispatchEvent(new MouseEvent(eventName, {{
                            bubbles: true, cancelable: true, view: window,
                            clientX: rect.width / 2, clientY: rect.height / 2,
                        }}));
                    }});
                    return {{ success: true, x: x, y: y }};
                }})()
                """
                result = await self.session.eval_js(js)
                if not result.get("success", False):
                    raise Exception(result.get("error", "click failed"))
                
                elapsed = time.time() - start_time
                self._record_operation("click", True, elapsed)
                logger.info(f"点击成功: {selector} ({click_type.value}), 耗时 {elapsed:.2f}s")
                return ClickResult(
                    success=True, click_type=click_type.value,
                    selector=selector, x=result.get("x"), y=result.get("y"), elapsed=elapsed,
                )
            except Exception as e:
                elapsed = time.time() - start_time
                if attempt < retry - 1:
                    logger.warning(f"点击失败 (尝试 {attempt+1}/{retry}): {e}")
                    await asyncio.sleep(0.5)
                else:
                    logger.error(f"点击失败: {e}")
                    self._record_operation("click", False, elapsed)
                    return ClickResult(success=False, click_type=click_type.value, selector=selector, error=str(e), elapsed=elapsed)
    async def double_click(self, selector: str, wait_for: Optional[str] = None, timeout: float = 10.0) -> ClickResult:
        """双击元素"""
        start_time = time.time()
        js = f"""
        (() => {{
            const el = document.querySelector({selector!r});
            if (!el) return {{ success: false }};
            const rect = el.getBoundingClientRect();
            const x = rect.left + rect.width / 2 + window.scrollX;
            const y = rect.top + rect.height / 2 + window.scrollY;
            el.dispatchEvent(new MouseEvent('mousedown', {{ bubbles: true }}));
            el.dispatchEvent(new MouseEvent('mouseup', {{ bubbles: true }}));
            el.dispatchEvent(new MouseEvent('click', {{ bubbles: true }}));
            el.dispatchEvent(new MouseEvent('mousedown', {{ bubbles: true }}));
            el.dispatchEvent(new MouseEvent('mouseup', {{ bubbles: true }}));
            el.dispatchEvent(new MouseEvent('click', {{ bubbles: true }}));
            el.dispatchEvent(new MouseEvent('dblclick', {{ bubbles: true }}));
            return {{ success: true, x: x, y: y }};
        }})()
        """
        result = await self.session.eval_js(js)
        elapsed = time.time() - start_time
        self._record_operation("double_click", result.get("success", False), elapsed)
        return ClickResult(success=result.get("success", False), click_type="double", selector=selector, x=result.get("x"), y=result.get("y"), elapsed=elapsed)
    
    async def right_click(self, selector: str, wait_for: Optional[str] = None, timeout: float = 10.0) -> ClickResult:
        """右键点击元素"""
        start_time = time.time()
        js = f"""
        (() => {{
            const el = document.querySelector({selector!r});
            if (!el) return {{ success: false }};
            const rect = el.getBoundingClientRect();
            const x = rect.left + rect.width / 2 + window.scrollX;
            const y = rect.top + rect.height / 2 + window.scrollY;
            el.dispatchEvent(new MouseEvent('contextmenu', {{ bubbles: true, button: 2 }}));
            return {{ success: true, x: x, y: y }};
        }})()
        """
        result = await self.session.eval_js(js)
        elapsed = time.time() - start_time
        self._record_operation("right_click", result.get("success", False), elapsed)
        return ClickResult(success=result.get("success", False), click_type="right", selector=selector, x=result.get("x"), y=result.get("y"), elapsed=elapsed)
    
    async def smart_click(self, text: str, tag: str = None, timeout: float = 10.0) -> ClickResult:
        """智能点击：根据文本查找元素并点击"""
        start_time = time.time()
        js = f"""
        (() => {{
            const tags = {tag!r} ? [{tag!r}] : ['button', 'a', 'input', 'span', 'div', 'label'];
            const elements = [];
            for (const tag of tags) {{
                const els = document.querySelectorAll(tag);
                for (const el of els) {{
                    const t = (el.innerText || el.value || '').trim();
                    if (t.includes({text!r})) {{
                        const r = el.getBoundingClientRect();
                        elements.push({{ tag: tag, text: t.slice(0, 50), x: r.left + r.width / 2 + window.scrollX, y: r.top + r.height / 2 + window.scrollY, clickable: el.offsetParent !== null && !el.disabled }});
                    }}
                }}
            }}
            return elements.find(e => e.clickable) || elements[0] || null;
        }})()
        """
        result = await self.session.eval_js(js)
        elapsed = time.time() - start_time
        if not result:
            self._record_operation("smart_click", False, elapsed)
            return ClickResult(success=False, click_type="smart", error=f"未找到包含文本 '{text}' 的可点击元素", elapsed=elapsed)
        click_js = f"""
        (() => {{
            const tags = {tag!r} ? [{tag!r}] : ['button', 'a', 'input', 'span', 'div', 'label'];
            for (const tag of tags) {{
                const els = document.querySelectorAll(tag);
                for (const el of els) {{
                    const t = (el.innerText || el.value || '').trim();
                    if (t.includes({text!r}) && el.offsetParent !== null) {{ el.click(); return {{ success: true }}; }}
                }}
            }}
            return {{ success: false }};
        }})()
        """
        click_result = await self.session.eval_js(click_js)
        self._record_operation("smart_click", click_result.get("success", False), elapsed)
        return ClickResult(success=click_result.get("success", False), click_type="smart", elapsed=elapsed)
    
    # =========================================================================
    # 输入操作
    # =========================================================================
    
    async def type_text(self, selector: str, text: str, clear_first: bool = True, delay: float = 0.05, wait_for: Optional[str] = None, timeout: float = 10.0) -> InputResult:
        """输入文本（逐字符输入，模拟真实用户）"""
        start_time = time.time()
        try:
            if wait_for:
                await self._smart_wait.wait_for(wait_for, timeout=timeout)
            else:
                await self._smart_wait.wait_for("selector", selector=selector, timeout=timeout)
            if clear_first:
                clear_js = f"""
                (() => {{
                    const el = document.querySelector({selector!r});
                    if (!el) return false;
                    el.value = '';
                    el.dispatchEvent(new Event('input', {{ bubbles: true }}));
                    el.dispatchEvent(new Event('change', {{ bubbles: true }}));
                    return true;
                }})()
                """
                await self.session.eval_js(clear_js)
            for ch in text:
                type_js = f"""
                (() => {{
                    const el = document.querySelector({selector!r});
                    if (!el) return false;
                    el.focus();
                    const start = el.selectionStart || el.value.length;
                    el.value = el.value.substring(0, start) + {ch!r} + el.value.substring(el.selectionEnd || el.value.length);
                    el.dispatchEvent(new Event('input', {{ bubbles: true }}));
                    el.dispatchEvent(new Event('change', {{ bubbles: true }}));
                    return true;
                }})()
                """
                await self.session.eval_js(type_js)
                if delay > 0:
                    await asyncio.sleep(delay)
            elapsed = time.time() - start_time
            self._record_operation("type_text", True, elapsed)
            logger.info(f"文本输入成功: {selector}, 长度: {len(text)}, 耗时 {elapsed:.2f}s")
            return InputResult(success=True, operation="type_text", selector=selector, text=text, elapsed=elapsed)
        except Exception as e:
            elapsed = time.time() - start_time
            self._record_operation("type_text", False, elapsed)
            logger.error(f"文本输入失败: {e}")
            return InputResult(success=False, operation="type_text", selector=selector, text=text, error=str(e), elapsed=elapsed)
    
    async def press_key(self, key: str, selector: str = None) -> InputResult:
        """按下键盘按键"""
        start_time = time.time()
        key_codes = {
            "Enter": {"key": "Enter", "code": "Enter", "keyCode": 13},
            "Tab": {"key": "Tab", "code": "Tab", "keyCode": 9},
            "Escape": {"key": "Escape", "code": "Escape", "keyCode": 27},
            "Backspace": {"key": "Backspace", "code": "Backspace", "keyCode": 8},
            "Delete": {"key": "Delete", "code": "Delete", "keyCode": 46},
            "ArrowUp": {"key": "ArrowUp", "code": "ArrowUp", "keyCode": 38},
            "ArrowDown": {"key": "ArrowDown", "code": "ArrowDown", "keyCode": 40},
            "ArrowLeft": {"key": "ArrowLeft", "code": "ArrowLeft", "keyCode": 37},
            "ArrowRight": {"key": "ArrowRight", "code": "ArrowRight", "keyCode": 39},
            "Home": {"key": "Home", "code": "Home", "keyCode": 36},
            "End": {"key": "End", "code": "End", "keyCode": 35},
            "PageUp": {"key": "PageUp", "code": "PageUp", "keyCode": 33},
            "PageDown": {"key": "PageDown", "code": "PageDown", "keyCode": 34},
        }
        key_spec = key_codes.get(key)
        if not key_spec:
            return InputResult(success=False, operation="press_key", error=f"不支持的按键: {key}")
        js = f"""
        (() => {{
            const target = {selector!r} ? document.querySelector({selector!r}) : document.activeElement;
            if (!target) return {{ success: false }};
            target.dispatchEvent(new KeyboardEvent('keydown', {{ key: {key_spec['key']!r}, code: {key_spec['code']!r}, keyCode: {key_spec['keyCode']}, bubbles: true }}));
            target.dispatchEvent(new KeyboardEvent('keyup', {{ key: {key_spec['key']!r}, code: {key_spec['code']!r}, keyCode: {key_spec['keyCode']}, bubbles: true }}));
            return {{ success: true }};
        }})()
        """
        result = await self.session.eval_js(js)
        elapsed = time.time() - start_time
        self._record_operation("press_key", result.get("success", False), elapsed)
        return InputResult(success=result.get("success", False), operation="press_key", selector=selector, elapsed=elapsed)
    
    async def fill_form(self, form_selector: str, fields: Dict[str, Any], submit: bool = False, submit_selector: str = None, wait_for_response: bool = True, timeout: float = 30.0) -> InputResult:
        """填写表单"""
        start_time = time.time()
        try:
            for selector, value in fields.items():
                await self.type_text(selector, str(value), delay=0.02)
            if submit:
                if submit_selector:
                    await self.click(submit_selector)
                else:
                    submit_js = f"""
                    (() => {{
                        const form = document.querySelector({form_selector!r});
                        if (!form) return null;
                        const btn = form.querySelector('button[type="submit"], input[type="submit"]');
                        return btn ? btn.outerHTML.substring(0, 100) : null;
                    }})()
                    """
                    btn = await self.session.eval_js(submit_js)
                    if btn:
                        await self.click(form_selector + ' button[type="submit"], ' + form_selector + ' input[type="submit"]')
                if wait_for_response:
                    await self._smart_wait.wait_for("route", timeout=timeout)
                    await self._dynamic_support.wait_for_page_ready(timeout=timeout)
            elapsed = time.time() - start_time
            self._record_operation("fill_form", True, elapsed)
            return InputResult(success=True, operation="fill_form", selector=form_selector, elapsed=elapsed)
        except Exception as e:
            elapsed = time.time() - start_time
            self._record_operation("fill_form", False, elapsed)
            logger.error(f"表单填写失败: {e}")
            return InputResult(success=False, operation="fill_form", selector=form_selector, error=str(e), elapsed=elapsed)
    # =========================================================================
    # 拖拽操作
    # =========================================================================
    
    async def drag(self, source_selector: str, target_selector: str, wait_for_source: bool = True, wait_for_target: bool = True, timeout: float = 10.0) -> DragResult:
        """拖拽元素到另一个元素"""
        start_time = time.time()
        try:
            if wait_for_source:
                await self._smart_wait.wait_for("selector", selector=source_selector, timeout=timeout)
            if wait_for_target:
                await self._smart_wait.wait_for("selector", selector=target_selector, timeout=timeout)
            js = f"""
            (() => {{
                const source = document.querySelector({source_selector!r});
                const target = document.querySelector({target_selector!r});
                if (!source || !target) return {{ success: false }};
                const sourceRect = source.getBoundingClientRect();
                const targetRect = target.getBoundingClientRect();
                const startX = sourceRect.left + sourceRect.width / 2 + window.scrollX;
                const startY = sourceRect.top + sourceRect.height / 2 + window.scrollY;
                const endX = targetRect.left + targetRect.width / 2 + window.scrollX;
                const endY = targetRect.top + targetRect.height / 2 + window.scrollY;
                const events = [
                    ['mousedown', startX, startY],
                    ['mousemove', (startX + endX) / 2, (startY + endY) / 2],
                    ['mousemove', endX, endY],
                    ['mouseup', endX, endY],
                ];
                for (const [type, x, y] of events) {{
                    const el = type === 'mousedown' ? source : target;
                    el.dispatchEvent(new MouseEvent(type, {{ bubbles: true, clientX: x - window.scrollX, clientY: y - window.scrollY }}));
                }}
                source.dispatchEvent(new DragEvent('dragstart', {{ bubbles: true }}));
                target.dispatchEvent(new DragEvent('dragover', {{ bubbles: true }}));
                target.dispatchEvent(new DragEvent('drop', {{ bubbles: true }}));
                source.dispatchEvent(new DragEvent('dragend', {{ bubbles: true }}));
                return {{ success: true }};
            }})()
            """
            result = await self.session.eval_js(js)
            elapsed = time.time() - start_time
            self._record_operation("drag", result.get("success", False), elapsed)
            return DragResult(success=result.get("success", False), mode="to_element", source=source_selector, target=target_selector, elapsed=elapsed)
        except Exception as e:
            elapsed = time.time() - start_time
            self._record_operation("drag", False, elapsed)
            logger.error(f"拖拽失败: {e}")
            return DragResult(success=False, mode="to_element", source=source_selector, target=target_selector, error=str(e), elapsed=elapsed)
    
    async def drag_to(self, source_selector: str, x: float, y: float, wait_for_source: bool = True, timeout: float = 10.0) -> DragResult:
        """拖拽元素到指定坐标"""
        start_time = time.time()
        try:
            if wait_for_source:
                await self._smart_wait.wait_for("selector", selector=source_selector, timeout=timeout)
            js = f"""
            (() => {{
                const source = document.querySelector({source_selector!r});
                if (!source) return {{ success: false }};
                const rect = source.getBoundingClientRect();
                const startX = rect.left + rect.width / 2 + window.scrollX;
                const startY = rect.top + rect.height / 2 + window.scrollY;
                source.dispatchEvent(new MouseEvent('mousedown', {{ bubbles: true, clientX: startX - window.scrollX, clientY: startY - window.scrollY }}));
                document.dispatchEvent(new MouseEvent('mousemove', {{ bubbles: true, clientX: {x}, clientY: {y} }}));
                document.dispatchEvent(new MouseEvent('mouseup', {{ bubbles: true, clientX: {x}, clientY: {y} }}));
                return {{ success: true }};
            }})()
            """
            result = await self.session.eval_js(js)
            elapsed = time.time() - start_time
            self._record_operation("drag_to", result.get("success", False), elapsed)
            return DragResult(success=result.get("success", False), mode="to_coords", source=source_selector, x=x, y=y, elapsed=elapsed)
        except Exception as e:
            elapsed = time.time() - start_time
            self._record_operation("drag_to", False, elapsed)
            logger.error(f"拖拽失败: {e}")
            return DragResult(success=False, mode="to_coords", source=source_selector, x=x, y=y, error=str(e), elapsed=elapsed)
    
    # =========================================================================
    # 滚动操作
    # =========================================================================
    
    async def scroll(self, direction: ScrollDirection = ScrollDirection.DOWN, distance: int = 800, selector: str = None, wait_after: float = 0.5) -> ScrollResultData:
        """滚动页面或元素"""
        start_time = time.time()
        try:
            if selector:
                js = f"""
                (() => {{
                    const el = document.querySelector({selector!r});
                    if (!el) return {{ success: false }};
                    el.scrollIntoView({{ behavior: 'smooth', block: 'center' }});
                    return {{ success: true, position: el.getBoundingClientRect().top }};
                }})()
                """
            else:
                scroll_amount = distance if direction in (ScrollDirection.DOWN, ScrollDirection.RIGHT) else -distance
                js = f"""
                (() => {{
                    window.scrollBy({{ left: {scroll_amount if direction in (ScrollDirection.RIGHT,) else 0}, top: {scroll_amount if direction in (ScrollDirection.DOWN,) else 0}, behavior: 'smooth' }});
                    return {{ success: true }};
                }})()
                """
            result = await self.session.eval_js(js)
            if wait_after > 0:
                await asyncio.sleep(wait_after)
            elapsed = time.time() - start_time
            self._record_operation("scroll", result.get("success", False), elapsed)
            return ScrollResultData(success=result.get("success", False), direction=direction.value, distance=distance, elapsed=elapsed)
        except Exception as e:
            elapsed = time.time() - start_time
            self._record_operation("scroll", False, elapsed)
            logger.error(f"滚动失败: {e}")
            return ScrollResultData(success=False, direction=direction.value, distance=distance, error=str(e), elapsed=elapsed)
    
    async def scroll_to_element(self, selector: str) -> ScrollResultData:
        """滚动到指定元素"""
        return await self.scroll(direction=ScrollDirection.TO_ELEMENT, selector=selector)
    
    async def scroll_to_top(self) -> ScrollResultData:
        """滚动到页面顶部"""
        return await self.scroll(direction=ScrollDirection.TO_TOP)
    
    async def scroll_to_bottom(self) -> ScrollResultData:
        """滚动到页面底部"""
        return await self.scroll(direction=ScrollDirection.TO_BOTTOM)
    
    async def scroll_down(self, distance: int = 800, wait_after: float = 0.5) -> ScrollResultData:
        """向下滚动"""
        return await self.scroll(direction=ScrollDirection.DOWN, distance=distance, wait_after=wait_after)
    
    async def scroll_up(self, distance: int = 800, wait_after: float = 0.5) -> ScrollResultData:
        """向上滚动"""
        return await self.scroll(direction=ScrollDirection.UP, distance=distance, wait_after=wait_after)
    
    async def infinite_scroll(self, item_selector: str = "", max_items: int = 100, max_pages: int = 10, scroll_distance: int = 800, scroll_delay: float = 0.8) -> ScrollResult:
        """智能无限滚动加载"""
        logger.info(f"开始智能无限滚动，最大项目数: {max_items}")
        config = ScrollConfig(item_selector=item_selector, max_pages=max_pages, scroll_distance=scroll_distance, scroll_delay=scroll_delay)
        result = await self._dynamic_loader.smart_scroll(max_pages=max_pages)
        await self._dynamic_support.wait_for_lazy_images(timeout=5.0)
        logger.info(f"无限滚动完成: {result.pages_loaded} 页, {result.items_found} 个项目")
        return result
    
    # =========================================================================
    # 截图操作
    # =========================================================================
    
    async def screenshot(self, path: str, full_page: bool = False, region: str = None, annotate: bool = False, zoom: float = 1.0, fmt: str = "png", quality: int = 95) -> ScreenshotResult:
        """截图操作"""
        start_time = time.time()
        try:
            # 使用 browser_screenshot 模块的功能
            from src.core.browser_screenshot import capture, annotate_png, smart_region_crop, zoom_screenshot, save_screenshot
            from src.core.utils import scan_interactive_elements
            
            elements = []
            if annotate:
                elements = scan_interactive_elements(self.session)
            
            clip = None
            png_bytes = capture(self.session, full_page=full_page, clip=clip, timeout=60.0)
            
            if region:
                png_bytes = smart_region_crop(png_bytes, region)
            
            if zoom > 1.0:
                png_bytes = zoom_screenshot(png_bytes, zoom)
            
            if annotate and not full_page:
                png_bytes = annotate_png(png_bytes, elements)
            
            save_screenshot(png_bytes, path, fmt=fmt, quality=quality)
            
            elapsed = time.time() - start_time
            size_bytes = len(png_bytes)
            self._record_operation("screenshot", True, elapsed)
            logger.info(f"截图成功: {path}, 大小: {size_bytes} bytes, 耗时 {elapsed:.2f}s")
            return ScreenshotResult(success=True, path=path, format=fmt, size_bytes=size_bytes, elapsed=elapsed)
        except Exception as e:
            elapsed = time.time() - start_time
            self._record_operation("screenshot", False, elapsed)
            logger.error(f"截图失败: {e}")
            return ScreenshotResult(success=False, path=path, error=str(e), elapsed=elapsed)
    
    async def screenshot_full_page(self, path: str, annotate: bool = False) -> ScreenshotResult:
        """整页截图"""
        return await self.screenshot(path, full_page=True, annotate=annotate)
    
    async def screenshot_region(self, path: str, region: str) -> ScreenshotResult:
        """区域截图"""
        return await self.screenshot(path, region=region)
    
    # =========================================================================
    # 组合操作
    # =========================================================================
    
    async def wait_and_click(self, selector: str, wait_for: str = "networkidle", timeout: float = 10.0, **kwargs) -> ClickResult:
        """等待条件后点击"""
        await self._smart_wait.wait_for(wait_for, timeout=timeout)
        return await self.click(selector, **kwargs)
    
    async def wait_and_type(self, selector: str, text: str, wait_for: str = "networkidle", timeout: float = 10.0, **kwargs) -> InputResult:
        """等待条件后输入"""
        await self._smart_wait.wait_for(wait_for, timeout=timeout)
        return await self.type_text(selector, text, **kwargs)
    
    async def batch_operations(self, operations: List[Dict[str, Any]], pause_between: float = 0.5) -> List[Dict[str, Any]]:
        """批量执行操作"""
        results = []
        for op in operations:
            op_type = op.get("type")
            if op_type == "click":
                result = await self.click(op.get("selector"), **{k: v for k, v in op.items() if k != "type"})
                results.append({"operation": "click", "result": result.to_dict() if hasattr(result, 'to_dict') else result})
            elif op_type == "type":
                result = await self.type_text(op.get("selector"), op.get("text"), **{k: v for k, v in op.items() if k != "type"})
                results.append({"operation": "type", "result": result.to_dict() if hasattr(result, 'to_dict') else result})
            elif op_type == "scroll":
                result = await self.scroll(ScrollDirection[op.get("direction", "DOWN").upper()], op.get("distance", 800))
                results.append({"operation": "scroll", "result": result.__dict__})
            elif op_type == "screenshot":
                result = await self.screenshot(op.get("path"), **{k: v for k, v in op.items() if k != "type"})
                results.append({"operation": "screenshot", "result": result.to_dict() if hasattr(result, 'to_dict') else result})
            
            if pause_between > 0:
                await asyncio.sleep(pause_between)
        return results
    
    def get_stats(self) -> Dict[str, Any]:
        """获取操作统计"""
        stats = {}
        for op, data in self._operation_stats.items():
            total = data["success"] + data["failure"]
            stats[op] = {
                "total": total,
                "success": data["success"],
                "failure": data["failure"],
                "success_rate": data["success"] / total * 100 if total > 0 else 0,
                "avg_time": data["total_time"] / total if total > 0 else 0,
            }
        return stats


# 便捷函数
async def click_element(session, selector: str, **kwargs) -> ClickResult:
    """便捷点击函数"""
    ops = InteractionOperations(session)
    return await ops.click(selector, **kwargs)


async def type_text_to_element(session, selector: str, text: str, **kwargs) -> InputResult:
    """便捷输入函数"""
    ops = InteractionOperations(session)
    return await ops.type_text(selector, text, **kwargs)


async def scroll_page(session, direction: str = "down", distance: int = 800) -> ScrollResultData:
    """便捷滚动函数"""
    ops = InteractionOperations(session)
    return await ops.scroll(ScrollDirection[direction.upper()], distance)


async def take_screenshot(session, path: str, **kwargs) -> ScreenshotResult:
    """便捷截图函数"""
    ops = InteractionOperations(session)
    return await ops.screenshot(path, **kwargs)
