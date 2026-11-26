"""快速面板（S2，PySide6）。

一个无边框、置顶的小窗口：输入一句话 → 发到专属 session → 显示结果；
可中断当前任务、查看专属 session 的最近任务（双击填回输入框）。

线程模型：所有网络调用都在工作线程里做（threading.Thread），结果通过 Qt 信号
回到主线程更新界面。Qt 会自动把"非主线程 emit、主线程接收"的信号排队投递。

Esc 关闭面板；Enter 发送；Shift+Enter 换行。
"""

from __future__ import annotations

import threading
from typing import Callable

import httpx
from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QCursor, QGuiApplication, QKeyEvent, QMouseEvent
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPlainTextEdit,
    QPushButton, QSizeGrip, QTextBrowser, QVBoxLayout, QWidget,
)

from mini_agent_desktop.panel_geometry import PANEL_HEIGHT, PANEL_WIDTH, Rect, fit_panel
from mini_agent_desktop.quick import QuickSession, TaskOutcome, run_task, state_label

PANEL_MIN_HEIGHT = 360     # 低于此高度时布局会挤压输入区，不再继续缩小


class QuickHotkeyBridge(QObject):
    """把热键线程里的回调转成 Qt 信号（只能在主线程 connect 到面板的方法上）。"""

    requested = Signal()


class _Signals(QObject):
    status = Signal(str)
    done = Signal(object)        # TaskOutcome
    failed = Signal(str)
    recent = Signal(object)      # list[dict]
    info = Signal(str)


class DragBar(QLabel):
    """无边框窗口没有标题栏，这里用一条把手替代：按住拖动即可移动整个面板。

    不把整个窗口做成拖动区，是因为输入框、结果区等子控件会吞掉鼠标事件，
    整窗拖动会与文本选择、滚动冲突。
    """

    def __init__(self, parent=None) -> None:
        super().__init__("⠿  拖动此处移动窗口", parent)
        self.setCursor(Qt.CursorShape.SizeAllCursor)
        self.setToolTip("按住拖动移动快速面板")
        self.setStyleSheet("color: #888; padding: 2px 0;")
        self._grab_offset = None   # 按下时鼠标相对窗口左上角的偏移（全局坐标系）

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            win = self.window()
            self._grab_offset = event.globalPosition().toPoint() - win.frameGeometry().topLeft()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._grab_offset is not None and (event.buttons() & Qt.MouseButton.LeftButton):
            self.window().move(event.globalPosition().toPoint() - self._grab_offset)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        self._grab_offset = None
        super().mouseReleaseEvent(event)


class _SubmitEdit(QPlainTextEdit):
    """Enter 发送，Shift+Enter 换行。"""

    def __init__(self, on_submit: Callable[[], None], parent=None) -> None:
        super().__init__(parent)
        self._on_submit = on_submit
        self.setPlaceholderText("想让 mini_agent 做什么？  Enter 发送 · Shift+Enter 换行 · Esc 关闭")
        self.setFixedHeight(88)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802 - Qt 命名
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not (
            event.modifiers() & Qt.KeyboardModifier.ShiftModifier
        ):
            self._on_submit()
            return
        super().keyPressEvent(event)


def describe_error(exc: BaseException) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        if code in (401, 403):
            return "认证失败：token 无效或缺失（.agent/agent_api.key）"
        return f"请求失败：HTTP {code}"
    if isinstance(exc, httpx.HTTPError):
        return "无法连接 daemon（可在托盘菜单中启动 Agent）"
    if isinstance(exc, ValueError):
        return str(exc)
    return f"出错：{exc}"


class QuickPanel(QWidget):
    def __init__(self, session_factory: Callable[[], QuickSession], parent=None) -> None:
        super().__init__(
            parent,
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint,
        )
        self._factory = session_factory
        self._sig = _Signals()
        self._busy = False
        self._sig.status.connect(self._on_status)
        self._sig.done.connect(self._on_done)
        self._sig.failed.connect(self._on_failed)
        self._sig.recent.connect(self._on_recent)
        self._sig.info.connect(self._on_info)

        self.setWindowTitle("mini_agent 快速面板")
        self.setMinimumWidth(520)
        self.setMinimumHeight(PANEL_MIN_HEIGHT)

        self.input = _SubmitEdit(self._submit)
        self.send_btn = QPushButton("发送")
        self.send_btn.clicked.connect(self._submit)
        self.stop_btn = QPushButton("中断")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self._interrupt)
        self.close_btn = QPushButton("关闭")
        self.close_btn.clicked.connect(self.hide)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.reply = QTextBrowser()
        self.reply.setOpenExternalLinks(True)
        self.reply.setMinimumHeight(120)
        self.recent = QListWidget()
        self.recent.setMinimumHeight(60)
        self.recent.setMaximumHeight(120)
        self.recent.itemDoubleClicked.connect(self._fill_from_recent)
        self.recent_title = QLabel("最近任务（双击填回输入框）")

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(self.stop_btn)
        buttons.addWidget(self.send_btn)
        buttons.addWidget(self.close_btn)

        self.drag_bar = DragBar()
        root = QVBoxLayout(self)
        root.addWidget(self.drag_bar)
        root.addWidget(self.input)
        root.addLayout(buttons)
        root.addWidget(self.status)
        root.addWidget(self.reply, 1)
        root.addWidget(self.recent_title)
        root.addWidget(self.recent)
        # 无边框窗口没有系统缩放边框，用右下角的 SizeGrip 代替
        self.size_grip = QSizeGrip(self)
        root.addWidget(self.size_grip, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom)

    # ── 显示/隐藏 ─────────────────────────────────────────────────────────
    def toggle(self) -> None:
        if self.isVisible():
            self.hide()
        else:
            self.open_panel()

    def open_panel(self) -> None:
        self._place_on_screen()
        self.show()
        self.raise_()
        self.activateWindow()
        self.input.setFocus()
        self._start_worker(self._load_recent)

    def _place_on_screen(self) -> None:
        """尺寸与位置都限制在鼠标所在屏幕的可用区域（扣掉任务栏）以内，保证整窗可见。"""
        pos = QCursor.pos()
        screen = QGuiApplication.screenAt(pos) or QGuiApplication.primaryScreen()
        if screen is None:          # 无屏幕信息（极少见）：退回默认尺寸，不做夹紧
            self.resize(PANEL_WIDTH, PANEL_HEIGHT)
            self.move(pos.x() - PANEL_WIDTH // 2, pos.y() - 60)
            return
        avail = screen.availableGeometry()
        r = fit_panel((pos.x(), pos.y()),
                      Rect(avail.x(), avail.y(), avail.width(), avail.height()))
        self.setGeometry(r.x, r.y, r.w, r.h)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_Escape:
            self.hide()
            return
        super().keyPressEvent(event)

    # ── 动作（主线程）────────────────────────────────────────────────────
    def _submit(self) -> None:
        if self._busy:
            return
        text = self.input.toPlainText().strip()
        if not text:
            return
        self._set_busy(True)
        self.reply.clear()
        self.status.setText(state_label("submitted"))
        self._start_worker(lambda: self._send_worker(text))

    def _interrupt(self) -> None:
        self._start_worker(self._interrupt_worker)
        self.stop_btn.setEnabled(False)

    def _fill_from_recent(self, item: QListWidgetItem) -> None:
        self.input.setPlainText(item.data(Qt.ItemDataRole.UserRole) or item.text())
        self.input.setFocus()

    # ── 工作线程 ─────────────────────────────────────────────────────────
    def _start_worker(self, fn: Callable[[], None]) -> None:
        threading.Thread(target=fn, name="quick-panel-worker", daemon=True).start()

    def _send_worker(self, text: str) -> None:
        session = None
        try:
            session = self._factory()
            outcome: TaskOutcome = run_task(session, text, on_status=self._sig.status.emit)
            self._sig.done.emit(outcome)
        except Exception as exc:  # noqa: BLE001 - 网络/解析错误都要回到界面
            self._sig.failed.emit(describe_error(exc))
        finally:
            if session is not None:
                session.close()
            self._start_worker(self._load_recent)

    def _interrupt_worker(self) -> None:
        session = None
        try:
            session = self._factory()
            session.interrupt()
            self._sig.info.emit("已请求中断，等待当前步骤结束…")
        except Exception as exc:  # noqa: BLE001
            self._sig.info.emit(describe_error(exc))
        finally:
            if session is not None:
                session.close()

    def _load_recent(self) -> None:
        session = None
        try:
            session = self._factory()
            self._sig.recent.emit(session.recent_turns())
        except Exception as exc:  # noqa: BLE001
            self._sig.info.emit(describe_error(exc))
        finally:
            if session is not None:
                session.close()

    # ── 信号回调（主线程）────────────────────────────────────────────────
    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.send_btn.setEnabled(not busy)
        self.stop_btn.setEnabled(busy)

    def _on_status(self, state: str) -> None:
        self.status.setText(state_label(state))

    def _on_done(self, outcome: TaskOutcome) -> None:
        self._set_busy(False)
        self.status.setText(state_label(outcome.state))
        if outcome.reply:
            self.reply.setMarkdown(outcome.reply)
        if outcome.state in ("done", "interrupted", "error"):
            self.input.clear()

    def _on_failed(self, message: str) -> None:
        self._set_busy(False)
        self.status.setText(message)

    def _on_info(self, message: str) -> None:
        self.status.setText(message)

    def _on_recent(self, rows: list) -> None:
        self.recent.clear()
        for row in rows:
            text = row.get("input", "").replace("\n", " ")
            label = f"[{state_label(row.get('state', ''))}] {text[:60]}"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, row.get("input", ""))
            self.recent.addItem(item)
