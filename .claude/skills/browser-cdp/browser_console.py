"""
browser_console.py - 执行 JS / 抓取 console 日志 / 抓取网络请求

用法：
  python browser_console.py --tab <id> --eval "document.title"
  python browser_console.py --tab <id> --watch-console --duration 5     # 打开页面时顺手看5秒内的console输出
  python browser_console.py --tab <id> --watch-network --duration 5     # 5秒内发生的网络请求
"""
from __future__ import annotations

import argparse

from utils import add_connection_args, get_session, print_json


def cmd_eval(session, expr: str):
    value = session.eval_js(expr, await_promise=True)
    print_json({"result": value})


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


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_connection_args(parser)
    parser.add_argument("--eval", metavar="JS_EXPR", default=None)
    parser.add_argument("--watch-console", action="store_true")
    parser.add_argument("--watch-network", action="store_true")
    parser.add_argument("--duration", type=float, default=5.0, help="watch-* 模式下的采集时长（秒）")

    args = parser.parse_args()
    session = get_session(args)
    try:
        if args.eval is not None:
            cmd_eval(session, args.eval)
        if args.watch_console:
            cmd_watch_console(session, args.duration)
        if args.watch_network:
            cmd_watch_network(session, args.duration)
        if not any([args.eval is not None, args.watch_console, args.watch_network]):
            parser.print_help()
    finally:
        session.close()


if __name__ == "__main__":
    main()
