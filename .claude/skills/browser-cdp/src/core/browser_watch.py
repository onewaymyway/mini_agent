"""
browser_watch.py - 与用户协作时的"观察"能力

场景：用户自己在浏览器里操作（比如登录、验证码、支付），
Agent 不动手，只是每隔一段时间瞄一眼当前 tab 状态，判断用户是否已经完成。

用法：
  # 看当前所有 tab 里，用户实际在看哪个（通常取最后 focus 的，用 --active 近似）
  python browser_watch.py --list-state

  # 轮询等待某个 tab 的 URL 出现关键字（比如跳到了 /dashboard 说明登录成功）
  python browser_watch.py --tab <id> --wait-url-contains "/dashboard" --timeout 120 --interval 2

  # 轮询等待标题变化（例如从"处理中..."变成"完成"）
  python browser_watch.py --tab <id> --wait-title-contains "完成" --timeout 60
"""
from __future__ import annotations

import argparse
import time

from src.core.cdp_client import list_tabs, DEFAULT_HOST, DEFAULT_PORT
from src.core.utils import add_connection_args, get_session, print_json, die
from src.reliability.middleware import (
    get_middleware,
    OperationType,
    with_error_handling,
)


@with_error_handling("list_state", OperationType.NAVIGATION, max_retries=2)
def cmd_list_state(host: str, port: int):
    tabs = list_tabs(host, port)
    print_json(
        [{"id": t.get("id"), "title": t.get("title"), "url": t.get("url")} for t in tabs]
    )


@with_error_handling("poll_until", OperationType.WAIT, max_retries=1)
def poll_until(session, check_fn, timeout: float, interval: float, desc: str):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if check_fn():
                print(f"[ok] 条件已满足: {desc}")
                return True
        except Exception:
            pass
        time.sleep(interval)
    print(f"[timeout] 在 {timeout}s 内未满足条件: {desc}")
    return False


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_connection_args(parser)
    parser.add_argument("--list-state", action="store_true", help="列出所有 tab 的当前 url/title，不需要 --tab")
    parser.add_argument("--wait-url-contains", default=None)
    parser.add_argument("--wait-title-contains", default=None)
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--interval", type=float, default=2.0)

    args = parser.parse_args()

    if args.list_state:
        cmd_list_state(args.host, args.port)
        return

    if not (args.wait_url_contains or args.wait_title_contains):
        die("请指定 --list-state 或 --wait-url-contains / --wait-title-contains 之一")

    session = get_session(args)
    try:
        if args.wait_url_contains:
            poll_until(
                session,
                lambda: args.wait_url_contains in (session.eval_js("location.href") or ""),
                args.timeout,
                args.interval,
                f"URL 包含 '{args.wait_url_contains}'",
            )
        if args.wait_title_contains:
            poll_until(
                session,
                lambda: args.wait_title_contains in (session.eval_js("document.title") or ""),
                args.timeout,
                args.interval,
                f"标题包含 '{args.wait_title_contains}'",
            )
    finally:
        session.close()


if __name__ == "__main__":
    main()
