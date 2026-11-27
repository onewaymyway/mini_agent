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

from core.explore_json_api import MAX_DEPTH, MIN_ITEMS, observe_json

MAX_RESPONSES = 40   # 单次渲染最多分析的 JSON 响应数


class HeadedRenderer:
    def __init__(self, scratch_root: Path, *, timeout_ms: int = 30000,
                 session_factory: Optional[Callable[..., Any]] = None,
                 max_responses: int = MAX_RESPONSES, min_items: int = MIN_ITEMS,
                 max_depth: int = MAX_DEPTH) -> None:
        self.scratch_root = Path(scratch_root)
        self.timeout_ms = timeout_ms
        self.max_responses = max_responses      # P19-3：来自看板参数
        self.min_items = min_items
        self.max_depth = max_depth
        self._factory = session_factory or self._default_factory
        self.last_observations: list[dict[str, Any]] = []   # 最近一次渲染观察到的列表型接口（只含结论，不含正文）

    @staticmethod
    def _default_factory(profile_dir: Path, status_path: Path):
        from core.browser_session import BrowserSession  # 延迟导入，测试与无浏览器环境可不加载 playwright
        return BrowserSession(profile_dir, status_path)

    def _on_response(self, response: Any) -> None:
        """分析 JSON 响应：当场判断是否为列表型接口，正文不保存。"""
        if len(self.last_observations) >= self.max_responses:
            return
        try:
            if response.status != 200:
                return
            ctype = str((response.headers or {}).get("content-type", "")).lower()
            if "json" not in ctype:
                return
            payload = response.json()
        except Exception:  # noqa: BLE001 - 非 JSON、读取失败等一律忽略
            return
        desc = observe_json(str(response.url), payload, min_items=self.min_items, max_depth=self.max_depth)
        if desc and all(d["endpoint"] != desc["endpoint"] for d in self.last_observations):
            self.last_observations.append(desc)

    def html(self, url: str) -> str:
        """渲染一次并返回 HTML；观察到的列表型接口保存在 last_observations。无论成功与否都会删除临时 profile。"""
        self.scratch_root.mkdir(parents=True, exist_ok=True)
        self.last_observations = []
        work = Path(tempfile.mkdtemp(prefix="explore_", dir=str(self.scratch_root)))
        try:
            session = self._factory(work / "profile", work / "status.json")
            return session.fetch_html(url, headless=False, timeout_ms=self.timeout_ms,
                                      on_response=self._on_response)
        finally:
            shutil.rmtree(work, ignore_errors=True)
