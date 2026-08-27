#!/usr/bin/env python
"""tools/fetch_iwencai_cookie.py — 打开一个真实浏览器窗口，让用户手动
完成问财（iwencai）的登录/验证，自动检测到 `hexin-v` cookie 后写进
`config/secrets.local.yaml`。

背景：`data_sources.py` 里"问财 hexin-v 令牌"小节说明过，这个令牌是
前端一段混淆 JS 动态算出来的，不是简单的服务端 Set-Cookie，本项目
刻意不逆向那段加密逻辑。但一个**真实浏览器**加载页面时会自动执行那段
JS、自动算出正确的令牌——用户只要用真实浏览器打开一次页面（该验证/
登录就验证/登录），令牌就已经在浏览器的 cookie 里了。这个脚本自动化
的只是"打开浏览器 → 等令牌出现 → 读出来写进配置文件"这几个机械步骤，
用户自己要做的事（如果网站要求）跟平时用浏览器访问问财完全一样，不
存在这个脚本代替用户"骗过"验证的情况。

用法：
    cd external_projects/stock_watch
    pip install playwright
    playwright install chromium      # 首次使用需要下载浏览器内核
    python tools/fetch_iwencai_cookie.py

    # 网站这次没有要求任何验证，长时间检测不到 cookie 时，也可以手动
    # 在弹出的浏览器窗口里刷新页面、随便点点触发一下：
    python tools/fetch_iwencai_cookie.py --timeout 180

这个脚本是给人手动跑的交互式工具，不是 `entrypoints/` 下那种被
daemon/cron 无人值守调度的脚本（headless 环境下用不了——需要一个能
显示窗口、能让人操作的桌面环境），所以放在 `tools/` 而不是
`entrypoints/`，也不接入 `_common.run_entrypoint()` 那套账本机制。
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Optional

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from stock_watch.config import DEFAULT_SECRETS_PATH  # noqa: E402

IWENCAI_URL = "https://www.iwencai.com/"
COOKIE_NAME = "v"  # 页面里显示的参数名是 hexin-v，但实际存在 cookie 里的名字是 v


def _find_hexin_v(cookies: list) -> Optional[str]:
    for c in cookies:
        if c.get("name") == COOKIE_NAME and "iwencai" in c.get("domain", ""):
            return c.get("value")
    return None


def _write_cookie_to_secrets(secrets_path: Path, cookie_value: str) -> None:
    """把拿到的令牌写进 `secrets.local.yaml`，保留文件里已有的其它字段
    （比如未来这个文件里存了别的敏感配置），不整体覆盖。
    """
    existing = {}
    if secrets_path.exists():
        existing = yaml.safe_load(secrets_path.read_text(encoding="utf-8")) or {}
    existing["iwencai_cookie"] = cookie_value
    secrets_path.parent.mkdir(parents=True, exist_ok=True)
    secrets_path.write_text(
        yaml.safe_dump(existing, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--timeout", type=int, default=120,
        help="最多等待用户完成登录/验证的秒数（默认 120）",
    )
    parser.add_argument(
        "--secrets-path", type=Path, default=DEFAULT_SECRETS_PATH,
        help=f"写入的目标文件（默认 {DEFAULT_SECRETS_PATH}）",
    )
    args = parser.parse_args()

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print(
            "未安装 playwright。请先运行：\n"
            "  pip install playwright\n"
            "  playwright install chromium\n"
            "（后者会下载一个 Chromium 浏览器内核，第一次运行耗时较长，"
            "属正常现象）",
            file=sys.stderr,
        )
        return 1

    print(f"正在打开浏览器窗口，访问 {IWENCAI_URL} ...")
    print("如果页面要求登录/滑块验证/短信验证，请在弹出的窗口里手动完成。")
    print(f"脚本会每秒检测一次 cookie，最多等待 {args.timeout} 秒。")

    with sync_playwright() as p:
        # headless=False：必须是有窗口、能看见、能操作的模式，这是这个
        # 工具存在的意义——headless 浏览器一样会跑 JS 算出令牌，但用户
        # 没法在里面手动完成登录/验证。
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()
        try:
            page.goto(IWENCAI_URL, timeout=30_000)
        except Exception as exc:  # noqa: BLE001 - playwright 异常类型较多，统一提示
            print(f"页面加载失败: {exc}", file=sys.stderr)
            browser.close()
            return 1

        cookie_value: Optional[str] = None
        deadline = time.monotonic() + args.timeout
        while time.monotonic() < deadline:
            cookie_value = _find_hexin_v(context.cookies())
            if cookie_value:
                break
            time.sleep(1)
            print(".", end="", flush=True)
        print()

        browser.close()

    if not cookie_value:
        print(
            f"{args.timeout} 秒内没有检测到 hexin-v cookie。可能原因：\n"
            "  - 验证/登录还没完成（可以加大 --timeout 再试一次）\n"
            "  - 问财这次改了 cookie 名称/存放位置（这个脚本按名字 'v' "
            "查找，需要重新确认）\n"
            "没有写入任何文件。",
            file=sys.stderr,
        )
        return 1

    _write_cookie_to_secrets(args.secrets_path, cookie_value)
    print(f"已获取令牌并写入 {args.secrets_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
