#!/usr/bin/env python3
"""steps/launch_zhihu_logged_in.py — 启动带知乎登录态的专用浏览器。

[browser-cdp 依赖清理] 原来是 `.claude/skills/browser-cdp/src/utilities/
launch_zhihu_logged_in.py`，本身只用 `subprocess` + 标准库，不依赖
browser-cdp 其它任何模块（`check_zhihu_logged_in()` 原来有一段"尝试 import
browser-cdp 的 CDP 客户端，失败则退化成 HTTP 方式检测"的分支，退化分支本身
就够用了，这里直接只保留退化分支，顺带去掉了那个不必要的依赖）。搬进本
workflow 的 `steps/` 目录下，成为一个独立可执行脚本（不是 workflow 的
step，本 workflow 不会自动调用它——需要使用者在运行 workflow 前手动执行一次）。

使用固定的 user-data-dir，首次启动后用户手动登录知乎，
后续每次运行都会自动保持登录状态。

用法:
    python steps/launch_zhihu_logged_in.py

首次运行后，浏览器会打开知乎，用户扫码/账号登录。
关闭浏览器后，下次运行同一命令，登录态会自动保留。
"""

import subprocess
import sys
import argparse
import time
import socket
import json
from pathlib import Path

# 固定的用户数据目录（相对于本脚本所在目录，即本 workflow 的 steps/ 目录下）
WORKFLOW_STEPS_DIR = Path(__file__).parent
USER_DATA_DIR = WORKFLOW_STEPS_DIR / "temp_data" / "zhihu_logged_in_profile"
DEBUG_PORT = 9336


def find_chrome():
    """自动查找 Chrome/Edge 浏览器"""
    import platform
    system = platform.system()

    if system == "Windows":
        paths = [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            str(Path.home() / "AppData/Local/Google/Chrome/Application/chrome.exe"),
        ]
    elif system == "Darwin":  # macOS
        paths = [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        ]
    else:  # Linux
        paths = [
            "/usr/bin/google-chrome",
            "/usr/bin/chromium-browser",
            "/usr/bin/microsoft-edge",
        ]

    for path in paths:
        if Path(path).exists():
            return path

    return None


def _remove_stale_singleton_locks(user_data_dir: Path) -> None:
    """Chrome 异常退出（被 taskkill/进程被杀/崩溃）后会在 user-data-dir 下留下
    SingletonLock/SingletonSocket/SingletonCookie 这几个锁文件。下次用同一个
    user-data-dir 启动时，如果这些锁文件还在，Chrome 会认为"已经有另一个
    实例在用这个 profile"，进而不会真正加载这个 profile 的 cookies/session
    ——不是目录/文件被删除，是 profile 没被正常使用，表现出来就是"登录态
    在重启后丢了"。只删除这三个锁文件，不动 cookies/sessions 等真实登录数据。
    """
    for name in ("SingletonLock", "SingletonSocket", "SingletonCookie"):
        p = user_data_dir / name
        try:
            if p.exists() or p.is_symlink():
                p.unlink()
        except Exception:
            pass


def check_zhihu_logged_in(port: int) -> bool:
    """检查知乎是否已登录。

    通过 CDP `/json/list` 看是否已经有一个 zhihu.com 的 tab——未登录时知乎
    会重定向到登录页而不是保持在正常页面，这里的判定是"存在知乎 tab 就
    假设已登录"，不追求 100% 精确（精确判定需要真的执行一段 JS 检查页面上
    的用户头像元素，但那需要一次额外的 CDP WebSocket 往返，这个脚本只是
    登录态启动器，不需要做到这么精确）。
    """
    try:
        import urllib.request
        with urllib.request.urlopen(f'http://127.0.0.1:{port}/json/list', timeout=3) as resp:
            tabs = json.loads(resp.read().decode())
            zhihu_tabs = [t for t in tabs if 'zhihu.com' in (t.get('url') or '')]
            if zhihu_tabs:
                print(f"  [debug] 找到 {len(zhihu_tabs)} 个知乎 tab，假设已登录")
                return True
        return False
    except Exception as e:
        print(f"  [warn] 检查登录状态失败：{e}")
        return False


def main():
    parser = argparse.ArgumentParser(description="启动带知乎登录态的专用浏览器")
    parser.add_argument("--no-login", action="store_true", help="仅启动浏览器，不自动打开知乎")
    parser.add_argument("--browser", help="指定浏览器路径（默认自动检测）")
    parser.add_argument("--port", type=int, default=DEBUG_PORT, help=f"调试端口（默认 {DEBUG_PORT}）")
    parser.add_argument("--reset", action="store_true", help="清除登录态，重新登录")
    parser.add_argument("--auto-continue", action="store_true", help="检测到已登录后自动退出")

    args = parser.parse_args()

    browser_path = args.browser or find_chrome()
    if not browser_path:
        print("[error] 未找到 Chrome/Edge 浏览器，请手动指定 --browser 参数")
        print('示例：python launch_zhihu_logged_in.py --browser "C:/Program Files/Google/Chrome/Application/chrome.exe"')
        sys.exit(1)

    print(f"[info] 浏览器：{browser_path}")
    print(f"[info] 数据目录：{USER_DATA_DIR}")
    print(f"[info] 调试端口：{args.port}")

    if args.reset:
        if USER_DATA_DIR.exists():
            import shutil
            print("[info] 清除登录态...")
            shutil.rmtree(USER_DATA_DIR)
        USER_DATA_DIR.mkdir(parents=True, exist_ok=True)
    else:
        USER_DATA_DIR.mkdir(parents=True, exist_ok=True)

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    port_in_use = sock.connect_ex(('127.0.0.1', args.port)) == 0
    sock.close()

    if port_in_use:
        print(f"\n[info] 检测到端口 {args.port} 已有浏览器运行")
        print("[info] 检查知乎登录状态...")
        if check_zhihu_logged_in(args.port):
            print("[ok] 知乎已登录！")
            if args.auto_continue:
                print("[info] 自动退出，可以开始搜索了")
                sys.exit(0)
        else:
            print("[warn] 知乎未登录，请在浏览器中登录")
    else:
        # 只在真的要拉起新进程时清理锁文件——如果端口已经通了（port_in_use
        # 分支），说明浏览器本来就在正常运行，不需要也不应该动锁文件。
        _remove_stale_singleton_locks(USER_DATA_DIR)

        cmd = [
            browser_path,
            f"--remote-debugging-port={args.port}",
            f"--user-data-dir={USER_DATA_DIR}",
            "--remote-allow-origins=*",
        ]

        if not args.no_login:
            cmd.append("https://www.zhihu.com")

        print("\n[info] 正在启动浏览器...")
        print("[info] 首次运行请手动登录知乎，后续运行会自动保持登录态\n")

        subprocess.Popen(cmd)
        time.sleep(3)

    if args.auto_continue and not args.no_login:
        print("\n[info] 等待知乎登录...")
        print("[info] 请在浏览器中登录知乎，登录后本脚本会自动退出\n")

        max_wait = 300  # 最多等待 5 分钟
        waited = 0

        while waited < max_wait:
            if check_zhihu_logged_in(args.port):
                print("\n[ok] 知乎已登录！自动退出...")
                sys.exit(0)

            time.sleep(5)
            waited += 5
            print(f"  等待中... ({waited}s)", end="\r")

        print("\n[warn] 超时未登录，请手动登录后重新运行")
        sys.exit(1)

    print("\n[ok] 浏览器已启动")
    print(f"[info] 调试端口：{args.port}")
    print("[info] 可以使用以下命令确认：")
    print(f"       curl http://127.0.0.1:{args.port}/json/list")
    print("\n[info] 按 Ctrl+C 退出（浏览器会继续运行）")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[info] 退出")


if __name__ == "__main__":
    main()
