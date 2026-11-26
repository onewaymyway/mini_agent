"""入口：python -m mini_agent_desktop [--project-root DIR] [--port N] [--check]

--check      无界面自检：解析 daemon 端点 → 健康检查 → 拉取摘要并打印，
             退出码 0=正常 / 2=daemon 未运行 / 3=认证失败 / 4=其它错误。
             不需要安装 PySide6，可用于排障。
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Optional

import httpx

from mini_agent_desktop import __version__
from mini_agent_desktop.api import DaemonApi
from mini_agent_desktop import autostart, logsetup, security
from mini_agent_desktop import project_locator as locator
from mini_agent_desktop.config import DesktopConfig, load_config, save_config
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


def resolve_project_root(args: argparse.Namespace, cfg: DesktopConfig) -> Optional[Path]:
    """按 project_locator 的优先级解析项目根；找不到返回 None（不再静默用当前目录）。"""
    resolved = resolve_project_root_info(args, cfg)
    return resolved.path if resolved else None


def resolve_project_root_info(args: argparse.Namespace, cfg: DesktopConfig) -> Optional[Resolved]:
    return locator.resolve_project_root(cli_value=args.project_root or "",
                                        config_value=cfg.project_root or "")


NOT_FOUND_HINT = ("未找到 mini_agent 项目目录。请使用 --project-root 指定，或在 desktop.json 中设置 "
                  "project_root，或设置环境变量 MINI_AGENT_PROJECT_ROOT。")


def run_check(project_root: Path, port: int, source: str = "") -> int:
    ep = resolve_endpoint(project_root, port)
    print(f"project_root : {project_root}" + (f"  （来源：{source}）" if source else ""))
    print(f"Python       : {sys.executable}")
    try:
        import PySide6  # noqa: F401
        print("PySide6      : 已安装")
    except ImportError:
        print("PySide6      : 未安装（托盘版无法启动：pip install -r requirements.txt）")
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


def _show_fatal(text: str) -> None:
    """pythonw 没有控制台，致命错误必须用弹窗告知，否则用户只会看到\"没反应\"。"""
    try:
        from PySide6.QtWidgets import QApplication, QMessageBox
    except ImportError:
        from mini_agent_desktop.bootstrap import native_error
        native_error("mini_agent", text)
        return
    app = QApplication.instance() or QApplication(sys.argv[:1])
    QMessageBox.critical(None, "mini_agent", text)
    del app


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    cfg = load_config()
    resolved = resolve_project_root_info(args, cfg)
    if resolved is None:
        if args.check:
            print(NOT_FOUND_HINT, file=sys.stderr)
            return 4
        _show_fatal(NOT_FOUND_HINT)
        return 1
    project_root = resolved.path
    port = args.port or cfg.http_port

    if args.check:
        return run_check(project_root, port, resolved.source)

    # Qt 延迟导入：--check / 单测不需要装 PySide6
    try:
        from PySide6.QtCore import QDir, QLockFile
        from PySide6.QtWidgets import QApplication, QMessageBox, QSystemTrayIcon
    except ImportError as exc:
        # pythonw 下 stderr 不可见，必须写日志并弹窗
        msg = ("缺少 PySide6（当前 Python：{}）。请在同一个 Python 环境中执行：\n"
               "pip install -r apps\\mini_agent_desktop\\requirements.txt").format(sys.executable)
        logging.getLogger("mini_agent_desktop.app").error("%s；导入错误：%s", msg, exc)
        from mini_agent_desktop.bootstrap import native_error
        native_error("mini_agent", msg)
        return 1
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QDesktopServices

    from mini_agent_desktop.pending_dialog import AttentionBridge, PendingDialog
    from mini_agent_desktop.quick_panel import QuickHotkeyBridge, QuickPanel
    from mini_agent_desktop.tray import TrayUi

    log_path = logsetup.default_log_path()
    logsetup.setup_logging(log_path)
    log = logging.getLogger("mini_agent_desktop.app")
    log.info("桌面端启动 v%s，project_root=%s（%s）", __version__, project_root, resolved.source)

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

    # S4：登录自启以 desktop.json 为准同步注册表项（只动自己的值）
    launcher = autostart.launcher_path()
    auto_cmd = autostart.build_command(sys.executable, launcher, project_root)
    auto_store = autostart.default_store()
    try:
        log.info("开机自启同步：%s", autostart.sync(auto_store, cfg.autostart, auto_cmd))
    except OSError as exc:
        log.warning("开机自启同步失败：%s", exc)

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
            log.info("Toast：%s — %s", toast.title, toast.body)
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

    def notify(title: str, text: str) -> None:
        if "ui" in holder:
            holder["ui"].notify_text(title, text)

    def toggle_autostart(enabled: bool) -> None:
        cfg.autostart = enabled
        try:
            save_config(cfg)
        except OSError as exc:
            log.warning("保存 desktop.json 失败：%s", exc)
        try:
            result = autostart.sync(auto_store, enabled, auto_cmd)
        except OSError as exc:
            result = f"failed: {exc}"
            notify("mini_agent", f"开机自启设置失败：{exc}")
        log.info("开机自启切换为 %s → %s", enabled, result)
        if result == "unsupported":
            notify("mini_agent", "开机自启仅支持 Windows")

    def harden_tokens() -> None:
        results = security.harden_project_tokens(project_root)
        for r in results:
            log.info("token ACL %s: %s %s", r.status, r.path, r.message)
        hardened = [r for r in results if r.status == "hardened"]
        failed = [r for r in results if r.status == "failed"]
        if failed:
            notify("mini_agent", f"token 权限加固失败：{failed[0].message}")
        elif hardened:
            notify("mini_agent", f"已加固 {len(hardened)} 个 token 文件")
        else:
            notify("mini_agent", "没有找到需要加固的 token 文件")

    def open_logs() -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(log_path.parent)))

    def quit_and_stop() -> None:
        answer = QMessageBox.question(
            None, "mini_agent",
            "确定停止 Agent 后台进程并退出桌面端？正在执行的任务会被中断。",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        api = api_factory()
        try:
            api.shutdown()
            log.info("已请求 daemon 关停")
        except Exception as exc:  # noqa: BLE001 - daemon 可能已不在运行
            log.info("关停请求未成功（daemon 可能已停止）：%s", exc)
        finally:
            api.close()
        quit_app()

    holder["ui"] = TrayUi(
        model, cfg.console_url,
        on_start_daemon=controller.manual_start_daemon,
        on_reconnect=controller.reconnect_now,
        on_quit=quit_app,
        on_open_quick=panel.toggle,
        on_open_pending=pending_dialog.open_dialog,
        on_quit_stop=quit_and_stop,
        on_toggle_autostart=toggle_autostart,
        autostart_checked=cfg.autostart,
        on_harden_token=harden_tokens,
        on_open_logs=open_logs,
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
            log.info("全局热键 %s：%s", cfg.hotkey, "已注册" if ok else reason)
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
