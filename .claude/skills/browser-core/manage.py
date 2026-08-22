"""
browser-core/manage.py — 直接从命令行列举/关闭调试浏览器，不经过
capability_call/探索子agent（阶段十八新增）。

这是给"人"用的运维小工具，不是探索子agent的工具原语（那些走
`explorer/tool_allowlist.json` 声明的 browser_list_sessions/
browser_close_session，见 `impl/browser_core_impl.py`）——两者背后调用的是
同一份 `impl/session_manager.py` 实现，区别只是调用入口：本脚本是一次性的
独立 Python 进程，直接 import impl/ 下的模块，不依赖 mini_agent 的 Agent/
CapabilityEngine 主流程，也不消耗任何探索预算/LLM 调用。

用法：
    python manage.py list
    python manage.py list --probe 127.0.0.1:9333
    python manage.py close --port 9222
    python manage.py close --all
    python manage.py close --port 9222 --no-kill   # 只断开连接，不杀浏览器进程

注意：因为这是一次性独立进程，`list`/`close` 看到的"已建立会话"永远是空的
（`session_manager._sessions` 是进程内内存字典，本脚本刚启动，谁都没连过）
——所以 `list` 默认会探测标准端口 9222 是否有浏览器在监听（见
`browser_core_impl.py::browser_list_sessions` 同样的默认探测逻辑）；`close`
在没有会话记录时会走"系统层面手动关闭"的提示分支。如果确实需要程序化关闭
一个当前没有会话记录、但确实在监听的调试端口，本脚本的 `close` 命令会先
临时 attach 一次再关闭，这一步只有本脚本会做（capability_call 里的
browser_close_session 出于"不擅自发起未声明的连接"的原则不会这样做）。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_IMPL_DIR = Path(__file__).resolve().parent / "impl"
if str(_IMPL_DIR) not in sys.path:
    sys.path.insert(0, str(_IMPL_DIR))

import session_manager  # type: ignore  # noqa: E402
from cdp_client import is_debug_port_alive  # type: ignore  # noqa: E402


def _parse_host_port(value: str) -> tuple[str, int]:
    host, _, port = value.partition(":")
    return host or "127.0.0.1", int(port) if port else 9222


def cmd_list(args: argparse.Namespace) -> None:
    sessions = session_manager.list_sessions()
    probe_targets = [_parse_host_port(p) for p in (args.probe or [])]
    if not probe_targets and not any(s["port"] == 9222 for s in sessions):
        probe_targets.append(("127.0.0.1", 9222))
    probed = [
        {"host": h, "port": p, "alive": is_debug_port_alive(h, p)}
        for h, p in probe_targets
    ]
    print(json.dumps({"sessions": sessions, "probed": probed}, ensure_ascii=False, indent=2))


def cmd_close(args: argparse.Namespace) -> None:
    kill_process = not args.no_kill
    if args.all:
        results = session_manager.close_all_sessions(kill_process=kill_process)
        print(json.dumps({"closed": results}, ensure_ascii=False, indent=2))
        return

    host, port = args.host, args.port
    result = session_manager.close_session(host=host, port=port, kill_process=kill_process)
    if not result["closed_our_session"] and is_debug_port_alive(host, port):
        # 独立脚本场景下，允许先临时 attach 一次再关闭——见文件头说明，这是
        # 本脚本相比 browser_close_session 工具原语额外做的一步便利操作。
        try:
            session_manager.get_or_create_session({"mode": "attach", "host": host, "port": port})
            result = session_manager.close_session(host=host, port=port, kill_process=kill_process)
            result["note"] = "本脚本先临时 attach 后再关闭（原本没有会话记录）。"
        except Exception as e:  # noqa: BLE001
            result["note"] = f"尝试临时 attach 后关闭失败: {e}"
    result.update({"host": host, "port": port})
    print(json.dumps(result, ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description="browser-core 调试浏览器管理工具")
    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list", help="列出已知/探测到的调试浏览器会话")
    p_list.add_argument("--probe", action="append", help="额外探测的 host:port，可重复传")
    p_list.set_defaults(func=cmd_list)

    p_close = sub.add_parser("close", help="关闭一个或全部调试浏览器会话")
    p_close.add_argument("--host", default="127.0.0.1")
    p_close.add_argument("--port", type=int, default=9222)
    p_close.add_argument("--all", action="store_true", help="关闭所有已知会话")
    p_close.add_argument("--no-kill", action="store_true", help="只断开 CDP 连接，不终止浏览器进程")
    p_close.set_defaults(func=cmd_close)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
