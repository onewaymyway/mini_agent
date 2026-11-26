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
        on_open_pending: Optional[Callable[[], None]] = None,
        on_quit_stop: Optional[Callable[[], None]] = None,
        on_toggle_autostart: Optional[Callable[[bool], None]] = None,
        autostart_checked: bool = False,
        on_harden_token: Optional[Callable[[], None]] = None,
        on_open_logs: Optional[Callable[[], None]] = None,
        on_toggle_toast: Optional[Callable[[bool], None]] = None,
        toast_checked: bool = False,
        on_open_settings: Optional[Callable[[], None]] = None,
        on_open_health: Optional[Callable[[], None]] = None,
        on_open_workboard: Optional[Callable[[], None]] = None,
        on_toggle_context: Optional[Callable[[bool], None]] = None,
        context_checked: bool = False,
    ) -> None:
        super().__init__()
        self._model = model
        self._console_url = console_url
        self._on_open_quick = on_open_quick
        self._on_open_pending = on_open_pending
        self._on_quit_stop = on_quit_stop
        self._on_toggle_autostart = on_toggle_autostart
        self._on_toggle_toast = on_toggle_toast

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

        self.pending_action = QAction("待处理事项…", self.menu)
        self.pending_action.triggered.connect(self._open_pending)
        self.menu.addAction(self.pending_action)

        self.settings_action = QAction("设置…", self.menu)
        self.settings_action.triggered.connect(lambda: on_open_settings and on_open_settings())
        self.menu.addAction(self.settings_action)

        self.health_action = QAction("健康状态…", self.menu)
        self.health_action.triggered.connect(lambda: on_open_health and on_open_health())
        self.menu.addAction(self.health_action)

        self.context_action = QAction("快速面板附带当前窗口（隐私）", self.menu)
        self.context_action.setCheckable(True)
        self.context_action.setChecked(context_checked)
        self.context_action.setToolTip("开启后，打开快速面板时读取前台窗口的程序名与标题；不读取页面或文件内容")
        self.context_action.toggled.connect(lambda v: on_toggle_context and on_toggle_context(v))
        self.menu.addAction(self.context_action)

        self.workboard_action = QAction("目标与定时任务…", self.menu)
        self.workboard_action.triggered.connect(lambda: on_open_workboard and on_open_workboard())
        self.menu.addAction(self.workboard_action)

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
        # 阶段A：弹窗提醒开关。默认关闭，必须用户主动勾选（desktop.json 的 toast_enabled 同步保存）
        self.toast_action = QAction("桌面弹窗提醒", self.menu)
        self.toast_action.setCheckable(True)
        self.toast_action.setChecked(toast_checked)
        self.toast_action.setToolTip("审批、提问、任务完成/失败时弹出系统气泡")
        self.toast_action.toggled.connect(self._on_toast_toggled)
        self.menu.addAction(self.toast_action)

        self.test_toast_action = QAction("发送测试弹窗", self.menu)
        self.test_toast_action.setToolTip("不经过提醒规则，直接发一条系统气泡，用于检查通知是否可见")
        self.test_toast_action.triggered.connect(self._send_test_toast)
        self.menu.addAction(self.test_toast_action)

        self.menu.addSeparator()
        # S4：系统维护类操作
        self.autostart_action = QAction("开机自动启动", self.menu)
        self.autostart_action.setCheckable(True)
        self.autostart_action.setChecked(autostart_checked)
        self.autostart_action.toggled.connect(self._on_autostart_toggled)
        self.menu.addAction(self.autostart_action)

        self.harden_action = QAction("加固 token 文件权限（仅当前用户）", self.menu)
        self.harden_action.triggered.connect(lambda: on_harden_token and on_harden_token())
        self.menu.addAction(self.harden_action)

        self.logs_action = QAction("打开日志文件夹", self.menu)
        self.logs_action.triggered.connect(lambda: on_open_logs and on_open_logs())
        self.menu.addAction(self.logs_action)

        self.menu.addSeparator()
        # 退出只关桌面端；后台 daemon 保持运行（"关闭桌面端 ≠ 关闭 Runtime"）
        self.quit_action = QAction("退出桌面端（Agent 继续在后台运行）", self.menu)
        self.quit_action.triggered.connect(on_quit)
        self.menu.addAction(self.quit_action)
        # S4：退出并停止 daemon 是独立的显式选项，需要用户确认（由 app.py 弹窗）
        self.quit_stop_action = QAction("退出并停止 Agent…", self.menu)
        self.quit_stop_action.triggered.connect(lambda: on_quit_stop and on_quit_stop())
        self.menu.addAction(self.quit_stop_action)

        self.tray.setContextMenu(self.menu)
        self.tray.activated.connect(self._on_activated)
        self.tray.messageClicked.connect(self._open_pending)   # S3：点击气泡 → 待处理面板
        self.changed.connect(self.refresh)
        self.refresh()

    def show(self) -> None:
        self.tray.show()

    def hide(self) -> None:
        self.tray.hide()

    def notify_text(self, title: str, text: str) -> None:
        """托盘气泡提示（仅 UI 线程调用）：热键未注册等配置类提示。"""
        self.tray.showMessage(title, text, QSystemTrayIcon.MessageIcon.Warning, 6000)

    def toast(self, title: str, body: str, blocking: bool = False) -> None:
        """S3 Toast：走 Windows 原生气泡（经通知中心显示），点击即打开待处理面板。
        仅 UI 线程调用；策略判断在 attention.ToastPolicy 中完成。"""
        icon = QSystemTrayIcon.MessageIcon.Warning if blocking else QSystemTrayIcon.MessageIcon.Information
        self.tray.showMessage(title, body, icon, 8000)

    def _on_toast_toggled(self, checked: bool) -> None:
        if self._on_toggle_toast is not None:
            self._on_toggle_toast(checked)

    def _send_test_toast(self) -> None:
        self.toast("mini_agent 测试提醒", "如果你能看到这条气泡，说明系统通知可用。", False)

    def _on_autostart_toggled(self, checked: bool) -> None:
        if self._on_toggle_autostart is not None:
            self._on_toggle_autostart(checked)

    def _open_pending(self) -> None:
        if self._on_open_pending is not None:
            self._on_open_pending()

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
