"""E2：托盘健康面板（PySide6）。只读展示，数据由 health.build_health_rows 生成。

窗口可见时每 2 秒刷新一次（只读内存与本地文件，不发网络请求，避免阻塞界面）。
"""

from __future__ import annotations

import time
from typing import Callable

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QDialog, QHBoxLayout, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout

from mini_agent_desktop.health import LEVEL_ERROR, LEVEL_INFO, LEVEL_OK, LEVEL_WARN, build_health_rows

_COLORS = {
    LEVEL_OK: QColor("#1a7f37"),
    LEVEL_WARN: QColor("#9a6700"),
    LEVEL_ERROR: QColor("#cf222e"),
    LEVEL_INFO: QColor("#57606a"),
}


class HealthWindow(QDialog):
    def __init__(self, snapshot_fn: Callable[[], dict], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("mini_agent 健康状态")
        self.setMinimumSize(680, 420)
        self._snapshot_fn = snapshot_fn

        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["项目", "状态"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)

        self.refresh_btn = QPushButton("立即刷新")
        self.refresh_btn.clicked.connect(self.refresh)
        self.close_btn = QPushButton("关闭")
        self.close_btn.clicked.connect(self.hide)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(self.refresh_btn)
        buttons.addWidget(self.close_btn)

        root = QVBoxLayout(self)
        root.addWidget(self.table)
        root.addLayout(buttons)

        self._timer = QTimer(self)
        self._timer.setInterval(2000)
        self._timer.timeout.connect(self.refresh)

    def showEvent(self, event) -> None:  # noqa: N802
        self.refresh()
        self._timer.start()
        super().showEvent(event)

    def hideEvent(self, event) -> None:  # noqa: N802
        self._timer.stop()
        super().hideEvent(event)

    def refresh(self) -> None:
        snapshot = dict(self._snapshot_fn() or {})
        snapshot.setdefault("now", time.time())
        rows = build_health_rows(snapshot)
        self.table.setRowCount(len(rows))
        for r, (label, value, level) in enumerate(rows):
            name_item = QTableWidgetItem(label)
            value_item = QTableWidgetItem(value)
            value_item.setForeground(_COLORS.get(level, _COLORS[LEVEL_INFO]))
            name_item.setFlags(name_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            value_item.setFlags(value_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(r, 0, name_item)
            self.table.setItem(r, 1, value_item)
        self.table.resizeColumnToContents(0)
