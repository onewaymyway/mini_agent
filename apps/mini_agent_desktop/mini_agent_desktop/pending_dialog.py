"""S3：待处理事项面板（PySide6）。

列出三类等你回答的事项，并直接在面板里处理：
  - 工具审批：批准 / 拒绝（mode=once）
  - 交互式提问 ask_user 系列：文字输入、是/否、选项按钮
  - cron 异步提问：选项按钮或文字回答

打开方式：点击 Toast 气泡、托盘菜单\"待处理事项…\"。被其它客户端（CLI/看板）
先处理掉的事项，提交时会得到 404，面板会提示并自动刷新。

线程模型同 quick_panel.py：网络调用全部在工作线程，结果经 Qt 信号回主线程。
"""

from __future__ import annotations

import threading
from typing import Callable

import httpx
from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (
    QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea,
    QVBoxLayout, QWidget,
)

from mini_agent_desktop.attention import PendingItem, answer_item, collect_pending
from mini_agent_desktop.quick import QuickSession


class _Signals(QObject):
    loaded = Signal(object)     # list[PendingItem]
    message = Signal(str)


class AttentionBridge(QObject):
    """控制器线程 → 主线程的 attention 事件桥（信号自动排队投递）。"""

    received = Signal(object)   # dict


def describe_answer_error(exc: BaseException) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        if code == 404:
            return "这项已经被处理（可能在别处回答了），列表已刷新"
        if code in (401, 403):
            return "认证失败：token 无效或缺失"
        return f"提交失败：HTTP {code}"
    if isinstance(exc, httpx.HTTPError):
        return "无法连接 daemon"
    if isinstance(exc, ValueError):
        return str(exc)
    return f"出错：{exc}"


class PendingDialog(QDialog):
    def __init__(self, session_factory: Callable[[], QuickSession], parent=None) -> None:
        super().__init__(parent, Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint)
        self._factory = session_factory
        self._sig = _Signals()
        self._sig.loaded.connect(self._render)
        self._sig.message.connect(self._show_message)

        self.setWindowTitle("待处理事项")
        self.setMinimumWidth(480)
        self.resize(520, 420)

        self.hint = QLabel("")
        self.hint.setWordWrap(True)
        self.list_host = QWidget()
        self.list_layout = QVBoxLayout(self.list_host)
        self.list_layout.setContentsMargins(4, 4, 4, 4)
        self.list_layout.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.list_host)
        refresh = QPushButton("刷新")
        refresh.clicked.connect(self.reload)
        close = QPushButton("关闭")
        close.clicked.connect(self.hide)
        bar = QHBoxLayout()
        bar.addWidget(self.hint, 1)
        bar.addWidget(refresh)
        bar.addWidget(close)
        root = QVBoxLayout(self)
        root.addLayout(bar)
        root.addWidget(scroll, 1)

    # ── 显示 ─────────────────────────────────────────────────────────────
    def open_dialog(self) -> None:
        self.show()
        self.raise_()
        self.activateWindow()
        self.reload()

    def reload(self) -> None:
        self.hint.setText("正在加载…")
        threading.Thread(target=self._load_worker, name="pending-load", daemon=True).start()

    def _load_worker(self) -> None:
        session = None
        try:
            session = self._factory()
            self._sig.loaded.emit(collect_pending(session.api))
        except Exception as exc:  # noqa: BLE001
            self._sig.message.emit(describe_answer_error(exc))
        finally:
            if session is not None:
                session.close()

    def _submit(self, item: PendingItem, **kwargs) -> None:
        self.hint.setText("提交中…")
        threading.Thread(
            target=self._submit_worker, args=(item, kwargs), name="pending-answer", daemon=True
        ).start()

    def _submit_worker(self, item: PendingItem, kwargs: dict) -> None:
        session = None
        try:
            session = self._factory()
            answer_item(session.api, item, **kwargs)
            self._sig.message.emit("已提交")
        except Exception as exc:  # noqa: BLE001
            self._sig.message.emit(describe_answer_error(exc))
        finally:
            if session is not None:
                session.close()
            self.reload()

    # ── 渲染（主线程）────────────────────────────────────────────────────
    def _show_message(self, text: str) -> None:
        self.hint.setText(text)

    def _render(self, items: list) -> None:
        while self.list_layout.count() > 1:
            child = self.list_layout.takeAt(0)
            if child.widget() is not None:
                child.widget().deleteLater()
        if not items:
            self.hint.setText("当前没有待处理事项")
            return
        self.hint.setText(f"共 {len(items)} 项等待你处理")
        for item in items:
            self.list_layout.insertWidget(self.list_layout.count() - 1, self._row(item))

    _KIND_LABEL = {"permission": "审批", "interaction": "提问", "cron": "后台提问"}

    def _row(self, item: PendingItem) -> QFrame:
        frame = QFrame()
        frame.setFrameShape(QFrame.Shape.StyledPanel)
        lay = QVBoxLayout(frame)

        head = QLabel(f"<b>[{self._KIND_LABEL.get(item.kind, item.kind)}]</b> {_escape(item.title)}")
        head.setWordWrap(True)
        lay.addWidget(head)
        if item.detail:
            detail = QLabel(_escape(item.detail))
            detail.setWordWrap(True)
            detail.setStyleSheet("color: #888;")
            lay.addWidget(detail)

        if item.kind == "permission":
            row = QHBoxLayout()
            row.addStretch(1)
            deny = QPushButton("拒绝")
            deny.clicked.connect(lambda: self._submit(item, approve=False))
            ok = QPushButton("批准")
            ok.clicked.connect(lambda: self._submit(item, approve=True))
            row.addWidget(deny)
            row.addWidget(ok)
            lay.addLayout(row)
        elif item.interaction_kind == "ask_user_confirm":
            row = QHBoxLayout()
            row.addStretch(1)
            no = QPushButton("否")
            no.clicked.connect(lambda: self._submit(item, confirmed=False))
            yes = QPushButton("是")
            yes.clicked.connect(lambda: self._submit(item, confirmed=True))
            row.addWidget(no)
            row.addWidget(yes)
            lay.addLayout(row)
        elif item.options:
            row = QHBoxLayout()
            row.addStretch(1)
            for idx, opt in enumerate(item.options):
                btn = QPushButton(opt)
                if item.kind == "cron":
                    btn.clicked.connect(lambda _=False, o=opt: self._submit(item, text=o))
                else:
                    btn.clicked.connect(lambda _=False, i=idx: self._submit(item, choice_index=i))
                row.addWidget(btn)
            lay.addLayout(row)
        else:
            edit_row = QHBoxLayout()
            edit = QLineEdit()
            edit.setPlaceholderText("输入回答后回车或点提交")
            send = QPushButton("提交")
            edit.returnPressed.connect(lambda: self._submit(item, text=edit.text()))
            send.clicked.connect(lambda: self._submit(item, text=edit.text()))
            edit_row.addWidget(edit, 1)
            edit_row.addWidget(send)
            lay.addLayout(edit_row)
        return frame


def _escape(text: str) -> str:
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace("\n", "<br>"))
