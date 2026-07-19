"""
perception/behavior/collectors/browser_launcher.py — 启动一个专用的、带 CDP 调试端口的浏览器实例

设计边界（对应设计方案）：
  - 这不是去接管用户日常在用的浏览器，而是新开一个独立实例：
    独立 --user-data-dir，默认不共享用户已登录的 cookie/历史/书签。
  - 用户需要主动执行 `/behavior browser start`，弹出的这个窗口才会被采集；
    平时用的浏览器不受影响、不会被静默接管。
  - 如果用户自己把 user_data_dir 显式指向真实 profile，属于用户自己的选择，
    这里只在文档/提示里提醒风险，不做阻拦。
"""

from __future__ import annotations

import json
import platform
import shutil
import subprocess
import time
import urllib.request
import urllib.error
from pathlib import Path
from typing import Optional

from ..config import _behavior_dir


def default_user_data_dir() -> Path:
    return _behavior_dir() / "browser_profile"


def _remove_singleton_locks(profile_dir: Path) -> None:
    """只删除 Chrome 单实例锁文件，不动其他数据（cookies/sessions 等）。

    Chrome 在 Windows/mac/Linux 下会用以下锁文件标记 profile 正被使用：
      - SingletonLock
      - SingletonSocket
      - SingletonCookie
    删除它们可以让新进程认为"没有实例在用这个 profile"，从而正常启动。
    """
    for name in ("SingletonLock", "SingletonSocket", "SingletonCookie"):
        p = profile_dir / name
        try:
            if p.exists() or p.is_symlink():
                p.unlink()
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.perception.behavior.collectors.browser_launcher._remove_singleton_locks')
            pass


_CANDIDATES_BY_OS = {
    "Windows": [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    ],
    "Darwin": [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
    ],
    "Linux": [
        "google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "microsoft-edge",
    ],
}


def find_browser_executable(explicit_path: str = "") -> Optional[str]:
    """按用户配置 > PATH 探测 > 常见安装路径的顺序找可执行文件。"""
    if explicit_path:
        return explicit_path if Path(explicit_path).exists() else None

    system = platform.system()
    for name in _CANDIDATES_BY_OS.get(system, []):
        # PATH 里的可执行名（Linux 常见）
        found = shutil.which(name)
        if found:
            return found
        # 绝对路径（Windows/macOS 常见）
        if Path(name).exists():
            return name
    return None


class DebugBrowserProcess:
    """管理一个带调试端口的浏览器子进程。"""

    def __init__(
        self,
        executable: str,
        port: int = 9333,
        user_data_dir: Optional[Path] = None,
        headless: bool = False,
    ) -> None:
        self._executable = executable
        self._port = port
        self._user_data_dir = user_data_dir or default_user_data_dir()
        self._headless = headless
        self._proc: Optional[subprocess.Popen] = None

    @property
    def port(self) -> int:
        return self._port

    @property
    def is_running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def start(self, startup_timeout: float = 10.0) -> None:
        if self.is_running:
            return
        self._user_data_dir.mkdir(parents=True, exist_ok=True)
        # 只删除单实例锁文件，保留 cookies/sessions 等数据
        _remove_singleton_locks(self._user_data_dir)

        args = [
            self._executable,
            f"--remote-debugging-port={self._port}",
            f"--user-data-dir={self._user_data_dir}",
            "--no-first-run",
            "--no-default-browser-check",
            # 禁用可能触发单实例重定向的功能
            "--disable-features=TranslateUI,BackgroundMode,BackgroundFetch",
            "--disable-backgrounding-occluded-windows",
            "--disable-renderer-backgrounding",
            "--disable-background-network-requests",
        ]
        if self._headless:
            args.append("--headless=new")

        self._proc = subprocess.Popen(
            args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        deadline = time.time() + startup_timeout
        last_err = None
        while time.time() < deadline:
            try:
                self.fetch_version_info()
                return
            except Exception as e:  # noqa: BLE001
                from mini_agent.errors import log_exception
                log_exception(e, where='mini_agent.perception.behavior.collectors.browser_launcher.DebugBrowserProcess.start')
                last_err = e
                time.sleep(0.3)
        raise RuntimeError(f"浏览器启动后 {startup_timeout}s 内未探测到 CDP 端口 {self._port}: {last_err}")

    def stop(self) -> None:
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        self._proc = None

    def fetch_version_info(self) -> dict:
        """GET http://127.0.0.1:<port>/json/version — 返回浏览器级 CDP endpoint。"""
        url = f"http://127.0.0.1:{self._port}/json/version"
        with urllib.request.urlopen(url, timeout=2) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def browser_ws_url(self) -> str:
        info = self.fetch_version_info()
        return info["webSocketDebuggerUrl"]
