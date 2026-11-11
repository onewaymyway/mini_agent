"""共享浏览器会话。

设计要点：
- 所有站点共用同一个持久化 profile 目录（paths.browser_profile_dir），登录一次即可被后续无头抓取复用。
- 同一 profile 同一时间只能被一个浏览器进程占用，因此所有使用 profile 的操作都在 file_lock 内串行执行。
- 登录由用户触发：有头浏览器打开登录页，用户完成登录后回到终端确认，系统再用无头模式验证登录是否生效。
- 登录状态记录在 browser/login_status.json（纯文本，不含任何凭据）。

playwright 在方法内部才 import，因此不使用浏览器的命令（list / validate / 测试其他模块）不依赖它。
"""
from __future__ import annotations

import asyncio
import contextlib
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

from core.store import atomic_write_json, file_lock, read_json


class LoginRequired(RuntimeError):
    """站点登录态已失效，需要用户重新执行 login 命令。"""

    def __init__(self, site: str, final_url: str):
        super().__init__(f"站点 {site} 需要重新登录（当前跳转到 {final_url}）")
        self.site = site
        self.final_url = final_url


@contextlib.contextmanager
def proactor_loop_policy():
    """Windows 下临时启用 Proactor 事件循环策略。

    playwright 需要用 asyncio 启动浏览器子进程；Streamlit/Tornado 常把全局策略设为
    SelectorEventLoop，此时 sync_playwright().start() 会抛 NotImplementedError。
    只在创建 playwright 自己的事件循环期间切换，结束后恢复原策略，不影响看板。
    非 Windows 不做任何事。
    """
    if sys.platform != "win32":
        yield
        return
    old = asyncio.get_event_loop_policy()
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    try:
        yield
    finally:
        asyncio.set_event_loop_policy(old)


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class BrowserSession:
    def __init__(
        self,
        profile_dir: Path,
        status_path: Path,
        launcher: Optional[Callable[[bool], tuple[Any, Any]]] = None,
    ) -> None:
        self.profile_dir = Path(profile_dir)
        self.status_path = Path(status_path)
        # launcher(headless) -> (playwright_handle, context)，测试时可注入假实现
        self._launcher = launcher or self._default_launcher
        # 锁文件与 profile 同级：profile.lock
        self._lock_target = self.profile_dir

    # ---------- 底层启动 ----------
    def _default_launcher(self, headless: bool) -> tuple[Any, Any]:
        from playwright.sync_api import sync_playwright  # 延迟导入

        self.profile_dir.mkdir(parents=True, exist_ok=True)
        with proactor_loop_policy():
            pw = sync_playwright().start()
        ctx = pw.chromium.launch_persistent_context(
            user_data_dir=str(self.profile_dir.resolve()),
            headless=headless,
            args=["--disable-blink-features=AutomationControlled"],
        )
        return pw, ctx

    def _open(self, headless: bool):
        pw, ctx = self._launcher(headless)
        return pw, ctx

    @staticmethod
    def _close(pw: Any, ctx: Any) -> None:
        try:
            ctx.close()
        finally:
            if hasattr(pw, "stop"):
                pw.stop()

    # ---------- 抓取 ----------
    def fetch_html(
        self,
        url: str,
        *,
        site: Optional[str] = None,
        headless: bool = True,
        wait_selector: Optional[str] = None,
        expired_pattern: Optional[str] = None,
        timeout_ms: int = 30000,
    ) -> str:
        with file_lock(self._lock_target):
            return self._fetch_unlocked(url, site, headless, wait_selector, expired_pattern, timeout_ms)

    def _fetch_unlocked(self, url, site, headless, wait_selector, expired_pattern, timeout_ms) -> str:
        pw, ctx = self._open(headless)
        try:
            page = ctx.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            if wait_selector:
                try:
                    page.wait_for_selector(wait_selector, timeout=timeout_ms)
                except Exception:  # noqa: BLE001 - 超时则用当前页面内容，由解析阶段判断是否有数据
                    pass
            final_url = page.url
            html = page.content()
        finally:
            self._close(pw, ctx)
        if site and expired_pattern and expired_pattern in final_url:
            self._record(site, status="expired", error=f"跳转到 {final_url}")
            raise LoginRequired(site, final_url)
        return html

    # ---------- 登录 ----------
    def interactive_login(
        self,
        site: str,
        login_url: str,
        check_url: str,
        expired_pattern: Optional[str] = None,
        wait_for_user: Callable[[str], None] = lambda prompt: input(prompt),
    ) -> str:
        """打开有头浏览器让用户登录，完成后用无头模式验证。返回最终状态字符串。"""
        with file_lock(self._lock_target):
            pw, ctx = self._open(headless=False)
            try:
                page = ctx.new_page()
                page.goto(login_url, wait_until="domcontentloaded")
                wait_for_user(f"请在打开的浏览器中完成 {site} 的登录，完成后回到这里按回车确认：")
            finally:
                self._close(pw, ctx)
            # 登录窗口已关闭，profile 已释放，再用无头模式验证登录是否生效
            try:
                self._fetch_unlocked(check_url, site, True, None, expired_pattern, 30000)
            except LoginRequired:
                return "expired"
            self._record(site, status="logged_in", login=True)
            return "logged_in"

    # ---------- 状态记录 ----------
    def _record(self, site: str, status: str, login: bool = False, error: Optional[str] = None) -> None:
        data = read_json(self.status_path, default={}) or {}
        entry = data.get(site, {})
        entry["status"] = status
        entry["last_checked_at"] = _now()
        if login:
            entry["last_login_at"] = entry["last_checked_at"]
        entry["last_error"] = error
        data[site] = entry
        atomic_write_json(self.status_path, data)

    def login_status(self) -> dict[str, Any]:
        return read_json(self.status_path, default={}) or {}
