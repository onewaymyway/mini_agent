#!/usr/bin/env python
"""entrypoints/refresh_iwencai_cookie.py — 刷新问财 cookie。

当 screener 报 401 时，运行此脚本尝试自动刷新。
有两种模式：

1. 自动模式（默认）：尝试通过 CDP 连接本地 Chrome，读取现有 cookie
   - 前提：Chrome 以 --remote-debugging-port=9222 启动，并已访问过问财
2. 交互模式（--spawn）：拉取新 Chrome，等待用户登录问财，自动写入 cookie

用法：
    python entrypoints/refresh_iwencai_cookie.py          # 自动模式
    python entrypoints/refresh_iwencai_cookie.py --spawn  # 交互模式
"""

from __future__ import annotations

import argparse
import sys

import _common  # noqa: F401


def main() -> int:
    parser = argparse.ArgumentParser(description="刷新问财 cookie")
    parser.add_argument("--spawn", action="store_true", help="拉取新 Chrome 实例并等待登录")
    args = parser.parse_args()

    if args.spawn:
        # 走工具脚本的完整流程
        # 注意：spawn 模式需要交互式终端（input() 等待用户按回车），
        # 在 daemon/non-TTY 环境下直接调用会抛出 EOFError。
        # 这里做前置检测，避免把错误传播到账本。
        if not sys.stdin.isatty():
            print(
                "❌ --spawn 模式需要交互式终端（stdin 必须是 tty），\n"
                "   当前环境不是交互式终端，无法使用此模式。\n"
                "   请改为运行自动模式（不带 --spawn），\n"
                "   或手动在终端里运行：\n"
                "     python entrypoints/refresh_iwencai_cookie.py --spawn",
                file=sys.stderr,
            )
            return 1
        sys.path.insert(0, str(_common.PROJECT_ROOT / "tools"))
        import fetch_iwencai_cookie as _fic
        try:
            return _fic.main()
        except EOFError:
            print(
                "❌ --spawn 模式需要交互式终端（stdin 必须是 tty），\n"
                "   当前环境不是交互式终端，无法使用此模式。\n"
                "   请改为运行自动模式（不带 --spawn），\n"
                "   或手动在终端里运行：\n"
                "     python entrypoints/refresh_iwencai_cookie.py --spawn",
                file=sys.stderr,
            )
            return 1

    # 自动模式：尝试通过 CDP 读取现有 cookie
    from stock_watch.data_sources import _try_refresh_iwencai_cookie_via_cdp

    if _try_refresh_iwencai_cookie_via_cdp():
        print("✅ 问财 cookie 已自动刷新")
        return 0
    else:
        print(
            "❌ 无法自动刷新 cookie。\n"
            "请运行以下命令之一：\n"
            "  ① python entrypoints/refresh_iwencai_cookie.py --spawn  # 交互式登录\n"
            "  ② 手动从浏览器复制 hexin-v cookie 到 config/secrets.local.yaml\n"
            "  ③ pip install pywencai  # 免维护方案",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(_common.run_entrypoint("refresh_iwencai_cookie", main, trigger="manual"))