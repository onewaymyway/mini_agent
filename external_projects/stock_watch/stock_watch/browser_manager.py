"""
stock_watch/browser_manager.py — CDP 浏览器管理模块

功能：
1. 检测 CDP 调试端口是否可用
2. 自动启动 Chrome 浏览器（如未运行）
3. 提供 CDP 会话管理

设计目标：
- 完全独立于 browser-cdp skill，不依赖外部路径
- 自动管理浏览器生命周期
- 支持 Windows/macOS/Linux
"""
from __future__ import annotations

import json
import os
import platform
import subprocess
import time
from pathlib import Path
from typing import Optional

import requests

try:
    from .cdp_client import CDPSession, list_tabs, is_debug_port_alive
except ImportError:
    from cdp_client import CDPSession, list_tabs, is_debug_port_alive


# 配置
DEFAULT_PORT = 9333
DEFAULT_HOST = "127.0.0.1"
PROFILE_DIR = Path(__file__).parent.parent / ".cache" / "chrome_profile"
REGISTRY_FILE = Path(__file__).parent.parent / ".cache" / "browser_registry.json"


class BrowserManagerError(RuntimeError):
    """浏览器管理相关错误。"""


def _get_registry() -> dict:
    """加载浏览器注册表。"""
    if REGISTRY_FILE.exists():
        try:
            with open(REGISTRY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _save_registry(registry: dict) -> None:
    """保存浏览器注册表。"""
    REGISTRY_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(REGISTRY_FILE, "w", encoding="utf-8") as f:
        json.dump(registry, f, indent=2)


def _find_chrome_binary() -> Optional[str]:
    """查找 Chrome/Chromium 可执行文件。"""
    system = platform.system()
    if system == "Windows":
        candidates = [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        ]
        for path in candidates:
            if os.path.exists(path):
                return path
        # 尝试从 PATH 查找
        for path in ["chrome.exe", "msedge.exe"]:
            try:
                result = subprocess.run(["where", path], capture_output=True, text=True)
                if result.returncode == 0:
                    return result.stdout.strip().split("\n")[0]
            except Exception:
                pass
    elif system == "Darwin":
        candidates = [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        ]
        for path in candidates:
            if os.path.exists(path):
                return path
    else:  # Linux
        candidates = ["google-chrome", "google-chrome-stable", "chromium-browser", "microsoft-edge"]
        for cmd in candidates:
            try:
                result = subprocess.run(["which", cmd], capture_output=True, text=True)
                if result.returncode == 0:
                    return result.stdout.strip()
            except Exception:
                pass
    return None


def _start_browser(port: int, headless: bool = True) -> subprocess.Popen:
    """启动 Chrome 浏览器并返回进程对象。"""
    binary = _find_chrome_binary()
    if not binary:
        raise BrowserManagerError("未找到 Chrome/Chromium 可执行文件")

    PROFILE_DIR.mkdir(parents=True, exist_ok=True)

    args = [
        binary,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={PROFILE_DIR}",
        "--no-first-run",
        "--no-default-browser-check",
        "--remote-allow-origins=*",
    ]
    if headless:
        args.append("--headless=new")

    proc = subprocess.Popen(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    
    # 等待浏览器启动
    for _ in range(30):  # 最多等待 30 秒
        if proc.poll() is not None:
            break
        if is_debug_port_alive(port=port, timeout=1):
            time.sleep(1)  # 额外等待确保稳定
            return proc
        time.sleep(1)
    
    raise BrowserManagerError(f"浏览器启动超时或失败 (port={port})")


def ensure_browser_running(port: int = DEFAULT_PORT, headless: bool = True) -> tuple[int, str]:
    """确保浏览器运行，如未运行则启动。
    
    返回:
        (port, tab_id): 端口号和 tab ID
    """
    # 检查端口是否可用
    if is_debug_port_alive(port=port):
        try:
            tabs = list_tabs(port=port)
            if tabs:
                return port, tabs[0]["id"]
        except Exception:
            pass
    
    # 启动浏览器
    print(f"[Browser] 启动 Chrome 调试实例 (port={port}, headless={headless})...")
    proc = _start_browser(port=port, headless=headless)
    
    # 保存注册信息
    registry = _get_registry()
    registry["port"] = port
    registry["pid"] = proc.pid
    registry["started_at"] = time.time()
    _save_registry(registry)
    
    print(f"[Browser] 已启动 (pid={proc.pid})")
    return port, None


def get_cdp_session(port: int = DEFAULT_PORT) -> CDPSession:
    """获取 CDP 会话，自动确保浏览器运行。"""
    port, tab_id = ensure_browser_running(port=port)
    tabs = list_tabs(port=port)
    tab = next((t for t in tabs if t.get("type") == "page"), None)
    if not tab:
        raise BrowserManagerError("未找到可用的浏览器 tab")
    return CDPSession(ws_url=tab["webSocketDebuggerUrl"]), tab


def close_browser(port: int = DEFAULT_PORT) -> bool:
    """关闭浏览器。"""
    registry = _get_registry()
    pid = registry.get("pid")
    if pid:
        try:
            os.kill(pid, 15)  # SIGTERM
            time.sleep(1)
            os.kill(pid, 9)   # SIGKILL if still alive
        except Exception:
            pass
    
    # 清理锁文件
    lock_file = PROFILE_DIR / ".mini_agent_lock.json"
    if lock_file.exists():
        lock_file.unlink()
    
    registry.pop("pid", None)
    registry.pop("started_at", None)
    _save_registry(registry)
    
    print(f"[Browser] 已关闭 (pid={pid})")
    return True


def is_browser_running(port: int = DEFAULT_PORT) -> bool:
    """检查浏览器是否正在运行。"""
    return is_debug_port_alive(port=port)


if __name__ == "__main__":
    # 测试
    print("Testing browser manager...")
    port, tab_id = ensure_browser_running()
    print(f"Browser running on port {port}, tab: {tab_id}")
    
    session = get_cdp_session(port)
    title = session.eval_js("document.title", await_promise=False)
    print(f"Page title: {title}")
    session.close()
    
    close_browser(port)
    print("Done!")
