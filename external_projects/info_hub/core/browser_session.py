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
from urllib.parse import parse_qs, urlparse

from core.store import atomic_write_json, file_lock, read_json


class LoginRequired(RuntimeError):
    """站点登录态已失效，需要用户重新执行 login 命令。"""

    def __init__(self, site: str, final_url: str):
        super().__init__(f"站点 {site} 需要重新登录（当前跳转到 {final_url}）")
        self.site = site
        self.final_url = final_url


class BrowserNotRunning(RuntimeError):
    """CDP 模式下专用浏览器未运行，或未以调试端口启动。"""


class RiskControlBlocked(RuntimeError):
    """站点风控拦截（非登录失效）：需要换可靠网络、或改用有头浏览器后重试。"""

    def __init__(self, site: str, final_url: str, error_code: str, message: str):
        super().__init__(
            f"站点 {site} 被风控拦截（错误码 {error_code}：{message}），"
            "请切换可靠网络，或改用有头浏览器后重试"
        )
        self.site = site
        self.final_url = final_url
        self.error_code = error_code


def classify_landing(url: str, expired_pattern: Optional[str]) -> Optional[tuple[str, str, str]]:
    """判断最终落地 URL 属于哪种异常页面。

    返回 None（正常页面）、("blocked", error_code, error_msg)（风控拦截）
    或 ("expired", "", "")（登录失效）。

    注意：登录失效只匹配 URL 的 path 部分，不匹配 query。风控错误页的 query 里
    常带有 rejectUrl=.../login/... 之类的字段，整串匹配会把风控误判为登录失效。
    """
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    if "error_code" in query:
        code = (query.get("error_code") or [""])[0]
        msg = (query.get("error_msg") or [""])[0]
        return ("blocked", code, msg)
    if expired_pattern and expired_pattern in parsed.path:
        return ("expired", "", "")
    return None


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


def cdp_available(port: int, timeout: float = 1.0) -> bool:
    """专用浏览器的调试端口是否可用（/json/version 可访问）。"""
    import json
    import urllib.request
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=timeout) as resp:
            return "webSocketDebuggerUrl" in json.loads(resp.read().decode("utf-8"))
    except (OSError, ValueError):
        return False


def _open_background_page(browser: Any, context: Any, timeout_ms: int = 5000) -> Any:
    """在 context 中创建抓取用标签页，尽量不切换前台。

    用 Playwright 的 expect_page 等待新页面：它会在等待期间处理浏览器事件。
    不能用 time.sleep 轮询 context.pages —— 同步 API 下事件只在 Playwright 调用中被处理，
    轮询会永远看不到新页面。若后台创建失败（例如浏览器版本不支持 background 参数），
    退回普通 new_page，抓取仍可完成，只是标签页会被激活。
    """
    try:
        cdp = browser.new_browser_cdp_session()
        with context.expect_page(timeout=timeout_ms) as info:
            cdp.send("Target.createTarget", {"url": "about:blank", "background": True})
        return info.value
    except Exception:  # noqa: BLE001 - 后台创建不可用时退回普通标签页
        return context.new_page()


class BrowserSession:
    def __init__(
        self,
        profile_dir: Path,
        status_path: Path,
        launcher: Optional[Callable[[bool], tuple[Any, Any]]] = None,
        *,
        mode: str = "launch",
        cdp_port: int = 9223,
    ) -> None:
        # mode="launch"：Playwright 自行启动独立浏览器（旧方式）；
        # mode="cdp"：附着到专用浏览器（需以调试端口启动），抓取与阅读共用同一个浏览器。
        if mode not in ("launch", "cdp"):
            raise ValueError(f"未知抓取模式：{mode}")
        self.mode = mode
        self.cdp_port = cdp_port
        self.profile_dir = Path(profile_dir)
        self.status_path = Path(status_path)
        # launcher(headless) -> (playwright_handle, context)，测试时可注入假实现
        self._launcher = launcher or self._default_launcher
        # 锁文件与 profile 同级：profile.lock
        self._lock_target = self.profile_dir
        self.prefer_system_chrome = True

    # ---------- 底层启动 ----------
    def _default_launcher(self, headless: bool) -> tuple[Any, Any]:
        from playwright.sync_api import sync_playwright  # 延迟导入

        self.profile_dir.mkdir(parents=True, exist_ok=True)
        with proactor_loop_policy():
            pw = sync_playwright().start()
        options: dict[str, Any] = dict(
            user_data_dir=str(self.profile_dir.resolve()),
            headless=headless,
            args=["--disable-blink-features=AutomationControlled"],
            # 去掉 Playwright 默认注入的 --enable-automation，减少自动化特征
            ignore_default_args=["--enable-automation"],
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
            viewport={"width": 1366, "height": 900},
        )
        ctx = None
        # 优先用本机已安装的 Chrome（指纹与日常浏览器一致）；未安装时退回 Playwright 自带 Chromium
        if self.prefer_system_chrome:
            try:
                ctx = pw.chromium.launch_persistent_context(channel="chrome", **options)
            except Exception:  # noqa: BLE001 - 本机无 Chrome，退回自带 Chromium
                ctx = None
        if ctx is None:
            try:
                ctx = pw.chromium.launch_persistent_context(**options)
            except Exception:
                pw.stop()
                raise
        # 隐藏 navigator.webdriver 标记（无头/自动化环境会暴露它）
        ctx.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined});")
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
        if self.mode == "cdp":
            return self._fetch_cdp(url, site, wait_selector, expired_pattern, timeout_ms)
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
        landing = classify_landing(final_url, expired_pattern) if site else None
        if landing is not None:
            kind, code, msg = landing
            if kind == "blocked":
                self._record(site, status="blocked", error=f"风控拦截 {code}：{msg}")
                raise RiskControlBlocked(site, final_url, code, msg)
            self._record(site, status="expired", error=f"跳转到 {final_url}")
            raise LoginRequired(site, final_url)
        return html

    def _fetch_cdp(self, url, site, wait_selector, expired_pattern, timeout_ms) -> str:
        """附着到专用浏览器抓取页面：后台标签打开、取内容、关闭标签，并断开连接（不关闭浏览器）。"""
        if not cdp_available(self.cdp_port):
            raise BrowserNotRunning(
                f"专用浏览器未运行或未开启调试端口 {self.cdp_port}。请在看板“服务管理”中启动专用浏览器")
        from playwright.sync_api import sync_playwright  # 延迟导入
        with proactor_loop_policy():
            pw = sync_playwright().start()
        browser = None
        try:
            browser = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{self.cdp_port}")
            context = browser.contexts[0] if browser.contexts else browser.new_context()
            page = _open_background_page(browser, context)
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                if wait_selector:
                    try:
                        page.wait_for_selector(wait_selector, timeout=timeout_ms)
                    except Exception:  # noqa: BLE001 - 超时则用当前内容，由解析阶段判断
                        pass
                final_url = page.url
                html = page.content()
            finally:
                try:
                    page.close()
                except Exception:  # noqa: BLE001
                    pass
        finally:
            if browser is not None:
                try:
                    browser.close()  # 对 CDP 连接而言只是断开，不会关闭用户的浏览器
                except Exception:  # noqa: BLE001
                    pass
            pw.stop()
        landing = classify_landing(final_url, expired_pattern) if site else None
        if landing is not None:
            kind, code, msg = landing
            if kind == "blocked":
                self._record(site, status="blocked", error=f"风控拦截 {code}：{msg}")
                raise RiskControlBlocked(site, final_url, code, msg)
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
