"""
browser_console.py - 执行 JS / 抓取 console 日志 / 抓取网络请求 / Cookie 管理

用法：
  python browser_console.py --tab <id> --eval "document.title"
  python browser_console.py --tab <id> --watch-console --duration 5     # 打开页面时顺手看5秒内的console输出
  python browser_console.py --tab <id> --watch-network --duration 5     # 5秒内发生的网络请求
  python browser_console.py --tab <id> --get-cookies                    # 获取当前页面所有 cookies
  python browser_console.py --tab <id> --set-cookie name value          # 设置 cookie
  python browser_console.py --tab <id> --delete-cookie name             # 删除 cookie
  python browser_console.py --tab <id> --clear-cookies                  # 清除所有 cookies
"""
from __future__ import annotations

import argparse

from src.core.utils import add_connection_args, get_session, print_json
from src.reliability.middleware import (
    get_middleware,
    OperationType,
    with_error_handling,
)


@with_error_handling("eval", OperationType.EXTRACT, max_retries=3)
def cmd_eval(session, expr: str):
    value = session.eval_js(expr, await_promise=True)
    print_json({"result": value})


# 别名，供测试使用
def eval(session, expr: str):
    """执行 JS 表达式（别名）"""
    return cmd_eval(session, expr)


def watch_console(session, duration: float):
    """监听控制台消息（别名）"""
    return cmd_watch_console(session, duration)


@with_error_handling("watch_console", OperationType.EXTRACT, max_retries=2)
def cmd_watch_console(session, duration: float):
    session.send("Runtime.enable")
    session.send("Log.enable")
    events = session.drain_events(duration=duration)
    logs = []
    for ev in events:
        method = ev.get("method")
        params = ev.get("params", {})
        if method == "Runtime.consoleAPICalled":
            args_repr = []
            for a in params.get("args", []):
                args_repr.append(a.get("value", a.get("description", "")))
            logs.append({"type": params.get("type"), "args": args_repr})
        elif method == "Runtime.exceptionThrown":
            detail = params.get("exceptionDetails", {})
            logs.append({"type": "exception", "text": detail.get("text"), "url": detail.get("url")})
        elif method == "Log.entryAdded":
            entry = params.get("entry", {})
            logs.append({"type": entry.get("level"), "text": entry.get("text"), "source": entry.get("source")})
    print_json(logs)


@with_error_handling("watch_network", OperationType.EXTRACT, max_retries=2)
def cmd_watch_network(session, duration: float):
    session.send("Network.enable")
    events = session.drain_events(method_prefix="Network.", duration=duration)
    requests = {}
    for ev in events:
        method = ev.get("method")
        params = ev.get("params", {})
        req_id = params.get("requestId")
        if not req_id:
            continue
        entry = requests.setdefault(req_id, {})
        if method == "Network.requestWillBeSent":
            entry["url"] = params.get("request", {}).get("url")
            entry["method"] = params.get("request", {}).get("method")
        elif method == "Network.responseReceived":
            entry["status"] = params.get("response", {}).get("status")
            entry["mimeType"] = params.get("response", {}).get("mimeType")
        elif method == "Network.loadingFailed":
            entry["error"] = params.get("errorText")
    print_json(list(requests.values()))


@with_error_handling("get_cookies", OperationType.TAB, max_retries=2)
def cmd_get_cookies(session):
    """获取当前页面的所有 cookies。"""
    cookies = session.get_all_cookies()
    print_json(cookies)


@with_error_handling("set_cookie", OperationType.TAB, max_retries=2)
def cmd_set_cookie(session, name: str, value: str, domain: str = "", path: str = "/",
                   secure: bool = True, http_only: bool = False, same_site: str = "Lax",
                   expires: float = -1):
    """设置 cookie。"""
    result = session.set_cookie(name, value, domain, path, secure, http_only, same_site, expires)
    print_json(result)


@with_error_handling("delete_cookie", OperationType.TAB, max_retries=2)
def cmd_delete_cookie(session, name: str, domain: str = "", path: str = "/"):
    """删除 cookie。"""
    result = session.delete_cookie(name, domain, path)
    print_json(result)


@with_error_handling("clear_cookies", OperationType.TAB, max_retries=2)
def cmd_clear_cookies(session):
    """清除所有 cookies。"""
    result = session.clear_all_cookies()
    print_json(result)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_connection_args(parser)
    parser.add_argument("--eval", metavar="JS_EXPR", default=None)
    parser.add_argument("--watch-console", action="store_true")
    parser.add_argument("--watch-network", action="store_true")
    parser.add_argument("--duration", type=float, default=5.0, help="watch-* 模式下的采集时长（秒）")
    # Cookie 相关参数
    parser.add_argument("--get-cookies", action="store_true", help="获取当前页面的所有 cookies")
    parser.add_argument("--set-cookie", nargs=2, metavar=("NAME", "VALUE"), help="设置 cookie: --set-cookie name value")
    parser.add_argument("--cookie-domain", default="", help="cookie 域名")
    parser.add_argument("--cookie-path", default="/", help="cookie 路径")
    parser.add_argument("--cookie-secure", action="store_true", default=True, help="cookie secure 标志")
    parser.add_argument("--cookie-http-only", action="store_true", help="cookie httpOnly 标志")
    parser.add_argument("--cookie-same-site", default="Lax", choices=["Strict", "Lax", "None"], help="cookie SameSite 策略")
    parser.add_argument("--cookie-expires", type=float, default=-1, help="cookie 过期时间（Unix 时间戳，-1 表示会话 cookie）")
    parser.add_argument("--delete-cookie", metavar="NAME", help="删除指定名称的 cookie")
    parser.add_argument("--clear-cookies", action="store_true", help="清除所有 cookies")

    args = parser.parse_args()
    session = get_session(args)
    try:
        if args.eval is not None:
            cmd_eval(session, args.eval)
        if args.watch_console:
            cmd_watch_console(session, args.duration)
        if args.watch_network:
            cmd_watch_network(session, args.duration)
        if args.get_cookies:
            cmd_get_cookies(session)
        if args.set_cookie:
            name, value = args.set_cookie
            cmd_set_cookie(session, name, value, args.cookie_domain, args.cookie_path,
                          args.cookie_secure, args.cookie_http_only, args.cookie_same_site, args.cookie_expires)
        if args.delete_cookie:
            cmd_delete_cookie(session, args.delete_cookie, args.cookie_domain, args.cookie_path)
        if args.clear_cookies:
            cmd_clear_cookies(session)
        if not any([args.eval is not None, args.watch_console, args.watch_network, args.get_cookies,
                    args.set_cookie, args.delete_cookie, args.clear_cookies]):
            parser.print_help()
    finally:
        session.close()


if __name__ == "__main__":
    main()
