"""系统托盘（PySide6）。只做展示与菜单，状态逻辑都在 state.py / controller.py。

后台线程通过 `changed` 信号通知 UI 线程刷新（Qt 会自动跨线程排队投递），
本模块不直接调用任何会阻塞的网络方法。
"""

from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtCore import QObject, Qt, QUrl, Signal
from PySide6.QtGui import QAction, QColor, QCursor, QDesktopServices, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from mini_agent_desktop.state import MenuState, TrayIcon, TrayModel

_COLORS = {
    TrayIcon.IDLE: "#3fb950",
    TrayIcon.WORKING: "#58a6ff",
    TrayIcon.ATTENTION: "#d29922",
    TrayIcon.ERROR: "#f85149",
    TrayIcon.OFFLINE: "#8b949e",
}


def make_icon(state: TrayIcon, badge: int = 0) -> QIcon:
    """程序绘制图标（避免随包携带图片资源）：状态色圆 + 右下角数字角标。"""
    size = 64
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(_COLORS[state]))
    p.drawEllipse(4, 4, 56, 56)
    # 工作中：中心画一个白点，和"空闲"在小尺寸下也能区分
    if state is TrayIcon.WORKING:
        p.setBrush(QColor("white"))
        p.drawEllipse(24, 24, 16, 16)
    if badge > 0:
        p.setBrush(QColor("#da3633"))
        p.drawEllipse(30, 30, 32, 32)
        p.setPen(QColor("white"))
        font = QFont()
        font.setBold(True)
        font.setPixelSize(22)
        p.setFont(font)
        p.drawText(30, 30, 32, 32, Qt.AlignmentFlag.AlignCenter, "9+" if badge > 9 else str(badge))
    p.end()
    return QIcon(pm)


class TrayUi(QObject):
    changed = Signal()     # 后台线程 emit，UI 线程刷新

    def __init__(
        self,
        model: TrayModel,
        console_url: str,
        on_start_daemon: Callable[[], None],
        on_reconnect: Callable[[], None],
        on_quit: Callable[[], None],
        on_open_quick: Optional[Callable[[], None]] = None,
    ) -> None:
        super().__init__()
        self._model = model
        self._console_url = console_url
        self._on_open_quick = on_open_quick

        self.tray = QSystemTrayIcon()
        self.menu = QMenu()

        self.status_action = QAction(self.menu)
        self.tasks_action = QAction(self.menu)
        self.attention_action = QAction(self.menu)
        for a in (self.status_action, self.tasks_action, self.attention_action):
            a.setEnabled(False)       # 纯信息行
            self.menu.addAction(a)
        self.menu.addSeparator()

        self.quick_action = QAction("快速面板…", self.menu)
        self.quick_action.triggered.connect(self._open_quick)
        self.menu.addAction(self.quick_action)

        self.console_action = QAction("打开管理控制台", self.menu)
        self.console_action.triggered.connect(self._open_console)
        self.menu.addAction(self.console_action)

        self.start_action = QAction("启动 Agent", self.menu)
        self.start_action.triggered.connect(on_start_daemon)
        self.menu.addAction(self.start_action)

        self.reconnect_action = QAction("重新连接", self.menu)
        self.reconnect_action.triggered.connect(on_reconnect)
        self.menu.addAction(self.reconnect_action)

        self.menu.addSeparator()
        # 退出只关桌面端；后台 daemon 保持运行（"关闭桌面端 ≠ 关闭 Runtime"）
        self.quit_action = QAction("退出桌面端（Agent 继续在后台运行）", self.menu)
        self.quit_action.triggered.connect(on_quit)
        self.menu.addAction(self.quit_action)

        self.tray.setContextMenu(self.menu)
        self.tray.activated.connect(self._on_activated)
        self.changed.connect(self.refresh)
        self.refresh()

    def show(self) -> None:
        self.tray.show()

    def hide(self) -> None:
        self.tray.hide()

    def notify_text(self, title: str, text: str) -> None:
        """托盘气泡提示（仅 UI 线程调用）。S3 的 Toast 会替换这里的实现。"""
        self.tray.showMessage(title, text, QSystemTrayIcon.MessageIcon.Warning, 6000)

    def notify_changed(self) -> None:
        """可在任意线程调用。"""
        self.changed.emit()

    def refresh(self) -> MenuState:
        s = self._model.menu_state()
        self.status_action.setText(s.status_text)
        self.tasks_action.setText(s.tasks_text)
        self.attention_action.setText(s.attention_text)
        self.start_action.setVisible(s.can_start_daemon)
        self.tray.setIcon(make_icon(s.icon, s.badge))
        self.tray.setToolTip(s.tooltip)
        return s

    def _open_console(self) -> None:
        QDesktopServices.openUrl(QUrl(self._console_url))

    def _open_quick(self) -> None:
        if self._on_open_quick is not None:
            self._on_open_quick()

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        # S2：左键单击唤起快速面板；右键仍由系统弹出菜单（setContextMenu）
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            if self._on_open_quick is not None:
                self._on_open_quick()
            else:
                self.menu.popup(QCursor.pos())
