"""入口：python -m mini_agent_desktop [--project-root DIR] [--port N] [--check]

--check      无界面自检：解析 daemon 端点 → 健康检查 → 拉取摘要并打印，
             退出码 0=正常 / 2=daemon 未运行 / 3=认证失败 / 4=其它错误。
             不需要安装 PySide6，可用于排障。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

import httpx

from mini_agent_desktop import __version__
from mini_agent_desktop.api import DaemonApi
from mini_agent_desktop.config import DesktopConfig, load_config
from mini_agent_desktop.controller import DesktopController
from mini_agent_desktop.discovery import count_unacked_crash_alerts, resolve_endpoint, spawn_daemon
from mini_agent_desktop.attention import QuietHours, ToastPolicy
from mini_agent_desktop.hotkey import GlobalHotkey
from mini_agent_desktop.quick import QuickSession
from mini_agent_desktop.state import TrayModel


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="mini_agent_desktop", description="mini_agent Windows 桌面常驻客户端")
    p.add_argument("--project-root", default="", help="mini_agent 项目根目录（默认读配置，再默认当前目录）")
    p.add_argument("--port", type=int, default=0, help="daemon HTTP 端口（默认读 daemon_info.json/配置）")
    p.add_argument("--auto-start-daemon", action="store_true",
                   help="daemon 未运行时自动拉起（默认关闭；也可在 desktop.json 里开启）")
    p.add_argument("--check", action="store_true", help="无界面自检后退出")
    p.add_argument("--version", action="version", version=f"mini_agent_desktop {__version__}")
    return p.parse_args(argv)


def resolve_project_root(args: argparse.Namespace, cfg: DesktopConfig) -> Path:
    raw = args.project_root or cfg.project_root
    return Path(raw).expanduser().resolve() if raw else Path.cwd()


def run_check(project_root: Path, port: int) -> int:
    ep = resolve_endpoint(project_root, port)
    print(f"project_root : {project_root}")
    print(f"endpoint     : {ep.base_url}")
    print(f"token        : {'已找到' if ep.token else '未找到（.agent/agent_api.key）'}")
    print(f"离线崩溃告警 : {count_unacked_crash_alerts(project_root)} 条未确认")
    api = DaemonApi(ep.base_url, ep.token)
    try:
        if not api.health():
            print("health       : 失败（daemon 未运行？）")
            return 2
        print("health       : ok")
        status = api.status()
        print(f"agent state  : {status.get('state')}  queue_depth={status.get('queue_depth')}")
        print("summary      : " + json.dumps(api.summary(), ensure_ascii=False))
        return 0
    except httpx.HTTPStatusError as exc:
        code = exc.response.status_code
        print(f"请求失败：HTTP {code}" + ("（token 无效或缺失）" if code in (401, 403) else ""))
        return 3 if code in (401, 403) else 4
    except httpx.HTTPError as exc:
        print(f"请求失败：{exc}")
        return 4
    finally:
        api.close()


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    cfg = load_config()
    project_root = resolve_project_root(args, cfg)
    port = args.port or cfg.http_port

    if args.check:
        return run_check(project_root, port)

    # Qt 延迟导入：--check / 单测不需要装 PySide6
    try:
        from PySide6.QtCore import QDir, QLockFile
        from PySide6.QtWidgets import QApplication, QMessageBox, QSystemTrayIcon
    except ImportError:
        print("缺少 PySide6，请先安装：pip install -r apps/mini_agent_desktop/requirements.txt", file=sys.stderr)
        return 1
    from mini_agent_desktop.pending_dialog import AttentionBridge, PendingDialog
    from mini_agent_desktop.quick_panel import QuickHotkeyBridge, QuickPanel
    from mini_agent_desktop.tray import TrayUi

    app = QApplication(sys.argv[:1])
    app.setQuitOnLastWindowClosed(False)    # 没有窗口也要常驻

    # 单实例：同一用户只允许一个桌面端
    lock = QLockFile(QDir.temp().filePath("mini_agent_desktop.lock"))
    lock.setStaleLockTime(0)
    if not lock.tryLock(100):
        QMessageBox.information(None, "mini_agent", "桌面端已经在运行（请看系统托盘）。")
        return 0
    if not QSystemTrayIcon.isSystemTrayAvailable():
        QMessageBox.warning(None, "mini_agent", "当前系统不支持系统托盘，无法启动。")
        return 1

    model = TrayModel()

    def api_factory() -> DaemonApi:
        ep = resolve_endpoint(project_root, port)
        return DaemonApi(ep.base_url, ep.token)

    def start_daemon() -> None:
        spawn_daemon(project_root, resolve_endpoint(project_root, port).port)

    def quick_session_factory() -> QuickSession:
        return QuickSession(api_factory())

    holder: dict = {}

    # S3：attention 事件可能来自控制器线程，统一经信号切回主线程做 Toast 判断
    attention_bridge = AttentionBridge()
    try:
        quiet = QuietHours.parse(cfg.quiet_hours)
        quiet_err = ""
    except ValueError as exc:
        quiet, quiet_err = None, str(exc)
    policy = ToastPolicy(enabled=cfg.toast_enabled, quiet=quiet)

    def on_attention_main(event: dict) -> None:
        toast = policy.decide(event)
        if toast is not None and "ui" in holder:
            holder["ui"].toast(toast.title, toast.body, toast.blocking)

    attention_bridge.received.connect(on_attention_main)

    controller = DesktopController(
        model, api_factory,
        offline_crashes=lambda: count_unacked_crash_alerts(project_root),
        on_change=lambda: holder["ui"].notify_changed(),
        on_attention=attention_bridge.received.emit,
        start_daemon=start_daemon,
        auto_start_daemon=args.auto_start_daemon or cfg.auto_start_daemon,
        poll_interval=cfg.poll_interval_seconds,
    )

    hotkey = None   # 下方注册成功后才赋值；quit_app 可能早于此被调用

    def quit_app() -> None:
        if hotkey is not None:
            hotkey.stop()
        controller.stop()      # 只停桌面端自己；不碰 daemon
        holder["ui"].hide()
        app.quit()

    panel = QuickPanel(quick_session_factory)
    hotkey_bridge = QuickHotkeyBridge()
    hotkey_bridge.requested.connect(panel.toggle)   # 热键线程 emit → 主线程执行

    pending_dialog = PendingDialog(quick_session_factory)

    holder["ui"] = TrayUi(
        model, cfg.console_url,
        on_start_daemon=controller.manual_start_daemon,
        on_reconnect=controller.reconnect_now,
        on_quit=quit_app,
        on_open_quick=panel.toggle,
        on_open_pending=pending_dialog.open_dialog,
    )
    holder["ui"].show()
    if quiet_err:
        holder["ui"].notify_text("mini_agent", f"免打扰设置无效，已忽略：{quiet_err}")
    controller.start()

    if cfg.hotkey_enabled:
        try:
            hotkey = GlobalHotkey(cfg.hotkey)
        except ValueError as exc:
            QMessageBox.warning(None, "mini_agent", f"热键配置无效，已跳过注册：{exc}")
            hotkey = None
        if hotkey is not None:
            ok, reason = hotkey.start(hotkey_bridge.requested.emit)
            if not ok:
                holder["ui"].notify_text("mini_agent", f"全局热键未注册：{reason}")
                hotkey = None

    code = app.exec()
    if hotkey is not None:
        hotkey.stop()
    controller.stop()
    lock.unlock()
    return code


if __name__ == "__main__":
    raise SystemExit(main())
