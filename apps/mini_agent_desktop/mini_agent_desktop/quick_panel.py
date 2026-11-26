"""快速面板（S2，PySide6）。

一个无边框、置顶的小窗口：输入一句话 → 发到专属 session → 显示结果；
可中断当前任务、查看专属 session 的最近任务（双击填回输入框）。

线程模型：所有网络调用都在工作线程里做（threading.Thread），结果通过 Qt 信号
回到主线程更新界面。Qt 会自动把"非主线程 emit、主线程接收"的信号排队投递。

Esc 关闭面板；Enter 发送；Shift+Enter 换行。
"""

from __future__ import annotations

import threading
from typing import Callable, Optional

import httpx
from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QCursor, QGuiApplication, QKeyEvent, QMouseEvent, QTextCursor
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPlainTextEdit,
    QCheckBox, QPushButton, QSizeGrip, QTextBrowser, QVBoxLayout, QWidget,
)

from mini_agent_desktop.context import ActiveWindow, format_context
from mini_agent_desktop.drop_stage import StagedBatch, build_message, default_drop_root, stage_paths
from mini_agent_desktop.panel_geometry import PANEL_HEIGHT, PANEL_WIDTH, Rect, fit_panel
from mini_agent_desktop.quick import STREAM_END_EVENTS, QuickSession, TaskOutcome, TokenAccumulator, run_task, state_label

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
    token = Signal(str)          # E3：流式 token 增量（流线程 emit）
    staged = Signal(object)      # E4：拖入文件复制完成（StagedBatch）


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
    def __init__(self, session_factory: Callable[[], QuickSession], parent=None,
                 drop_root=None) -> None:
        super().__init__(
            parent,
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint,
        )
        self._factory = session_factory
        self._drop_root = drop_root          # E4：测试可注入；默认见 drop_stage.default_drop_root()
        self._batch: Optional[StagedBatch] = None
        # E6：前台窗口上下文。reader 为 None 表示功能关闭；快照只在本面板内存中保存
        self._context_reader: Optional[Callable[[], Optional[ActiveWindow]]] = None
        self._captured: Optional[ActiveWindow] = None
        self._sig = _Signals()
        self._sig.staged.connect(self._on_staged)
        self._busy = False
        self._sig.status.connect(self._on_status)
        self._sig.done.connect(self._on_done)
        self._sig.failed.connect(self._on_failed)
        self._sig.recent.connect(self._on_recent)
        self._sig.info.connect(self._on_info)
        self._sig.token.connect(self._on_token)
        self._streaming = False          # E3：收尾后迟到的 token 不再追加，避免覆盖 Markdown 结果
        self._stream_stop: Optional[threading.Event] = None

        self.setWindowTitle("mini_agent 快速面板")
        self.setMinimumWidth(520)
        self.setMinimumHeight(PANEL_MIN_HEIGHT)

        self.input = _SubmitEdit(self._submit)
        self.send_btn = QPushButton("发送")
        self.send_btn.clicked.connect(self._submit)
        self.stop_btn = QPushButton("中断")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self._interrupt)
        self.context_box = QCheckBox("")
        self.context_box.setToolTip("发送时附上前台窗口的程序名与标题（不含页面或文件内容）")
        self.context_box.setVisible(False)
        self.attach_label = QLabel("")
        self.attach_label.setWordWrap(True)
        self.attach_label.setVisible(False)
        self.clear_attach_btn = QPushButton("清除附件")
        self.clear_attach_btn.setVisible(False)
        self.clear_attach_btn.clicked.connect(self._clear_attachment)
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
        # E4：拖放由面板统一接收；子控件不接收，否则文件 URL 会被当成文字插入
        self.setAcceptDrops(True)
        for w in (self.input, self.reply, self.recent):
            w.setAcceptDrops(False)

        buttons = QHBoxLayout()
        buttons.addWidget(self.clear_attach_btn)
        buttons.addStretch(1)
        buttons.addWidget(self.stop_btn)
        buttons.addWidget(self.send_btn)
        buttons.addWidget(self.close_btn)

        self.drag_bar = DragBar()
        root = QVBoxLayout(self)
        root.addWidget(self.drag_bar)
        root.addWidget(self.input)
        root.addLayout(buttons)
        root.addWidget(self.context_box)
        root.addWidget(self.attach_label)
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

    def set_context_reader(self, reader: Optional[Callable[[], Optional[ActiveWindow]]]) -> None:
        """E6：开启/关闭上下文感知。None = 关闭（托盘里取消勾选时调用）。"""
        self._context_reader = reader
        self.context_box.setVisible(reader is not None)
        if reader is None:
            self._captured = None

    def open_panel(self) -> None:
        # 必须在面板显示之前读取：面板自己获得焦点之后，前台窗口就不再是用户原来的窗口
        self._captured = self._read_context()
        self._refresh_context_box()
        self._place_on_screen()
        self.show()
        self.raise_()
        self.activateWindow()
        self.input.setFocus()
        self._start_worker(self._load_recent)

    def _read_context(self) -> Optional[ActiveWindow]:
        if self._context_reader is None:
            return None
        try:
            return self._context_reader()
        except Exception:  # noqa: BLE001 - 读取失败只影响附加信息，不能阻止打开面板
            return None

    def _refresh_context_box(self) -> None:
        if self._context_reader is None:
            return
        win = self._captured
        if win is None:
            self.context_box.setText("附带当前窗口：无法读取")
            self.context_box.setChecked(False)
            self.context_box.setEnabled(False)
            return
        self.context_box.setEnabled(True)
        self.context_box.setChecked(True)
        self.context_box.setText(f"附带当前窗口：{win.exe}" + (f" · {win.title}" if win.title else ""))

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

    # ── E4：拖放文件 ──────────────────────────────────────────────────────
    def dragEnterEvent(self, event) -> None:  # noqa: N802
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:  # noqa: N802
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:  # noqa: N802
        paths = [u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
        if not paths:
            event.ignore()
            return
        event.acceptProposedAction()
        self.status.setText("正在复制到临时目录…")
        self._start_worker(lambda: self._stage_worker(paths))

    def _stage_worker(self, paths: list[str]) -> None:
        root = self._drop_root or default_drop_root()
        try:
            batch = stage_paths(paths, root)
        except Exception as exc:  # noqa: BLE001 - 复制失败要回到界面，不能让线程静默死掉
            batch = StagedBatch(skipped=[("", f"暂存失败：{exc}")])
        self._sig.staged.emit(batch)

    def _on_staged(self, batch: StagedBatch) -> None:
        if batch.dir is None or not batch.files:
            reason = batch.skipped[0][1] if batch.skipped else "没有可复制的文件"
            self.status.setText(f"未附加文件：{reason}")
            return
        self._batch = batch
        self._refresh_attachment()
        note = f"，{len(batch.skipped)} 项未复制" if batch.skipped else ""
        self.status.setText(f"已附加 {len(batch.files)} 个文件{note}，发送时一并告诉 agent")

    def _refresh_attachment(self) -> None:
        batch = self._batch
        has = bool(batch and batch.files)
        self.attach_label.setVisible(has)
        self.clear_attach_btn.setVisible(has)
        if has:
            self.attach_label.setText(f"📎 {len(batch.files)} 个文件（临时副本）：{batch.dir}")

    def _clear_attachment(self) -> None:
        self._batch = None
        self._refresh_attachment()
        self.status.setText("已清除附件（临时副本会在 24 小时后自动删除）")

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
        batch = self._batch
        if not text and not (batch and batch.files):
            return
        payload = build_message(text, batch) if batch and batch.files else text
        if self._context_reader is not None and self.context_box.isEnabled() \
                and self.context_box.isChecked() and self._captured is not None:
            payload = format_context(self._captured) + "\n" + payload
        self._captured = None               # 上下文快照随本次发送用完即弃
        self._batch = None                  # 附件随本次消息发出，不再保留
        self._refresh_attachment()
        self._set_busy(True)
        self.reply.clear()
        self._streaming = True
        self.status.setText(state_label("submitted"))
        self._start_worker(lambda: self._send_worker(payload))

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
            outcome: TaskOutcome = run_task(session, text, on_status=self._sig.status.emit,
                                            on_turn=self._start_stream)
            self._sig.done.emit(outcome)
        except Exception as exc:  # noqa: BLE001 - 网络/解析错误都要回到界面
            self._sig.failed.emit(describe_error(exc))
        finally:
            if self._stream_stop is not None:
                self._stream_stop.set()
            if session is not None:
                session.close()
            self._start_worker(self._load_recent)

    def _start_stream(self, turn_id: str) -> None:
        """E3：为本次 turn 开一条流线程。失败只影响增量显示，最终结果仍由轮询保证。"""
        stop = threading.Event()
        self._stream_stop = stop
        threading.Thread(target=self._stream_worker, args=(turn_id, stop),
                         name="quick-panel-stream", daemon=True).start()

    def _stream_worker(self, turn_id: str, stop: threading.Event) -> None:
        session = None
        try:
            session = self._factory()
            acc = TokenAccumulator()
            for ev in session.api.stream_turn(turn_id, stop):
                chunk = acc.feed(ev)
                if chunk:
                    self._sig.token.emit(chunk)
                if ev.event in STREAM_END_EVENTS:
                    break
        except Exception:  # noqa: BLE001 - 流不可用时静默回退为轮询
            pass
        finally:
            if session is not None:
                session.close()

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

    def _on_token(self, chunk: str) -> None:
        if not self._streaming:
            return
        self.reply.moveCursor(QTextCursor.MoveOperation.End)
        self.reply.insertPlainText(chunk)

    def _on_done(self, outcome: TaskOutcome) -> None:
        self._streaming = False
        self._set_busy(False)
        self.status.setText(state_label(outcome.state))
        if outcome.reply:
            self.reply.setMarkdown(outcome.reply)
        if outcome.state in ("done", "interrupted", "error"):
            self.input.clear()

    def _on_failed(self, message: str) -> None:
        self._streaming = False
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
