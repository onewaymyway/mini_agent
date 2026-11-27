"""P14：探索用的有头渲染器。

- 每次渲染使用一次性 profile（临时目录），结束即删除，因此不携带任何登录态；
- 强制有头（headless=False），与共享登录态的 BrowserSession 完全分离；
- 只渲染公开页面：不点击、不提交表单、不输入内容（BrowserSession.fetch_html 只加载页面并取 HTML）。
"""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Any, Callable, Optional


class HeadedRenderer:
    def __init__(self, scratch_root: Path, *, timeout_ms: int = 30000,
                 session_factory: Optional[Callable[..., Any]] = None) -> None:
        self.scratch_root = Path(scratch_root)
        self.timeout_ms = timeout_ms
        self._factory = session_factory or self._default_factory

    @staticmethod
    def _default_factory(profile_dir: Path, status_path: Path):
        from core.browser_session import BrowserSession  # 延迟导入，测试与无浏览器环境可不加载 playwright
        return BrowserSession(profile_dir, status_path)

    def html(self, url: str) -> str:
        """渲染一次并返回 HTML。出错由调用方处理；无论成功与否都会删除临时 profile。"""
        self.scratch_root.mkdir(parents=True, exist_ok=True)
        work = Path(tempfile.mkdtemp(prefix="explore_", dir=str(self.scratch_root)))
        try:
            session = self._factory(work / "profile", work / "status.json")
            return session.fetch_html(url, headless=False, timeout_ms=self.timeout_ms)
        finally:
            shutil.rmtree(work, ignore_errors=True)
