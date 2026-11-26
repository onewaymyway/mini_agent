"""E5：目标与定时任务窗口（PySide6）。

- 网络调用全部在工作线程完成，结果通过信号回到主线程；
- 目标页只读；定时任务页可暂停/恢复（PUT enabled），操作后重新拉取。
"""

from __future__ import annotations

import threading
from typing import Callable

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QHeaderView, QLabel, QPushButton, QTableWidget, QTableWidgetItem,
    QTabWidget, QVBoxLayout, QWidget,
)

from mini_agent_desktop.workboard import summarize_cron, summarize_objectives

FetchFn = Callable[[], tuple]            # () -> (objectives: list[dict], jobs: list[dict])
ToggleFn = Callable[[str, bool], None]   # (job_id, enabled)


class _Bridge(QObject):
    loaded = Signal(object)
    failed = Signal(str)
    toggled = Signal(str)


def _table(headers: list[str]) -> QTableWidget:
    t = QTableWidget(0, len(headers))
    t.setHorizontalHeaderLabels(headers)
    t.verticalHeader().setVisible(False)
    t.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    t.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
    t.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
    t.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
    return t


class WorkboardWindow(QDialog):
    def __init__(self, fetch_fn: FetchFn, toggle_fn: ToggleFn, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("mini_agent 目标与定时任务")
        self.setMinimumSize(760, 460)
        self._fetch = fetch_fn
        self._toggle = toggle_fn
        self._bridge = _Bridge()
        self._bridge.loaded.connect(self._on_loaded)
        self._bridge.failed.connect(self._on_failed)
        self._bridge.toggled.connect(lambda _m: self.refresh())

        self.tabs = QTabWidget()
        self.goal_table = _table(["状态", "目标", "进展"])
        self.cron_table = _table(["状态", "任务", "下次运行", "执行"])
        self.tabs.addTab(self.goal_table, "目标")
        self.tabs.addTab(self.cron_table, "定时任务")

        self.toggle_btn = QPushButton("暂停 / 恢复所选任务")
        self.toggle_btn.clicked.connect(self._toggle_selected)
        self.refresh_btn = QPushButton("刷新")
        self.refresh_btn.clicked.connect(self.refresh)
        self.status = QLabel("")
        self.status.setWordWrap(True)

        buttons = QHBoxLayout()
        buttons.addWidget(self.toggle_btn)
        buttons.addStretch(1)
        buttons.addWidget(self.refresh_btn)

        root = QVBoxLayout(self)
        root.addWidget(self.tabs)
        root.addLayout(buttons)
        root.addWidget(self.status)
        self._jobs: list = []
        self.tabs.currentChanged.connect(lambda _i: self._update_buttons())
        self.cron_table.itemSelectionChanged.connect(self._update_buttons)

    def showEvent(self, event) -> None:  # noqa: N802
        self.refresh()
        super().showEvent(event)

    # ── 加载 ──────────────────────────────────────────────────────────────
    def refresh(self) -> None:
        self.status.setText("正在加载…")
        threading.Thread(target=self._load_worker, name="workboard-load", daemon=True).start()

    def _load_worker(self) -> None:
        try:
            self._bridge.loaded.emit(self._fetch())
        except Exception as exc:  # noqa: BLE001 - 网络错误回到界面
            self._bridge.failed.emit(f"加载失败：{exc}")

    def _on_failed(self, message: str) -> None:
        self.status.setText(message)

    def _on_loaded(self, payload) -> None:
        objectives, jobs = payload
        self._jobs = list(jobs)
        obj_rows = summarize_objectives(objectives)
        self.goal_table.setRowCount(len(obj_rows))
        for r, row in enumerate(obj_rows):
            for c, text in enumerate((row.status_label, row.title, row.progress)):
                item = QTableWidgetItem(text)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                item.setData(Qt.ItemDataRole.UserRole, row.id)
                self.goal_table.setItem(r, c, item)

        cron_rows = summarize_cron(jobs)
        self.cron_table.setRowCount(len(cron_rows))
        for r, row in enumerate(cron_rows):
            values = ("已启用" if row.enabled else "已停用", row.name, row.next_run, row.phase_label)
            for c, text in enumerate(values):
                item = QTableWidgetItem(text)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                item.setData(Qt.ItemDataRole.UserRole, row.id)
                self.cron_table.setItem(r, c, item)
        self.status.setText(f"目标 {len(obj_rows)} 项，定时任务 {len(cron_rows)} 项")
        self._update_buttons()

    # ── 启停 ──────────────────────────────────────────────────────────────
    def _selected_job(self):
        rows = self.cron_table.selectionModel().selectedRows()
        if not rows:
            return None
        job_id = self.cron_table.item(rows[0].row(), 0).data(Qt.ItemDataRole.UserRole)
        return next((j for j in self._jobs if str(j.get("id")) == str(job_id)), None)

    def _update_buttons(self) -> None:
        on_cron = self.tabs.currentWidget() is self.cron_table
        self.toggle_btn.setEnabled(on_cron and self._selected_job() is not None)

    def _toggle_selected(self) -> None:
        job = self._selected_job()
        if job is None:
            return
        enabled = not bool(job.get("enabled"))
        job_id = str(job.get("id"))

        def work() -> None:
            try:
                self._toggle(job_id, enabled)
                self._bridge.toggled.emit("ok")
            except Exception as exc:  # noqa: BLE001
                self._bridge.failed.emit(f"操作失败：{exc}")

        self.status.setText("正在更新…")
        threading.Thread(target=work, name="workboard-toggle", daemon=True).start()
