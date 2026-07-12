"""
browser_nav.py - 导航控制

用法：
  python browser_nav.py --tab <id> --goto "https://example.com"
  python browser_nav.py --tab <id> --back
  python browser_nav.py --tab <id> --forward
  python browser_nav.py --tab <id> --reload
  python browser_nav.py --tab <id> --wait-selector "#result" --timeout 10
  python browser_nav.py --list                 # 不带 --goto 等操作时，打印当前 tab 的 url/title
"""
from __future__ import annotations

import argparse
import time

from cdp_client import CDPError
from utils import add_connection_args, get_session, print_json, die


def cmd_goto(session, url: str, wait_load: bool, timeout: float):
    session.send("Page.navigate", {"url": url})
    if wait_load:
        try:
            session.wait_event("Page.loadEventFired", timeout=timeout)
        except CDPError:
            print("[warn] 等待 load 事件超时，页面可能仍在加载或使用了长轮询/SPA 路由")


def cmd_wait_selector(session, selector: str, timeout: float):
    deadline = time.time() + timeout
    js = f"!!document.querySelector({selector!r})"
    while time.time() < deadline:
        try:
            if session.eval_js(js):
                print(f"[ok] 元素已出现: {selector}")
                return
        except Exception:
            pass
        time.sleep(0.3)
    die(f"等待元素超时: {selector}")


def current_state(session) -> dict:
    url = session.eval_js("location.href")
    title = session.eval_js("document.title")
    ready = session.eval_js("document.readyState")
    return {"url": url, "title": title, "readyState": ready}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_connection_args(parser)
    parser.add_argument("--goto", metavar="URL", default=None)
    parser.add_argument("--back", action="store_true")
    parser.add_argument("--forward", action="store_true")
    parser.add_argument("--reload", action="store_true")
    parser.add_argument("--wait-selector", default=None, help="等待某个 CSS 选择器对应元素出现")
    parser.add_argument("--no-wait-load", action="store_true", help="goto 后不等待 load 事件，立即返回")
    parser.add_argument("--timeout", type=float, default=15.0)

    args = parser.parse_args()
    session = get_session(args)
    try:
        if args.goto:
            cmd_goto(session, args.goto, wait_load=not args.no_wait_load, timeout=args.timeout)
        if args.back:
            session.eval_js("history.back()")
        if args.forward:
            session.eval_js("history.forward()")
        if args.reload:
            session.send("Page.reload")
            if not args.no_wait_load:
                try:
                    session.wait_event("Page.loadEventFired", timeout=args.timeout)
                except CDPError:
                    pass
        if args.wait_selector:
            cmd_wait_selector(session, args.wait_selector, args.timeout)

        print_json(current_state(session))
    finally:
        session.close()


if __name__ == "__main__":
    main()
