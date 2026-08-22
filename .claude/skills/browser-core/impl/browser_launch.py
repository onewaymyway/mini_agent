"""
browser-core/impl/browser_launch.py — 拉起一个带 --remote-debugging-port 的
Chrome/Chromium 进程，headless 或 headed（有 GUI 窗口）均可。

风格与 `.claude/skills/browser-cdp/src/core/browser_launch.py::spawn_browser`
一致（同一批参数、同一套"Linux 下 root 用户需要 --no-sandbox"的处理），但
本文件是 browser-core 自己独立维护的一份精简实现，不 import browser-cdp，
理由见 `cdp_client.py` 文件头。

两种使用方式（对应本次改动"不应局限于无头浏览器"的要求）：

1. **launch_headless**：适合无 GUI 的服务器/沙盒环境，纯抓取场景，是此前
   `browser-site-scraper` 唯一设想过的用法。

2. **launch_headed**：拉起一个有真实窗口的浏览器。仍然是本模块自己启动的
   全新实例（全新 --user-data-dir，不带任何已登录状态），主要用于"需要看到
   浏览器界面调试"的场景，本身不解决登录问题——真正的登录场景应该用
   `session_manager.py` 的 **attach** 模式（见下），连接用户自己已经手动
   登录好的浏览器，而不是让 browser-core 去启动一个全新的、未登录的浏览器
   窗口然后期望用户在探索过程中冲进去手动登录（探索子agent的步数/时间预算
   是硬上限，不适合中途等待人工操作）。
"""
from __future__ import annotations

import os
import platform
import shutil
import subprocess
import tempfile
import time
from typing import Optional


def _find_chrome_binary() -> Optional[str]:
    """尽力猜测一个可用的 Chrome/Chromium/Edge 可执行文件路径。"""
    candidates_by_os = {
        "Darwin": [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
        ],
        "Windows": [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        ],
        "Linux": [
            "google-chrome",
            "google-chrome-stable",
            "chromium",
            "chromium-browser",
        ],
    }
    system = platform.system()
    for candidate in candidates_by_os.get(system, []):
        if os.path.isabs(candidate) and os.path.exists(candidate):
            return candidate
        found = shutil.which(candidate)
        if found:
            return found
    # 兜底：把所有系统的候选都试一遍 PATH 查找
    for names in candidates_by_os.values():
        for name in names:
            found = shutil.which(name)
            if found:
                return found
    return None


def spawn_browser(
    port: int,
    headless: bool,
    binary: Optional[str] = None,
    user_data_dir: Optional[str] = None,
    start_url: str = "about:blank",
    window_size: str = "1366,900",
) -> subprocess.Popen:
    binary = binary or _find_chrome_binary()
    if not binary:
        raise RuntimeError(
            "未找到可用的 Chrome/Chromium/Edge 可执行文件。请安装浏览器，或"
            "改用 attach 模式连接一个已经手动启动、带 --remote-debugging-port"
            "的浏览器实例（见 session_manager.py 的 mode='attach'）。"
        )
    user_data_dir = os.path.abspath(
        user_data_dir or os.path.join(tempfile.gettempdir(), f"browser-core-profile-{port}")
    )
    os.makedirs(user_data_dir, exist_ok=True)
    args = [
        binary,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={user_data_dir}",
        "--remote-allow-origins=*",
        "--no-first-run",
        "--no-default-browser-check",
        f"--window-size={window_size}",
    ]
    if headless:
        args += ["--headless=new", "--disable-gpu", "--hide-scrollbars", "--mute-audio"]
    if start_url:
        args.append(start_url)
    if platform.system() == "Linux" and hasattr(os, "geteuid") and os.geteuid() == 0:
        args.insert(1, "--no-sandbox")
    return subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def wait_port_alive(
    is_alive_fn,
    timeout: float = 15.0,
    proc: Optional[subprocess.Popen] = None,
) -> tuple[bool, Optional[str]]:
    """轮询等待调试端口就绪；若传入 proc，同时检测进程是否提前退出。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if is_alive_fn():
            return True, None
        if proc is not None:
            code = proc.poll()
            if code is not None:
                return False, f"浏览器进程已退出 (exit code={code})"
        time.sleep(0.3)
    return False, None
