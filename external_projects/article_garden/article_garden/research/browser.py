"""research/browser.py — 可选的浏览器渲染（备用抓取）。

用途：页面内容由脚本加载、普通抓取只得到空壳，或服务器暂时出错时，改用你本机已开启远程调试的 Chrome 渲染。
准备：
  1. 安装：pip install playwright
  2. 以远程调试端口启动 Chrome，例如：chrome --remote-debugging-port=9222
  3. 设置 → 调研（网站抓取）→ 打开「普通抓取失败时改用浏览器」，端口与上面一致。
边界：本模块只渲染调研流程已经允许抓取的网址（robots.txt 已先检查）；不用于绕过人工验证或访问控制。
"""

from __future__ import annotations

from typing import Callable, Optional, Tuple

RenderFn = Callable[[str], Tuple[Optional[str], str]]


def render_fn(port: int, timeout_sec: float = 30.0) -> RenderFn:
    """返回 render(url) -> (html 或 None, 错误说明)。只关闭自己打开的标签页，不关闭浏览器。"""

    def render(url: str) -> Tuple[Optional[str], str]:
        try:
            from playwright.sync_api import sync_playwright  # type: ignore
        except ImportError:
            return None, "未安装 playwright（请执行 pip install playwright）"
        try:
            with sync_playwright() as p:
                browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{port}", timeout=timeout_sec * 1000)
                context = browser.contexts[0] if browser.contexts else browser.new_context()
                page = context.new_page()
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=timeout_sec * 1000)
                    page.wait_for_timeout(2000)          # 给脚本一点时间加载正文
                    return page.content(), ""
                finally:
                    page.close()
        except Exception as exc:  # noqa: BLE001 — 原因展示给用户
            return None, f"{type(exc).__name__}: {str(exc)[:160]}（请确认 Chrome 已以端口 {port} 启动）"

    return render
