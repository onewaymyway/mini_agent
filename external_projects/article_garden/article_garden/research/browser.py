"""research/browser.py — 浏览器渲染（备用抓取）。

调试端口未开启时，自动在本机启动 Chrome（远程调试端口 + 独立的用户目录），再通过 Playwright 的
connect_over_cdp 连接。独立用户目录：Chrome 不允许对默认用户目录开启远程调试，且不影响你日常的浏览器资料。
需要：pip install playwright，并且本机安装了 Chrome 或 Chromium。
边界：只渲染调研流程允许抓取的网址。robots.txt 禁止、以及 401/403 访问控制，由 fetch 层直接拒绝，不会到达这里。
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable, Optional, Tuple

RenderFn = Callable[[str], Tuple[Optional[str], str]]
_LAUNCH_LOCK = threading.Lock()


def _mac_candidates():
    return ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
            "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"]


def _win_candidates():
    roots = [os.environ.get("ProgramFiles", r"C:\Program Files"),
             os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
             os.environ.get("LOCALAPPDATA", "")]
    rel = [r"Google\Chrome\Application\chrome.exe", r"Chromium\Application\chrome.exe",
           r"Microsoft\Edge\Application\msedge.exe"]
    return [os.path.join(r, x) for r in roots if r for x in rel]


def find_chrome() -> Optional[str]:
    """在本机查找可用的 Chrome / Chromium / Edge，找不到返回 None。"""
    import sys
    if sys.platform == "darwin":
        cands = _mac_candidates()
    elif os.name == "nt":
        cands = _win_candidates()
    else:
        for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "chrome"):
            hit = shutil.which(name)
            if hit:
                return hit
        return None
    for c in cands:
        if os.path.isfile(c):
            return c
    return None


def port_open(port: int, host: str = "127.0.0.1") -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False


def ensure_debug_browser(port: int, profile_dir: Path, wait_sec: float = 25.0) -> Tuple[bool, str]:
    """确保远程调试端口可用：未开启则自动启动 Chrome，并等待端口就绪。返回 (是否可用, 说明)。"""
    if port_open(port):
        return True, ""
    with _LAUNCH_LOCK:
        if port_open(port):          # 另一个线程可能刚刚启动
            return True, ""
        exe = find_chrome()
        if not exe:
            return False, "未找到 Chrome 或 Chromium，请先安装，或手动以调试端口启动浏览器"
        profile_dir.mkdir(parents=True, exist_ok=True)
        args = [exe, f"--remote-debugging-port={port}", f"--user-data-dir={profile_dir}",
                "--no-first-run", "--no-default-browser-check", "about:blank"]
        try:
            subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             start_new_session=True)
        except OSError as exc:
            return False, f"无法启动浏览器（{exc}）"
        deadline = time.monotonic() + wait_sec
        while time.monotonic() < deadline:
            if port_open(port):
                return True, f"已自动启动调试浏览器（端口 {port}）"
            time.sleep(0.5)
        return False, f"已启动浏览器，但调试端口 {port} 在 {wait_sec:.0f} 秒内未就绪"


def render_fn(port: int, profile_dir: Path, timeout_sec: float = 30.0) -> RenderFn:
    """返回 render(url) -> (html 或 None, 说明)。每次渲染前确认调试浏览器可用（被关闭后会自动重新启动）。
    只关闭自己打开的标签页，不关闭浏览器。"""

    def render(url: str) -> Tuple[Optional[str], str]:
        try:
            from playwright.sync_api import sync_playwright  # type: ignore
        except ImportError:
            return None, "未安装 playwright（请执行 pip install playwright）"
        ok, note = ensure_debug_browser(port, profile_dir)
        if not ok:
            return None, note
        try:
            with sync_playwright() as p:
                browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{port}", timeout=timeout_sec * 1000)
                context = browser.contexts[0] if browser.contexts else browser.new_context()
                page = context.new_page()
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=timeout_sec * 1000)
                    page.wait_for_timeout(2000)          # 给脚本一点时间加载正文
                    return page.content(), note
                finally:
                    page.close()
        except Exception as exc:  # noqa: BLE001 — 原因展示给用户
            return None, f"{type(exc).__name__}: {str(exc)[:160]}"

    return render
