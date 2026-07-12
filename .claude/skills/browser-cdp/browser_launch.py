"""
browser_launch.py - 确保有一个可通过 CDP 连接的 Chrome，并管理 tab。

两种模式：
1. attach（默认）：假设用户已经手动用调试端口启动了浏览器，本脚本只负责发现/连接。
   Windows 下让用户手动创建一个带 --remote-debugging-port 的快捷方式最省心，
   因为直接"接管"一个已经在跑、没开调试端口的 Chrome 是做不到的（Chrome 限制）。
2. spawn：在当前机器上新开一个 Chrome/Chromium 进程（可无头），专门用于抓取，
   不影响用户正在使用的浏览器窗口。适合无 GUI 服务器环境。

用法示例：
  python browser_launch.py --ensure                      # 检查/发现，不满足则报错并给出指引
  python browser_launch.py --ensure --spawn               # 若无可用调试端口，自动 spawn 一个无头浏览器
  python browser_launch.py --list                         # 列出所有 tab
  python browser_launch.py --new "https://example.com"    # 新建 tab 并打开网址
  python browser_launch.py --close <target_id>
  python browser_launch.py --activate <target_id>         # 把某个 tab 切到前台（有 GUI 时可见）
"""
from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
import time

from cdp_client import (
    DEFAULT_HOST,
    DEFAULT_PORT,
    is_debug_port_alive,
    list_tabs,
    new_tab,
    close_tab,
    activate_tab,
    version_info,
)
from utils import print_json, die


WINDOWS_CHROME_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
]

LINUX_CHROME_CANDIDATES = [
    "google-chrome",
    "google-chrome-stable",
    "chromium",
    "chromium-browser",
]

MAC_CHROME_CANDIDATES = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
]


def find_chrome_binary() -> str | None:
    system = platform.system()
    candidates = []
    if system == "Windows":
        candidates = WINDOWS_CHROME_CANDIDATES
    elif system == "Darwin":
        candidates = MAC_CHROME_CANDIDATES
    else:
        candidates = LINUX_CHROME_CANDIDATES

    for c in candidates:
        if os.path.isabs(c):
            if os.path.exists(c):
                return c
        else:
            found = shutil.which(c)
            if found:
                return found
    return None


def spawn_browser(
    binary: str,
    port: int,
    user_data_dir: str,
    headless: bool,
    start_url: str = "about:blank",
) -> subprocess.Popen:
    os.makedirs(user_data_dir, exist_ok=True)
    args = [
        binary,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={user_data_dir}",
        "--no-first-run",
        "--no-default-browser-check",
    ]
    if headless:
        args += [
            "--headless=new",
            "--disable-gpu",
            "--hide-scrollbars",
            "--mute-audio",
            "--window-size=1366,900",
        ]
    args.append(start_url)
    # Windows 下沙盒相关参数一般不需要；Linux 容器里跑 root 常需要 --no-sandbox
    if platform.system() == "Linux" and hasattr(os, "geteuid") and os.geteuid() == 0:
        args.insert(1, "--no-sandbox")
    creationflags = 0
    if platform.system() == "Windows":
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP
    proc = subprocess.Popen(
        args,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=creationflags,
    )
    return proc


def wait_port_alive(host: str, port: int, timeout: float = 15.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if is_debug_port_alive(host, port, timeout=1.0):
            return True
        time.sleep(0.3)
    return False


def cmd_ensure(args: argparse.Namespace) -> None:
    if is_debug_port_alive(args.host, args.port):
        info = version_info(args.host, args.port)
        print(f"[ok] 已连接到调试端口 {args.host}:{args.port} -> {info.get('Browser')}")
        return

    if not args.spawn:
        die(
            "调试端口不可用。\n"
            "  方式一（推荐，可与用户共享同一浏览器窗口）：\n"
            "    Windows 下完全关闭 Chrome 后，用以下命令重新打开：\n"
            r'    "C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222'
            "\n"
            "  方式二：加 --spawn 参数，由本脚本自动新开一个专用浏览器实例（不影响你现有窗口）。"
        )

    binary = args.binary or find_chrome_binary()
    if not binary:
        die("未找到 Chrome/Chromium 可执行文件，请用 --binary 指定路径")

    proc = spawn_browser(
        binary=binary,
        port=args.port,
        user_data_dir=args.user_data_dir,
        headless=args.headless,
        start_url=args.start_url,
    )
    if not wait_port_alive(args.host, args.port, timeout=args.spawn_timeout):
        die(f"已启动浏览器进程 (pid={proc.pid}) 但调试端口 {args.port} 在 {args.spawn_timeout}s 内未就绪")
    info = version_info(args.host, args.port)
    print(
        f"[ok] 已启动新浏览器实例 pid={proc.pid} headless={args.headless} "
        f"-> {args.host}:{args.port} ({info.get('Browser')})\n"
        f"     user_data_dir={args.user_data_dir}"
    )


def cmd_list(args: argparse.Namespace) -> None:
    tabs = list_tabs(args.host, args.port)
    print_json(
        [
            {"id": t.get("id"), "title": t.get("title"), "url": t.get("url"), "type": t.get("type")}
            for t in tabs
        ]
    )


def cmd_new(args: argparse.Namespace) -> None:
    t = new_tab(args.new, args.host, args.port)
    print_json({"id": t.get("id"), "url": t.get("url")})


def cmd_close(args: argparse.Namespace) -> None:
    close_tab(args.close, args.host, args.port)
    print(f"[ok] 已关闭 tab {args.close}")


def cmd_activate(args: argparse.Namespace) -> None:
    activate_tab(args.activate, args.host, args.port)
    print(f"[ok] 已激活 tab {args.activate}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)

    parser.add_argument("--ensure", action="store_true", help="检查调试端口是否可用")
    parser.add_argument("--spawn", action="store_true", help="配合 --ensure，若不可用则自动拉起一个专用实例")
    parser.add_argument("--headless", action="store_true", help="配合 --spawn，无 GUI 环境下使用（纯抓取场景）")
    parser.add_argument("--binary", default=None, help="浏览器可执行文件路径，不给则自动探测")
    parser.add_argument(
        "--user-data-dir",
        default=os.path.join(os.path.expanduser("~"), ".cdp_skill_profile"),
        help="spawn 时使用的独立 profile 目录，默认不会影响用户日常 Chrome profile",
    )
    parser.add_argument("--start-url", default="about:blank")
    parser.add_argument("--spawn-timeout", type=float, default=15.0)

    parser.add_argument("--list", action="store_true", help="列出当前所有 tab")
    parser.add_argument("--new", metavar="URL", default=None, help="新建 tab 并打开 URL")
    parser.add_argument("--close", metavar="TARGET_ID", default=None, help="关闭指定 tab")
    parser.add_argument("--activate", metavar="TARGET_ID", default=None, help="把指定 tab 切到前台")

    args = parser.parse_args()

    if args.ensure:
        cmd_ensure(args)
    if args.list:
        cmd_list(args)
    if args.new:
        cmd_new(args)
    if args.close:
        cmd_close(args)
    if args.activate:
        cmd_activate(args)

    if not any([args.ensure, args.list, args.new, args.close, args.activate]):
        parser.print_help()


if __name__ == "__main__":
    main()
