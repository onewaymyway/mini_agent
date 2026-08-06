"""
browser_browse.py - 浏览器浏览功能（统一入口）

整合页面导航、元素交互、截图、等待策略，提供统一的浏览操作接口。
支持错误处理、自动重试、稳定输出格式。

用法：
  # 导航到页面
  python browser_browse.py --tab <id> --goto "https://example.com" --wait-for networkidle
  
  # 截图
  python browser_browse.py --tab <id> --screenshot --out shot.png
  python browser_browse.py --tab <id> --screenshot --out shot.png --annotate
  python browser_browse.py --tab <id> --screenshot --out shot.png --full-page
  
  # 元素交互
  python browser_browse.py --tab <id> --click-index 3
  python browser_browse.py --tab <id> --type-index 5 --text "hello"
  python browser_browse.py --tab <id> --scroll-to-index 8
  
  # 等待元素
  python browser_browse.py --tab <id> --wait-selector "#result" --timeout 10
  
  # 组合操作
  python browser_browse.py --tab <id> --goto "https://example.com" --screenshot --annotate --out shot.png
"""
from __future__ import annotations

import argparse
import json
import os
import time
import logging
import traceback
from typing import List, Dict, Optional, Any, Callable, TypeVar
from dataclasses import dataclass, field
from enum import Enum
import functools

from src.core.utils import (
    add_connection_args,
    get_session,
    scan_interactive_elements,
    element_center,
    scroll_index_into_view,
    print_json,
    die,
)
from src.core.smart_wait import SmartWait
from src.core.browser_nav import cmd_goto, current_state
from src.core.browser_screenshot import capture, annotate_png, save_screenshot
from src.core.browser_input import (
    mouse_click,
    mouse_right_click,
    dispatch_key,
    type_text,
    find_element_by_index,
    find_element_by_text,
    drag_elements,
    batch_click,
)
from src.reliability.middleware import (
    get_middleware,
    OperationType,
    with_error_handling,
    with_error_handling_async,
)
from src.reliability.error import (
    ReliabilityError,
    ErrorCategory,
    CDPConnectionLostError,
    NavigationTimeoutError,
    ElementNotFoundError,
)

logger = logging.getLogger(__name__)


# ============================================================================
# 错误类型定义
# ============================================================================

class BrowserErrorType(Enum):
    """浏览器操作错误类型枚举"""
    TIMEOUT = "timeout"
    CONNECTION_LOST = "connection_lost"
    ELEMENT_NOT_FOUND = "element_not_found"
    SELECTOR_NOT_FOUND = "selector_not_found"
    NAVIGATION_FAILED = "navigation_failed"
    SCREENSHOT_FAILED = "screenshot_failed"
    PAGE_CRASHED = "page_crashed"
    UNKNOWN = "unknown"


@dataclass
class BrowserError(Exception):
    """浏览器操作错误"""
    error_type: BrowserErrorType
    message: str
    operation: str = ""
    attempt: int = 0
    max_attempts: int = 3
    details: Dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        return f"{self.error_type.value}: {self.message} ({self.operation}, {self.attempt}/{self.max_attempts})"

    def to_dict(self) -> dict:
        return {
            "error_type": self.error_type.value,
            "message": self.message,
            "operation": self.operation,
            "attempt": self.attempt,
            "max_attempts": self.max_attempts,
            "details": self.details,
        }


def classify_error(error: Exception) -> BrowserErrorType:
    """根据异常类型推断错误类型"""
    if isinstance(error, (NavigationTimeoutError, ElementNotFoundError)):
        return BrowserErrorType.TIMEOUT if "timeout" in str(error).lower() else BrowserErrorType.ELEMENT_NOT_FOUND
    if isinstance(error, ReliabilityError):
        if error.category == ErrorCategory.CONNECTION:
            return BrowserErrorType.CONNECTION_LOST
        if error.category == ErrorCategory.TIMEOUT:
            return BrowserErrorType.TIMEOUT
        if error.category == ErrorCategory.ELEMENT:
            return BrowserErrorType.ELEMENT_NOT_FOUND
    msg = str(error).lower()
    if "screenshot" in msg:
        return BrowserErrorType.SCREENSHOT_FAILED
    if "timeout" in msg:
        return BrowserErrorType.TIMEOUT
    if "connection" in msg or "lost" in msg or "disconnect" in msg:
        return BrowserErrorType.CONNECTION_LOST
    if "selector" in msg or "not found" in msg:
        return BrowserErrorType.ELEMENT_NOT_FOUND
    if "navigation" in msg or "failed" in msg:
        return BrowserErrorType.NAVIGATION_FAILED
    if "crash" in msg:
        return BrowserErrorType.PAGE_CRASHED
    return BrowserErrorType.UNKNOWN


def retry_operation(func: Callable) -> Callable:
    """重试操作装饰器"""
    @functools.wraps(func)
    def wrapper(*args, retry_config=None, **kwargs):
        from src.reliability.retry import RetryConfig, retry_operation as reliability_retry
        config = retry_config or RetryConfig()
        last_error = None
        for attempt in range(config.max_retries + 1):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                last_error = e
                error_type = classify_error(e)
                if attempt < config.max_retries:
                    import time
                    delay = min(config.base_delay * (2 ** (attempt - 1)), config.max_delay)
                    time.sleep(delay)
        raise BrowserError(
            error_type=classify_error(last_error),
            message=str(last_error),
            operation=func.__name__,
            attempt=config.max_retries,
            max_attempts=config.max_retries,
        ) from last_error
    return wrapper


# ============================================================================
# 结果格式化
# ============================================================================

@dataclass
class BrowseResult:
    """浏览操作结果"""
    success: bool
    operation: str
    data: Dict[str, Any] = field(default_factory=dict)
    error: Optional[Exception] = None
    elapsed: float = 0.0
    
    def to_dict(self) -> dict:
        result = {
            "success": self.success,
            "operation": self.operation,
            "elapsed": round(self.elapsed, 2),
        }
        if self.error:
            result["error"] = {
                "type": type(self.error).__name__,
                "message": str(self.error),
            }
        if self.data:
            result["data"] = self.data
        return result
    
    def __str__(self) -> str:
        if self.success:
            return f"[ok] {self.operation} 完成 ({self.elapsed:.2f}s)"
        else:
            return f"[error] {self.operation} 失败: {self.error}"


def format_result(operation: str, success: bool, data: dict = None, error: Exception = None, elapsed: float = 0.0) -> BrowseResult:
    """格式化操作结果"""
    return BrowseResult(
        success=success,
        operation=operation,
        data=data or {},
        error=error,
        elapsed=elapsed,
    )


# ============================================================================
# 核心操作函数
# ============================================================================

@with_error_handling("screenshot", OperationType.SCREENSHOT, max_retries=2)
def cmd_screenshot(
    session,
    out: str,
    full_page: bool = False,
    annotate: bool = False,
    element_index: int = None,
    region: str = None,
    detail: bool = False,
    no_scroll: bool = False,
    fmt: str = "png",
    quality: int = 95,
    zoom: float = 1.0,
    timeout: float = 60.0,
) -> dict:
    """执行截图操作"""
    elements = []
    if annotate or element_index is not None:
        elements = scan_interactive_elements(session)

    clip = None
    if element_index is not None:
        target = next((e for e in elements if e["index"] == element_index), None)
        if not target:
            raise ElementNotFoundError(selector=f"index={element_index}")
        r = target["rect"]
        clip = {"x": r["x"], "y": r["y"], "width": r["width"], "height": r["height"], "scale": 1}

    png_bytes = capture(session, full_page=full_page, clip=clip, timeout=timeout)

    # 智能区域裁剪
    if region:
        from src.core.browser_screenshot import smart_region_crop
        png_bytes = smart_region_crop(png_bytes, region)

    # 缩放
    if zoom > 1.0:
        from src.core.browser_screenshot import zoom_screenshot
        png_bytes = zoom_screenshot(png_bytes, zoom)

    # 标注
    if annotate and clip is None:
        png_bytes = annotate_png(png_bytes, elements, detail=detail, no_scroll=no_scroll)

    # 保存
    save_screenshot(png_bytes, out, fmt=fmt, quality=quality)
    
    result = {"screenshot": out, "success": True}
    if annotate:
        side_path = os.path.splitext(out)[0] + ".elements.json"
        with open(side_path, "w", encoding="utf-8") as f:
            json.dump(elements, f, ensure_ascii=False, indent=2)
        result["elements_file"] = side_path
        result["element_count"] = len(elements)
    
    print(f"[ok] 截图已保存: {out}")
    return result


@with_error_handling("click", OperationType.CLICK, max_retries=3)
def cmd_click(session, index: int = None, xy: tuple = None, selector: str = None, 
              text: str = None, double: bool = False, right: bool = False) -> dict:
    """执行点击操作"""
    if index is not None:
        el = find_element_by_index(session, index)
        x, y = element_center(el)
        mouse_click(session, x, y, click_count=2 if double else 1)
        print(f"[ok] 已点击 #{index}: <{el['tag']}> {el['text'][:40]!r}")
        return {"index": index, "tag": el['tag'], "text": el['text'][:40]}
    
    if xy:
        x, y = xy
        mouse_click(session, x, y)
        print(f"[ok] 已点击坐标 ({x}, {y})")
        return {"x": x, "y": y}
    
    if selector:
        js = f"""(() => {{
            const el = document.querySelector({selector!r});
            if (!el) return null;
            const r = el.getBoundingClientRect();
            return {{x: r.x + r.width/2 + window.scrollX, y: r.y + r.height/2 + window.scrollY}};
        }})()"""
        pos = session.eval_js(js)
        if not pos:
            raise ElementNotFoundError(selector=selector)
        mouse_click(session, pos["x"], pos["y"])
        print(f"[ok] 已点击 {selector}")
        return {"selector": selector}
    
    if text:
        el = find_element_by_text(session, text)
        if el:
            mouse_click(session, el["x"], el["y"])
            print(f"[ok] 已智能点击: {el['text']!r}")
            return {"text": text, "found": True}
        print(f"[warn] 未找到包含文本 '{text}' 的元素")
        return {"text": text, "found": False}
    
    raise ElementNotFoundError(selector="any")


@with_error_handling("type", OperationType.INPUT, max_retries=3)
def cmd_type(session, index: int = None, selector: str = None, text: str = None, 
             clear_first: bool = False) -> dict:
    """执行输入操作"""
    if text is None:
        raise ElementNotFoundError(selector="text")
    
    if index is not None:
        el = find_element_by_index(session, index)
        x, y = element_center(el)
        mouse_click(session, x, y)
    elif selector:
        js = f"""(() => {{
            const el = document.querySelector({selector!r});
            if (!el) return false;
            el.focus();
            return true;
        }})()"""
        if not session.eval_js(js):
            raise ElementNotFoundError(selector=selector)
    else:
        raise ElementNotFoundError(selector="any")
    
    if clear_first:
        session.send("Input.dispatchKeyEvent", {"type": "keyDown", "key": "a", "modifiers": 2})
        session.send("Input.dispatchKeyEvent", {"type": "keyUp", "key": "a", "modifiers": 2})
        dispatch_key(session, "Backspace")
    
    type_text(session, text)
    print(f"[ok] 已输入文本: {text!r}")
    return {"text": text}


@with_error_handling("scroll", OperationType.SCROLL, max_retries=3)
def cmd_scroll(session, index: int = None, by: tuple = None, to_top: bool = False, 
               to_bottom: bool = False) -> dict:
    """执行滚动操作"""
    if index is not None:
        rect = scroll_index_into_view(session, index)
        if not rect:
            raise ElementNotFoundError(selector=f"index={index}")
        print(f"[ok] 已滚动到 #{index}，新位置: {rect}")
        return {"index": index, "rect": rect}
    
    if by:
        dx, dy = by
        session.send(
            "Input.dispatchMouseEvent",
            {"type": "mouseWheel", "x": 400, "y": 300, "deltaX": dx, "deltaY": dy},
        )
        print(f"[ok] 已滚动 ({dx}, {dy})")
        return {"dx": dx, "dy": dy}
    
    if to_top:
        session.eval_js("window.scrollTo(0, 0)")
        print("[ok] 已滚动到顶部")
        return {"direction": "top"}
    
    if to_bottom:
        session.eval_js("window.scrollTo(0, document.body.scrollHeight)")
        print("[ok] 已滚动到底部")
        return {"direction": "bottom"}
    
    raise BrowserError(
        error_type=BrowserErrorType.UNKNOWN,
        message="滚动操作需要指定 --scroll-to-index / --scroll-by / --scroll-to-top / --scroll-to-bottom 之一",
        operation="scroll"
    )


@with_error_handling("wait", OperationType.WAIT, max_retries=3)
def cmd_wait(session, selector: str = None, timeout: float = 10.0, 
             strategy: str = None) -> dict:
    """执行等待操作"""
    if selector:
        deadline = time.time() + timeout
        js = f"!!document.querySelector({selector!r})"
        while time.time() < deadline:
            try:
                if session.eval_js(js):
                    print(f"[ok] 元素已出现: {selector}")
                    return {"selector": selector, "success": True}
            except Exception:
                pass
            time.sleep(0.3)
        raise BrowserError(
            error_type=BrowserErrorType.TIMEOUT,
            message=f"等待元素超时: {selector}",
            operation="wait",
            details={"selector": selector, "timeout": timeout}
        )
    
    if strategy:
        smart_wait = SmartWait(session)
        import asyncio
        result = asyncio.run(smart_wait.wait_for(strategy, timeout=timeout))
        print(f"[ok] 等待完成: strategy={result.strategy}, elapsed={result.elapsed:.2f}s")
        return {"strategy": result.strategy, "elapsed": result.elapsed, "success": result.success}
    
    raise BrowserError(
        error_type=BrowserErrorType.UNKNOWN,
        message="等待操作需要指定 --wait-selector 或 --wait-for",
        operation="wait"
    )


@with_error_handling("hover", OperationType.CLICK, max_retries=3)
def cmd_hover(session, index: int) -> dict:
    """执行悬停操作"""
    el = find_element_by_index(session, index)
    x, y = element_center(el)
    session.send("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x, "y": y})
    print(f"[ok] 已悬停到 #{index}")
    return {"index": index}


@with_error_handling("drag", OperationType.CLICK, max_retries=3)
def cmd_drag(session, from_index: int, to_index: int) -> dict:
    """执行拖拽操作"""
    from_el = find_element_by_index(session, from_index)
    to_el = find_element_by_index(session, to_index)
    from_x, from_y = element_center(from_el)
    to_x, to_y = element_center(to_el)
    drag_elements(session, from_x, from_y, to_x, to_y)
    print(f"[ok] 已从 #{from_index} 拖拽到 #{to_index}")
    return {"from": from_index, "to": to_index}


@with_error_handling("keys", OperationType.INPUT, max_retries=3)
def cmd_keys(session, key: str) -> dict:
    """执行按键操作"""
    dispatch_key(session, key)
    print(f"[ok] 已按键: {key}")
    return {"key": key}


# ============================================================================
# 主函数
# ============================================================================

def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    add_connection_args(parser)
    
    # 导航操作
    nav_group = parser.add_argument_group('导航操作')
    nav_group.add_argument("--goto", metavar="URL", default=None, help="导航到指定 URL")
    nav_group.add_argument("--back", action="store_true", help="后退")
    nav_group.add_argument("--forward", action="store_true", help="前进")
    nav_group.add_argument("--reload", action="store_true", help="刷新页面")
    nav_group.add_argument("--no-wait-load", action="store_true", help="goto 后不等待 load 事件")
    nav_group.add_argument("--timeout", type=float, default=15.0, help="超时时间（秒）")
    nav_group.add_argument("--wait-for", default=None, 
                          choices=["load", "networkidle", "route", "stable", "ajax", "selector"],
                          help="智能等待策略")
    nav_group.add_argument("--smart-wait", action="store_true", help="使用智能等待策略")
    nav_group.add_argument("--stealth", action="store_true", help="启用反检测模式")
    nav_group.add_argument("--detect-spa", action="store_true", help="检测 SPA 路由")
    
    # 截图操作
    screenshot_group = parser.add_argument_group('截图操作')
    screenshot_group.add_argument("--screenshot", action="store_true", help="截图")
    screenshot_group.add_argument("--out", default=None, help="截图输出路径")
    screenshot_group.add_argument("--full-page", action="store_true", help="整页截图")
    screenshot_group.add_argument("--annotate", action="store_true", help="标注可交互元素")
    screenshot_group.add_argument("--element-index", type=int, default=None, help="只截取指定编号元素")
    screenshot_group.add_argument("--region", choices=["nav", "main", "sidebar", "content", "footer"], 
                                  default=None, help="智能区域截图")
    screenshot_group.add_argument("--detail", action="store_true", help="详细标注（含元素类型）")
    screenshot_group.add_argument("--no-scroll", action="store_true", help="不标注滚动外元素")
    screenshot_group.add_argument("--format", choices=["png", "jpeg"], default="png", help="输出格式")
    screenshot_group.add_argument("--quality", type=int, default=95, help="JPEG 质量")
    screenshot_group.add_argument("--zoom", type=float, default=1.0, help="缩放倍数")
    
    # 交互操作
    interact_group = parser.add_argument_group('交互操作')
    interact_group.add_argument("--click-index", type=int, default=None, help="点击指定编号元素")
    interact_group.add_argument("--click-xy", nargs=2, type=float, metavar=("X", "Y"), default=None, help="点击坐标")
    interact_group.add_argument("--click-selector", default=None, help="点击选择器")
    interact_group.add_argument("--smart-click", default=None, help="根据文本智能点击")
    interact_group.add_argument("--double-click-index", type=int, default=None, help="双击元素")
    interact_group.add_argument("--right-click-index", type=int, default=None, help="右键点击元素")
    interact_group.add_argument("--batch-click", default=None, help="批量点击，用逗号分隔选择器")
    interact_group.add_argument("--batch-delay", type=float, default=0.5, help="批量操作间隔")
    
    # 输入操作
    input_group = parser.add_argument_group('输入操作')
    input_group.add_argument("--type-index", type=int, default=None, help="向指定编号元素输入")
    input_group.add_argument("--type-selector", default=None, help="向选择器输入")
    input_group.add_argument("--text", default=None, help="输入文本")
    input_group.add_argument("--clear-first", action="store_true", help="输入前先清空")
    
    # 按键操作
    input_group.add_argument("--key", default=None, 
                            choices=["Enter", "Tab", "Escape", "Backspace", "ArrowDown", "ArrowUp", 
                                   "ArrowLeft", "ArrowRight", "Home", "End", "PageUp", "PageDown", "Delete"],
                            help="按键")
    
    # 悬停操作
    interact_group.add_argument("--hover-index", type=int, default=None, help="悬停到元素")
    
    # 滚动操作
    interact_group.add_argument("--scroll-to-index", type=int, default=None, help="滚动到元素")
    interact_group.add_argument("--scroll-by", nargs=2, type=int, metavar=("DX", "DY"), default=None, help="滚动偏移")
    interact_group.add_argument("--scroll-to-top", action="store_true", help="滚动到顶部")
    interact_group.add_argument("--scroll-to-bottom", action="store_true", help="滚动到底部")
    
    # 拖拽操作
    interact_group.add_argument("--drag-from-index", type=int, default=None, help="拖拽起点编号")
    interact_group.add_argument("--drag-to-index", type=int, default=None, help="拖拽终点编号")
    
    # 等待操作
    wait_group = parser.add_argument_group('等待操作')
    wait_group.add_argument("--wait-selector", default=None, help="等待元素出现")
    wait_group.add_argument("--wait-for", dest="wait_strategy", default=None,
                           choices=["load", "networkidle", "route", "stable", "ajax", "selector"],
                           help="等待策略")
    wait_group.add_argument("--wait-page-ready", action="store_true", help="等待页面完全就绪（网络空闲+元素+内容稳定）")
    wait_group.add_argument("--wait-page-selector", default=None, help="等待页面就绪的关键选择器")
    
    # 动态页面操作
    dynamic_group = parser.add_argument_group('动态页面操作')
    dynamic_group.add_argument("--scroll-load", action="store_true", help="滚动加载内容（无限滚动）")
    dynamic_group.add_argument("--scroll-item-selector", default="", help="滚动加载的列表项选择器")
    dynamic_group.add_argument("--scroll-max-pages", type=int, default=10, help="最大滚动页数")
    dynamic_group.add_argument("--scroll-max-items", type=int, default=100, help="最大收集项数")
    dynamic_group.add_argument("--wait-lazy", action="store_true", help="等待懒加载图片完成")
    dynamic_group.add_argument("--wait-lazy-selector", default="img[loading='lazy'], [data-src], [data-lazy]", help="懒加载图片选择器")
    dynamic_group.add_argument("--wait-spa-route", action="store_true", help="等待 SPA 路由稳定")
    dynamic_group.add_argument("--wait-spa-url", default=None, help="SPA 路由期望的 URL 模式")
    
    # 状态查询
    state_group = parser.add_argument_group('状态查询')
    state_group.add_argument("--state", action="store_true", help="查询当前页面状态")
    state_group.add_argument("--elements", action="store_true", help="扫描可交互元素")
    
    # 重试配置
    retry_group = parser.add_argument_group('重试配置')
    retry_group.add_argument("--max-retries", type=int, default=3, help="最大重试次数")
    retry_group.add_argument("--retry-delay", type=float, default=1.0, help="重试基础延迟（秒）")
    
    args = parser.parse_args()
    session = get_session(args)
    
    # 设置重试配置
    retry_config = RetryConfig(
        max_retries=args.max_retries,
        base_delay=args.retry_delay,
        max_delay=args.retry_delay * 8
    )
    
    try:
        # 导航操作
        if args.goto:
            cmd_goto(
                session,
                args.goto,
                wait_load=not args.no_wait_load,
                timeout=args.timeout,
                wait_for=args.wait_for,
                enable_stealth=args.stealth,
                detect_spa=args.detect_spa,
                smart_wait=args.smart_wait,
                tab_id=getattr(args, 'tab_id', None)
            )
        
        if args.back:
            session.eval_js("history.back()")
        if args.forward:
            session.eval_js("history.forward()")
        if args.reload:
            session.send("Page.reload")
            if not args.no_wait_load:
                try:
                    session.wait_event("Page.loadEventFired", timeout=args.timeout)
                except Exception:
                    pass
        
        # 截图操作
        if args.screenshot:
            if not args.out:
                args.out = f"screenshot_{int(time.time())}.png"
            cmd_screenshot(
                session,
                args.out,
                full_page=args.full_page,
                annotate=args.annotate,
                element_index=args.element_index,
                region=args.region,
                detail=args.detail,
                no_scroll=args.no_scroll,
                fmt=args.format,
                quality=args.quality,
                zoom=args.zoom,
                timeout=args.timeout,
                retry_config=retry_config,
                operation="screenshot"
            )
        
        # 交互操作
        if args.click_index is not None:
            cmd_click(session, index=args.click_index, retry_config=retry_config, operation="click")
        if args.click_xy:
            cmd_click(session, xy=tuple(args.click_xy), retry_config=retry_config, operation="click")
        if args.click_selector:
            cmd_click(session, selector=args.click_selector, retry_config=retry_config, operation="click")
        if args.smart_click:
            cmd_click(session, text=args.smart_click, retry_config=retry_config, operation="click")
        if args.double_click_index is not None:
            cmd_click(session, index=args.double_click_index, double=True, retry_config=retry_config, operation="click")
        if args.right_click_index is not None:
            el = find_element_by_index(session, args.right_click_index)
            x, y = element_center(el)
            mouse_right_click(session, x, y)
            print(f"[ok] 已右键点击 #{args.right_click_index}")
        if args.batch_click:
            selectors = [s.strip() for s in args.batch_click.split(',')]
            results = batch_click(session, selectors, args.batch_delay)
            print(f"[ok] 批量点击完成，成功 {len(results)}/{len(selectors)} 个")
        
        # 输入操作
        if args.type_index is not None or args.type_selector:
            cmd_type(
                session,
                index=args.type_index,
                selector=args.type_selector,
                text=args.text,
                clear_first=args.clear_first,
                retry_config=retry_config,
                operation="type"
            )
        
        if args.key:
            cmd_keys(session, args.key, retry_config=retry_config, operation="key")
        
        # 悬停操作
        if args.hover_index is not None:
            cmd_hover(session, args.hover_index, retry_config=retry_config, operation="hover")
        
        # 滚动操作
        if args.scroll_to_index is not None:
            cmd_scroll(session, index=args.scroll_to_index, retry_config=retry_config, operation="scroll")
        if args.scroll_by:
            cmd_scroll(session, by=tuple(args.scroll_by), retry_config=retry_config, operation="scroll")
        if args.scroll_to_top:
            cmd_scroll(session, to_top=True, retry_config=retry_config, operation="scroll")
        if args.scroll_to_bottom:
            cmd_scroll(session, to_bottom=True, retry_config=retry_config, operation="scroll")
        
        # 拖拽操作
        if args.drag_from_index is not None and args.drag_to_index is not None:
            cmd_drag(session, args.drag_from_index, args.drag_to_index, retry_config=retry_config, operation="drag")
        
        # 等待操作
        if args.wait_selector:
            cmd_wait(session, selector=args.wait_selector, timeout=args.timeout, retry_config=retry_config, operation="wait")
        if args.wait_strategy:
            cmd_wait(session, strategy=args.wait_strategy, timeout=args.timeout, retry_config=retry_config, operation="wait")
        
        # 动态页面操作
        if args.wait_page_ready or args.wait_page_selector:
            import asyncio
            from src.core.dynamic_page_support import DynamicPageSupport
            support = DynamicPageSupport(session)
            selector = args.wait_page_selector or None
            result = asyncio.run(support.wait_for_page_ready(
                selector=selector,
                timeout=args.timeout,
            ))
            print(f"[ok] 页面就绪检查完成: {result}")
        
        if args.scroll_load:
            import asyncio
            from src.core.dynamic_page_support import DynamicPageSupport
            support = DynamicPageSupport(session)
            result = asyncio.run(support.scroll_to_load(
                item_selector=args.scroll_item_selector,
                max_pages=args.scroll_max_pages,
                max_items=args.scroll_max_items,
            ))
            print_json(result.to_dict() if hasattr(result, 'to_dict') else {
                "pages_loaded": result.pages_loaded,
                "items_found": result.items_found,
                "success": result.success,
            })
        
        if args.wait_lazy:
            import asyncio
            from src.core.dynamic_page_support import DynamicPageSupport
            support = DynamicPageSupport(session)
            loaded = asyncio.run(support.wait_for_lazy_images(
                selector=args.wait_lazy_selector,
                timeout=args.timeout,
            ))
            print(f"[ok] 懒加载图片已加载: {loaded} 张")
        
        if args.wait_spa_route:
            import asyncio
            from src.core.dynamic_page_support import DynamicPageSupport
            support = DynamicPageSupport(session)
            result = asyncio.run(support.wait_for_spa_route(
                timeout=args.timeout,
                expected_url=args.wait_spa_url,
            ))
            print(f"[ok] SPA 路由稳定检查完成: {result}")
        
        # 状态查询
        if args.state:
            print_json(current_state(session))
        
        if args.elements:
            elements = scan_interactive_elements(session)
            print_json({"count": len(elements), "elements": elements})
        
        # 如果没有执行任何操作，打印帮助
        if not any([
            args.goto,
            args.back,
            args.forward,
            args.reload,
            args.screenshot,
            args.click_index,
            args.click_xy,
            args.click_selector,
            args.smart_click,
            args.double_click_index,
            args.right_click_index,
            args.batch_click,
            args.type_index,
            args.type_selector,
            args.key,
            args.hover_index,
            args.scroll_to_index,
            args.scroll_by,
            args.scroll_to_top,
            args.scroll_to_bottom,
            args.drag_from_index,
            args.drag_to_index,
            args.wait_selector,
            args.wait_strategy,
            args.state,
            args.elements,
        ]):
            parser.print_help()
            
    except BrowserError as e:
        print(f"[error] {e}")
        print_json(e.to_dict())
        exit(1)
    except Exception as e:
        print(f"[error] 操作失败: {e}")
        traceback.print_exc()
        exit(1)
    finally:
        session.close()


if __name__ == "__main__":
    main()
