"""
browser_input.py - 模拟用户输入

配合 browser_screenshot.py --annotate 产生的编号使用，也支持直接给坐标/选择器。

用法：
  python browser_input.py --tab <id> --click-index 3
  python browser_input.py --tab <id> --click-xy 400 300
  python browser_input.py --tab <id> --click-selector "#submit"
  python browser_input.py --tab <id> --type-index 5 --text "hello world"
  python browser_input.py --tab <id> --type-selector "input[name=q]" --text "hello" --clear-first
  python browser_input.py --tab <id> --key Enter
  python browser_input.py --tab <id> --scroll-to-index 8
  python browser_input.py --tab <id> --scroll-by 0 600
  python browser_input.py --tab <id> --hover-index 2
"""
from __future__ import annotations

import argparse
import time

from src.core.utils import (
    add_connection_args,
    get_session,
    scan_interactive_elements,
    element_center,
    scroll_index_into_view,
    die,
)


KEY_CODES = {
    "Enter": {"key": "Enter", "code": "Enter", "windowsVirtualKeyCode": 13, "text": "\r"},
    "Tab": {"key": "Tab", "code": "Tab", "windowsVirtualKeyCode": 9},
    "Escape": {"key": "Escape", "code": "Escape", "windowsVirtualKeyCode": 27},
    "Backspace": {"key": "Backspace", "code": "Backspace", "windowsVirtualKeyCode": 8},
    "ArrowDown": {"key": "ArrowDown", "code": "ArrowDown", "windowsVirtualKeyCode": 40},
    "ArrowUp": {"key": "ArrowUp", "code": "ArrowUp", "windowsVirtualKeyCode": 38},
}


def mouse_click(session, x: float, y: float):
    for etype in ("mousePressed", "mouseReleased"):
        session.send(
            "Input.dispatchMouseEvent",
            {"type": etype, "x": x, "y": y, "button": "left", "clickCount": 1},
        )


def dispatch_key(session, key_name: str):
    spec = KEY_CODES.get(key_name)
    if not spec:
        die(f"暂不支持的按键: {key_name}（可在 KEY_CODES 里补充）")
    session.send("Input.dispatchKeyEvent", {"type": "rawKeyDown", **spec})
    if "text" in spec:
        session.send("Input.dispatchKeyEvent", {"type": "char", **spec})
    session.send("Input.dispatchKeyEvent", {"type": "keyUp", **spec})


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
            "Input.dispatchKeyEvent",
            {"type": "char", "text": ch, "unmodifiedText": ch, "key": ch},
        )
        session.send("Input.dispatchKeyEvent", {"type": "keyUp", "key": ch})
        if delay:
            time.sleep(delay)


def find_element_by_index(session, index: int) -> dict:
    elements = scan_interactive_elements(session)
    target = next((e for e in elements if e["index"] == index), None)
    if not target:
        die(f"未找到编号为 {index} 的元素，请先重新截图/扫描（页面可能已变化）")
    return target


def focus_and_click(session, x: float, y: float):
    mouse_click(session, x, y)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_connection_args(parser)

    parser.add_argument("--click-index", type=int, default=None)
    parser.add_argument("--click-xy", nargs=2, type=float, metavar=("X", "Y"), default=None)
    parser.add_argument("--click-selector", default=None)

    parser.add_argument("--type-index", type=int, default=None)
    parser.add_argument("--type-selector", default=None)
    parser.add_argument("--text", default=None)
    parser.add_argument("--clear-first", action="store_true", help="输入前先清空目标输入框内容")

    parser.add_argument("--key", default=None, choices=list(KEY_CODES.keys()))

    parser.add_argument("--hover-index", type=int, default=None)

    parser.add_argument("--scroll-to-index", type=int, default=None)
    parser.add_argument("--scroll-by", nargs=2, type=int, metavar=("DX", "DY"), default=None)

    args = parser.parse_args()
    session = get_session(args)
    try:
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
                return {{x: r.x + r.width/2, y: r.y + r.height/2}};
            }})()"""
            pos = session.eval_js(js)
            if not pos:
                die(f"未找到选择器: {args.click_selector}")
            focus_and_click(session, pos["x"], pos["y"])
            print(f"[ok] 已点击 {args.click_selector}")

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
                # 全选后删除，兼容 input/textarea/contenteditable
                session.send("Input.dispatchKeyEvent", {"type": "keyDown", "key": "a", "modifiers": 2})
                session.send("Input.dispatchKeyEvent", {"type": "keyUp", "key": "a", "modifiers": 2})
                dispatch_key(session, "Backspace")
            type_text(session, args.text)
            print(f"[ok] 已输入文本: {args.text!r}")

        if args.key:
            dispatch_key(session, args.key)
            print(f"[ok] 已按键: {args.key}")

        if args.hover_index is not None:
            el = find_element_by_index(session, args.hover_index)
            x, y = element_center(el)
            session.send("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x, "y": y})
            print(f"[ok] 已悬停到 #{args.hover_index}")

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

        if not any(
            [
                args.click_index is not None,
                args.click_xy,
                args.click_selector,
                args.type_index is not None,
                args.type_selector,
                args.key,
                args.hover_index is not None,
                args.scroll_to_index is not None,
                args.scroll_by,
            ]
        ):
            parser.print_help()
    finally:
        session.close()


if __name__ == "__main__":
    main()
