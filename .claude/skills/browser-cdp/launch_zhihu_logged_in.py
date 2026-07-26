#!/usr/bin/env python3
"""启动带知乎登录态的专用浏览器

使用固定的 user-data-dir，首次启动后用户手动登录知乎，
后续每次运行都会自动保持登录状态。

用法:
    python launch_zhihu_logged_in.py
    
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

# 固定的用户数据目录（相对于 browser-cdp 目录）
SKILL_DIR = Path(__file__).parent
USER_DATA_DIR = SKILL_DIR / "temp_data" / "zhihu_logged_in_profile"
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
            str(Path.home() / "AppData\Local\Google\Chrome\Application\chrome.exe"),
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
    """
    [next_doc/browser_cdp_stability_fixes.md #1（修正版）] 真正的根因：Chrome
    异常退出（被 taskkill/进程被杀/崩溃）后会在 user-data-dir 下留下
    SingletonLock/SingletonSocket/SingletonCookie 这几个锁文件。下次用同一个
    user-data-dir 启动时，如果这些锁文件还在，Chrome 会认为"已经有另一个
    实例在用这个 profile"，进而**不会真正加载这个 profile 的 cookies/session**
    ——不是目录/文件被删除，是 profile 没被正常使用，表现出来就是"登录态
    在重启后丢了"。

    browser_launch.py::spawn_browser() 里一直有这一步（_remove_singleton_locks），
    但这个独立的 launch_zhihu_logged_in.py 脚本之前没有，是本次修复遗漏的
    真正原因。只删除这三个锁文件，不动 cookies/sessions 等真实登录数据。
    """
    for name in ("SingletonLock", "SingletonSocket", "SingletonCookie"):
        p = user_data_dir / name
        try:
            if p.exists() or p.is_symlink():
                p.unlink()
        except Exception:
            pass


def check_zhihu_logged_in(port: int) -> bool:
    """检查知乎是否已登录"""
    try:
        # 方法 1: 尝试通过 CDP 连接（如果成功则使用）
        try:
            from browser_console import cmd_eval, get_session
            
            args = argparse.Namespace(
                port=port,
                host='127.0.0.1',
                tab_id=None,
                url_contains='zhihu.com',
                title_contains=None
            )
            
            session = get_session(args)
            
            # 检查登录状态
            js_code = """
            (() => {
                const userAvatar = document.querySelector('.UserLink--current, .Avatar, [class*=\"Avatar\"], .ProfileHeader-avatar');
                const userInfo = document.querySelector('.UserLink--current, .ProfileHeader-name');
                const loginBtn = document.querySelector('.SignButton, .LoginButton, button.SignLink');
                const url = window.location.href;
                const isZhihuHome = url.includes('zhihu.com') && !url.includes('/login');
                
                return JSON.stringify({
                    hasAvatar: !!userAvatar,
                    hasUserInfo: !!userInfo,
                    hasLoginBtn: !!loginBtn,
                    isZhihuHome: isZhihuHome,
                    isLoggedIn: !!userAvatar || !!userInfo || (isZhihuHome && !loginBtn)
                });
            })()
            """
            
            result = cmd_eval(session, js_code)
            session.close()
            
            if result:
                info = json.loads(result)
                print(f"  [debug] CDP 登录检测：{info}")
                return info.get('isLoggedIn', False)
        except Exception as cdp_err:
            print(f"  [debug] CDP 连接失败：{cdp_err}，尝试备用方法")
        
        # 方法 2: 通过 HTTP 请求检查（需要获取 Cookie）
        # 由于无法直接获取浏览器的 Cookie，我们采用另一种方式：
        # 检查是否有知乎相关的进程在运行，并且端口 9336 可访问
        import urllib.request
        
        # 检查 CDP 接口是否可访问
        try:
            with urllib.request.urlopen(f'http://127.0.0.1:{port}/json/list', timeout=3) as resp:
                tabs = json.loads(resp.read().decode())
                zhihu_tabs = [t for t in tabs if 'zhihu.com' in (t.get('url') or '')]
                
                if zhihu_tabs:
                    # 有知乎 tab，假设已登录（因为未登录时知乎会重定向到登录页）
                    print(f"  [debug] 找到 {len(zhihu_tabs)} 个知乎 tab，假设已登录")
                    return True
        except Exception as http_err:
            print(f"  [debug] HTTP 检查失败：{http_err}")
        
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
    
    # 查找浏览器
    browser_path = args.browser or find_chrome()
    if not browser_path:
        print("[error] 未找到 Chrome/Edge 浏览器，请手动指定 --browser 参数")
        print('示例：python launch_zhihu_logged_in.py --browser "C:/Program Files/Google/Chrome/Application/chrome.exe"')
        sys.exit(1)
    
    print(f"[info] 浏览器：{browser_path}")
    print(f"[info] 数据目录：{USER_DATA_DIR}")
    print(f"[info] 调试端口：{args.port}")
    
    # 如果指定了 --reset，清除用户数据目录
    if args.reset:
        if USER_DATA_DIR.exists():
            import shutil
            print(f"[info] 清除登录态...")
            shutil.rmtree(USER_DATA_DIR)
        USER_DATA_DIR.mkdir(parents=True, exist_ok=True)
    else:
        USER_DATA_DIR.mkdir(parents=True, exist_ok=True)
    
    # 先检查是否已经有浏览器在运行
    import socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    port_in_use = sock.connect_ex(('127.0.0.1', args.port)) == 0
    sock.close()
    
    if port_in_use:
        print(f"\n[info] 检测到端口 {args.port} 已有浏览器运行")
        
        # 检查是否已登录知乎
        print(f"[info] 检查知乎登录状态...")
        if check_zhihu_logged_in(args.port):
            print(f"[ok] 知乎已登录！")
            if args.auto_continue:
                print(f"[info] 自动退出，可以开始搜索了")
                sys.exit(0)
        else:
            print(f"[warn] 知乎未登录，请在浏览器中登录")
    else:
        # [next_doc/browser_cdp_stability_fixes.md #1] 只在真的要拉起新进程
        # 时清理锁文件——如果端口已经通了（port_in_use 分支），说明浏览器
        # 本来就在正常运行，不需要也不应该动锁文件。
        _remove_stale_singleton_locks(USER_DATA_DIR)

        # 构建启动命令
        cmd = [
            browser_path,
            f"--remote-debugging-port={args.port}",
            f"--user-data-dir={USER_DATA_DIR}",
            "--remote-allow-origins=*",
        ]
        
        # 如果不指定 --no-login，打开知乎
        if not args.no_login:
            cmd.append("https://www.zhihu.com")
        
        print(f"\n[info] 正在启动浏览器...")
        print(f"[info] 首次运行请手动登录知乎，后续运行会自动保持登录态\n")
        
        # 启动浏览器（不等待）
        subprocess.Popen(cmd)
        
        # 等待浏览器启动
        time.sleep(3)
    
    # 如果指定了 --auto-continue，循环检查登录状态
    if args.auto_continue and not args.no_login:
        print(f"\n[info] 等待知乎登录...")
        print(f"[info] 请在浏览器中登录知乎，登录后本脚本会自动退出\n")
        
        max_wait = 300  # 最多等待 5 分钟
        waited = 0
        
        while waited < max_wait:
            if check_zhihu_logged_in(args.port):
                print(f"\n[ok] 知乎已登录！自动退出...")
                sys.exit(0)
            
            time.sleep(5)
            waited += 5
            print(f"  等待中... ({waited}s)", end="\r")
        
        print(f"\n[warn] 超时未登录，请手动登录后重新运行")
        sys.exit(1)
    
    print(f"\n[ok] 浏览器已启动")
    print(f"[info] 调试端口：{args.port}")
    print(f"[info] 可以使用以下命令连接:")
    print(f"       python browser_console.py --port {args.port} --eval '...'")
    print(f"       python zhihu_search_with_login.py --batch")
    print(f"\n[info] 按 Ctrl+C 退出（浏览器会继续运行）")
    
    try:
        # 保持脚本运行，直到用户中断
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[info] 退出")


if __name__ == "__main__":
    main()
