"""
browser_input.py - 模拟用户输入（增强版）

配合 browser_screenshot.py --annotate 产生的编号使用，也支持直接给坐标/选择器。

用法：
  python browser_input.py --tab <id> --click-index 3
  python browser_input.py --tab <id> --click-xy 400 300
  python browser_input.py --tab <id> --click-selector "#submit"
  python browser_input.py --tab <id> --double-click-index 5
  python browser_input.py --tab <id> --right-click-index 5
  python browser_input.py --tab <id> --type-index 5 --text "hello world"
  python browser_input.py --tab <id> --type-selector "input[name=q]" --text "hello" --clear-first
  python browser_input.py --tab <id> --key Enter
  python browser_input.py --tab <id> --scroll-to-index 8
  python browser_input.py --tab <id> --scroll-by 0 600
  python browser_input.py --tab <id> --hover-index 2
  python browser_input.py --tab <id> --drag-from-index 1 --drag-to-index 2
  python browser_input.py --tab <id> --batch-click --selectors "#btn1,#btn2,#btn3"
  python browser_input.py --tab <id> --smart-click --text "提交"
"""
from __future__ import annotations

import argparse
import time
import logging
from typing import List, Dict, Optional, Tuple

from src.core.utils import (
    add_connection_args,
    get_session,
    scan_interactive_elements,
    element_center,
    scroll_index_into_view,
    die,
)
from src.reliability.middleware import (
    get_middleware,
    OperationType,
    with_error_handling,
)
from src.reliability.error import (
    ElementNotFoundError,
    CDPConnectionLostError,
)

logger = logging.getLogger(__name__)

KEY_CODES = {
    "Enter": {"key": "Enter", "code": "Enter", "windowsVirtualKeyCode": 13, "text": "\r"},
    "Tab": {"key": "Tab", "code": "Tab", "windowsVirtualKeyCode": 9},
    "Escape": {"key": "Escape", "code": "Escape", "windowsVirtualKeyCode": 27},
    "Backspace": {"key": "Backspace", "code": "Backspace", "windowsVirtualKeyCode": 8},
    "ArrowDown": {"key": "ArrowDown", "code": "ArrowDown", "windowsVirtualKeyCode": 40},
    "ArrowUp": {"key": "ArrowUp", "code": "ArrowUp", "windowsVirtualKeyCode": 38},
    "ArrowLeft": {"key": "ArrowLeft", "code": "ArrowLeft", "windowsVirtualKeyCode": 37},
    "ArrowRight": {"key": "ArrowRight", "code": "ArrowRight", "windowsVirtualKeyCode": 39},
    "Home": {"key": "Home", "code": "Home", "windowsVirtualKeyCode": 36},
    "End": {"key": "End", "code": "End", "windowsVirtualKeyCode": 35},
    "PageUp": {"key": "PageUp", "code": "PageUp", "windowsVirtualKeyCode": 33},
    "PageDown": {"key": "PageDown", "code": "PageDown", "windowsVirtualKeyCode": 34},
    "Delete": {"key": "Delete", "code": "Delete", "windowsVirtualKeyCode": 46},
}


@with_error_handling("mouse_click", OperationType.CLICK, max_retries=3)
def mouse_click(session, x: float, y: float, click_count: int = 1):
    """模拟鼠标点击"""
    for etype in ("mousePressed", "mouseReleased"):
        session.send(
            "Input.dispatchMouseEvent",
            {"type": etype, "x": x, "y": y, "button": "left", "clickCount": click_count},
        )


@with_error_handling("mouse_right_click", OperationType.CLICK, max_retries=3)
def mouse_right_click(session, x: float, y: float):
    """模拟鼠标右键点击"""
    for etype in ("mousePressed", "mouseReleased"):
        session.send(
            "Input.dispatchMouseEvent",
            {"type": etype, "x": x, "y": y, "button": "right", "clickCount": 1},
        )


@with_error_handling("dispatch_key", OperationType.INPUT, max_retries=3)
def dispatch_key(session, key_name: str):
    spec = KEY_CODES.get(key_name)
    if not spec:
        die(f"暂不支持的按键: {key_name}（可在 KEY_CODES 里补充）")
    session.send("Input.dispatchKeyEvent", {"type": "rawKeyDown", **spec})
    if "text" in spec:
        session.send("Input.dispatchKeyEvent", {"type": "char", **spec})
    session.send("Input.dispatchKeyEvent", {"type": "keyUp", **spec})


@with_error_handling("type_text", OperationType.INPUT, max_retries=3)
def type_text(session, text: str, delay: float = 0.02):
    """逐字符 dispatch，比直接写 value 更接近真实用户输入，能触发前端框架的事件监听。"""
    for ch in text:
        session.send(
            "Input.dispatchKeyEvent",
            {
                "type": "keyDown",
                "key": ch,
            },
        )
        session.send(
            "Input.dispatchKeyEvent", {"type": "char", "text": ch, "unmodifiedText": ch, "key": ch},
        )
        session.send("Input.dispatchKeyEvent", {"type": "keyUp", "key": ch})
        if delay:
            time.sleep(delay)


def find_element_by_index(session, index: int, max_retries: int = 3) -> dict:
    """查找元素，编号失效时自动重扫（最多重试 max_retries 次）"""
    for attempt in range(max_retries):
        elements = scan_interactive_elements(session)
        target = next((e for e in elements if e["index"] == index), None)
        if target:
            return target
        if attempt < max_retries - 1:
            logger.warning(f"编号 {index} 未找到，重新扫描中 ({attempt+1}/{max_retries})")
            time.sleep(0.5)
    die(f"未找到编号为 {index} 的元素，页面可能已变化，请重新截图/扫描")


def find_element_by_text(session, text: str, tag: str = None) -> Optional[dict]:
    """智能查找包含指定文本的元素"""
    js = f"""(() => {{
        const elements = Array.from(document.querySelectorAll('button, a, input, span, div, label'));
        const results = [];
        for (const el of elements) {{
            if ({tag!r} && el.tagName.toLowerCase() !== {tag!r}.toLowerCase()) continue;
            const t = (el.innerText || el.value || '').trim();
            if (t.includes({text!r})) {{
                const r = el.getBoundingClientRect();
                results.push({{
                    tag: el.tagName.toLowerCase(),
                    text: t.slice(0, 50),
                    x: r.x + r.width/2 + window.scrollX,
                    y: r.y + r.height/2 + window.scrollY,
                    index: null
                }});
            }}
        }}
        return results;
    }})()"""
    results = session.eval_js(js) or []
    return results[0] if results else None


def focus_and_click(session, x: float, y: float):
    mouse_click(session, x, y)


def drag_elements(session, from_x: float, from_y: float, to_x: float, to_y: float):
    """模拟拖拽操作"""
    # 按下
    session.send("Input.dispatchMouseEvent", {"type": "mousePressed", "x": from_x, "y": from_y, "button": "left"})
    time.sleep(0.1)
    # 移动
    session.send("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": to_x, "y": to_y})
    time.sleep(0.1)
    # 释放
    session.send("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": to_x, "y": to_y, "button": "left"})


def batch_click(session, selectors: List[str], delay: float = 0.5):
    """批量点击多个元素"""
    results = []
    for selector in selectors:
        js = f"""(() => {{
            const el = document.querySelector({selector!r});
            if (!el) return null;
            const r = el.getBoundingClientRect();
            return {{x: r.x + r.width/2 + window.scrollX, y: r.y + r.height/2 + window.scrollY}};
        }})()"""
        pos = session.eval_js(js)
        if pos:
            focus_and_click(session, pos["x"], pos["y"])
            results.append(selector)
            time.sleep(delay)
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_connection_args(parser)

    # 点击操作
    parser.add_argument("--click-index", type=int, default=None)
    parser.add_argument("--click-xy", nargs=2, type=float, metavar=("X", "Y"), default=None)
    parser.add_argument("--click-selector", default=None)
    parser.add_argument("--double-click-index", type=int, default=None)
    parser.add_argument("--right-click-index", type=int, default=None)
    parser.add_argument("--smart-click", default=None, help="根据文本智能查找并点击元素")
    parser.add_argument("--batch-click", default=None, help="批量点击，用逗号分隔选择器")
    parser.add_argument("--batch-delay", type=float, default=0.5, help="批量操作间隔（秒）")

    # 输入操作
    parser.add_argument("--type-index", type=int, default=None)
    parser.add_argument("--type-selector", default=None)
    parser.add_argument("--text", default=None)
    parser.add_argument("--clear-first", action="store_true", help="输入前先清空目标输入框内容")

    # 按键操作
    parser.add_argument("--key", default=None, choices=list(KEY_CODES.keys()))

    # 悬停操作
    parser.add_argument("--hover-index", type=int, default=None)

    # 滚动操作
    parser.add_argument("--scroll-to-index", type=int, default=None)
    parser.add_argument("--scroll-by", nargs=2, type=int, metavar=("DX", "DY"), default=None)
    parser.add_argument("--scroll-to-top", action="store_true")
    parser.add_argument("--scroll-to-bottom", action="store_true")

    # 拖拽操作
    parser.add_argument("--drag-from-index", type=int, default=None)
    parser.add_argument("--drag-to-index", type=int, default=None)
    parser.add_argument("--drag-from-xy", nargs=2, type=float, metavar=("X", "Y"), default=None)
    parser.add_argument("--drag-to-xy", nargs=2, type=float, metavar=("X", "Y"), default=None)

    args = parser.parse_args()
    session = get_session(args)
    try:
        # 点击操作
        if args.click_index is not None:
            el = find_element_by_index(session, args.click_index)
            x, y = element_center(el)
            focus_and_click(session, x, y)
            print(f"[ok] 已点击 #{args.click_index}: <{el['tag']}> {el['text'][:40]!r}")

        if args.click_xy:
            x, y = args.click_xy
            focus_and_click(session, x, y)
            print(f"[ok] 已点击坐标 ({x}, {y})")

        if args.click_selector:
            js = f"""(() => {{
                const el = document.querySelector({args.click_selector!r});
                if (!el) return null;
                const r = el.getBoundingClientRect();
                return {{x: r.x + r.width/2 + window.scrollX, y: r.y + r.height/2 + window.scrollY}};
            }})()"""
            pos = session.eval_js(js)
            if not pos:
                die(f"未找到选择器: {args.click_selector}")
            focus_and_click(session, pos["x"], pos["y"])
            print(f"[ok] 已点击 {args.click_selector}")

        if args.double_click_index is not None:
            el = find_element_by_index(session, args.double_click_index)
            x, y = element_center(el)
            mouse_click(session, x, y, click_count=2)
            print(f"[ok] 已双击 #{args.double_click_index}")

        if args.right_click_index is not None:
            el = find_element_by_index(session, args.right_click_index)
            x, y = element_center(el)
            mouse_right_click(session, x, y)
            print(f"[ok] 已右键点击 #{args.right_click_index}")

        if args.smart_click:
            el = find_element_by_text(session, args.smart_click)
            if el:
                focus_and_click(session, el["x"], el["y"])
                print(f"[ok] 已智能点击: {el['text']!r}")
            else:
                print(f"[warn] 未找到包含文本 '{args.smart_click}' 的元素")

        if args.batch_click:
            selectors = [s.strip() for s in args.batch_click.split(',')]
            results = batch_click(session, selectors, args.batch_delay)
            print(f"[ok] 批量点击完成，成功 {len(results)}/{len(selectors)} 个")

        # 输入操作
        if args.type_index is not None or args.type_selector:
            if args.text is None:
                die("--type-index/--type-selector 需要配合 --text 使用")
            if args.type_index is not None:
                el = find_element_by_index(session, args.type_index)
                x, y = element_center(el)
                focus_and_click(session, x, y)
            else:
                js = f"""(() => {{
                    const el = document.querySelector({args.type_selector!r});
                    if (!el) return false;
                    el.focus();
                    return true;
                }})()"""
                if not session.eval_js(js):
                    die(f"未找到选择器: {args.type_selector}")
            if args.clear_first:
                session.send("Input.dispatchKeyEvent", {"type": "keyDown", "key": "a", "modifiers": 2})
                session.send("Input.dispatchKeyEvent", {"type": "keyUp", "key": "a", "modifiers": 2})
                dispatch_key(session, "Backspace")
            type_text(session, args.text)
            print(f"[ok] 已输入文本: {args.text!r}")

        # 按键操作
        if args.key:
            dispatch_key(session, args.key)
            print(f"[ok] 已按键: {args.key}")

        # 悬停操作
        if args.hover_index is not None:
            el = find_element_by_index(session, args.hover_index)
            x, y = element_center(el)
            session.send("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x, "y": y})
            print(f"[ok] 已悬停到 #{args.hover_index}")

        # 滚动操作
        if args.scroll_to_index is not None:
            rect = scroll_index_into_view(session, args.scroll_to_index)
            if not rect:
                die(f"未找到编号为 {args.scroll_to_index} 的元素")
            print(f"[ok] 已滚动到 #{args.scroll_to_index}，新位置: {rect}")

        if args.scroll_by:
            dx, dy = args.scroll_by
            session.send(
                "Input.dispatchMouseEvent",
                {"type": "mouseWheel", "x": 400, "y": 300, "deltaX": dx, "deltaY": dy},
            )
            print(f"[ok] 已滚动 ({dx}, {dy})")

        if args.scroll_to_top:
            session.eval_js("window.scrollTo(0, 0)")
            print("[ok] 已滚动到顶部")

        if args.scroll_to_bottom:
            session.eval_js("window.scrollTo(0, document.body.scrollHeight)")
            print("[ok] 已滚动到底部")

        # 拖拽操作
        if args.drag_from_index is not None and args.drag_to_index is not None:
            from_el = find_element_by_index(session, args.drag_from_index)
            to_el = find_element_by_index(session, args.drag_to_index)
            from_x, from_y = element_center(from_el)
            to_x, to_y = element_center(to_el)
            drag_elements(session, from_x, from_y, to_x, to_y)
            print(f"[ok] 已从 #{args.drag_from_index} 拖拽到 #{args.drag_to_index}")

        if args.drag_from_xy and args.drag_to_xy:
            from_x, from_y = args.drag_from_xy
            to_x, to_y = args.drag_to_xy
            drag_elements(session, from_x, from_y, to_x, to_y)
            print(f"[ok] 已从 ({from_x}, {from_y}) 拖拽到 ({to_x}, {to_y})")

        if not any([
            args.click_index is not None,
            args.click_xy,
            args.click_selector,
            args.double_click_index,
            args.right_click_index,
            args.smart_click,
            args.batch_click,
            args.type_index is not None,
            args.type_selector,
            args.key,
            args.hover_index is not None,
            args.scroll_to_index is not None,
            args.scroll_by,
            args.scroll_to_top,
            args.scroll_to_bottom,
            args.drag_from_index is not None,
            args.drag_from_xy,
        ]):
            parser.print_help()
    finally:
        session.close()


if __name__ == "__main__":
    main()
