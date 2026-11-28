"""digest_window.py — 桌面端「今日日报」查看窗口。

数据来自 GET /v1/digest/daily（返回 Markdown 正文），用 QTextBrowser.setMarkdown 渲染。
支持前后翻日期，以及跳回最新一份。
"""

from __future__ import annotations

import datetime as _dt
from typing import Callable, Optional

from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QTextBrowser, QVBoxLayout, QWidget


class DigestWindow(QWidget):
    def __init__(self, fetch: Callable[[Optional[str]], dict]) -> None:
        super().__init__()
        self._fetch = fetch
        self._day: Optional[str] = None
        self.setWindowTitle("每日融合日报")
        self.resize(720, 640)

        bar = QHBoxLayout()
        self.prev_btn = QPushButton("◀ 前一天")
        self.latest_btn = QPushButton("最新")
        self.next_btn = QPushButton("后一天 ▶")
        self.day_label = QLabel("")
        bar.addWidget(self.prev_btn)
        bar.addWidget(self.day_label, 1)
        bar.addWidget(self.latest_btn)
        bar.addWidget(self.next_btn)

        self.view = QTextBrowser()
        self.view.setOpenExternalLinks(True)

        root = QVBoxLayout(self)
        root.addLayout(bar)
        root.addWidget(self.view, 1)

        self.prev_btn.clicked.connect(lambda: self._shift(-1))
        self.next_btn.clicked.connect(lambda: self._shift(1))
        self.latest_btn.clicked.connect(lambda: self.load(None))

    @property
    def current_day(self) -> Optional[str]:
        return self._day

    def load(self, day: Optional[str] = None) -> None:
        try:
            resp = self._fetch(day) or {}
        except Exception as exc:  # noqa: BLE001
            self._day = None
            self.day_label.setText("读取失败")
            self.view.setPlainText(f"读取日报失败：{exc}")
            return
        digest = resp.get("digest")
        if not digest:
            self._day = day
            self.day_label.setText(day or "暂无日报")
            msg = "这一天没有日报。" if day else "还没有生成过日报（每天 22:00 自动生成）。"
            self.view.setPlainText(msg)
            return
        self._day = digest.get("day")
        self.day_label.setText(self._day or "")
        self.view.setMarkdown(resp.get("markdown") or "（日报正文为空）")

    def _shift(self, delta: int) -> None:
        base = self._day or _dt.date.today().isoformat()
        target = _dt.date.fromisoformat(base) + _dt.timedelta(days=delta)
        self.load(target.isoformat())
