"""tests/test_desktop_s4.py

覆盖 next_doc/windows_desktop_resident_plan.md S4（自启动与加固）：

- autostart：启动命令构造（pythonw、引号、可选 --project-root）、sync 的幂等与只删自己的值
- security：icacls 参数、非 Windows / 文件缺失 / 执行失败 / 成功 四种结果（注入假 runner）
- logsetup：写文件、幂等不重复挂 handler、目录不可写时降级不抛异常
- 接口契约：POST /v1/shutdown 存在于真实 routes；DaemonApi.shutdown 请求形态
- config：autostart 默认关闭
- Qt 离屏：托盘 S4 菜单项存在，且"退出并停止 Agent"触发回调

运行方式：
    PYTHONPATH=src python3 -m pytest tests/test_desktop_s4.py -q
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path

import httpx
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "apps" / "mini_agent_desktop"))

from mini_agent_desktop import autostart, logsetup, security  # noqa: E402
from mini_agent_desktop.api import DaemonApi  # noqa: E402
from mini_agent_desktop.config import DesktopConfig, load_config, save_config  # noqa: E402


# ════════════════════════════════════════════════════════════════════
# autostart
# ════════════════════════════════════════════════════════════════════

def test_launcher_file_exists_and_compiles():
    path = autostart.launcher_path()
    assert path.name == "launch_desktop.pyw"
    assert path.exists()
    compile(path.read_text(encoding="utf-8"), str(path), "exec")


def test_pythonw_substitution(tmp_path):
    exe = tmp_path / "python.exe"
    exe.write_text("", encoding="utf-8")
    (tmp_path / "pythonw.exe").write_text("", encoding="utf-8")
    assert autostart.pythonw_for(str(exe)).lower().endswith("pythonw.exe")


def test_pythonw_kept_when_no_sibling(tmp_path):
    exe = tmp_path / "python.exe"
    assert autostart.pythonw_for(str(exe)) == str(exe)
    other = tmp_path / "custom-runner.exe"
    assert autostart.pythonw_for(str(other)) == str(other)


def test_build_command_quotes_paths_with_spaces(tmp_path):
    cmd = autostart.build_command(
        r"C:\Program Files\Python\python.exe",
        Path(r"C:\my apps\launch_desktop.pyw"),
        Path(r"D:\work\mini agent"),
    )
    assert cmd.startswith('"')
    assert '"C:\\my apps\\launch_desktop.pyw"' in cmd
    assert '--project-root "D:\\work\\mini agent"' in cmd


def test_build_command_without_project_root():
    cmd = autostart.build_command("python", Path("/x/launch_desktop.pyw"), None)
    assert "--project-root" not in cmd


class FakeRunStore:
    def __init__(self, value=None):
        self.value = value
        self.writes = 0
        self.deletes = 0

    def get(self):
        return self.value

    def set(self, command):
        self.value = command
        self.writes += 1

    def delete(self):
        self.value = None
        self.deletes += 1


def test_sync_enable_writes_once_then_unchanged():
    store = FakeRunStore()
    assert autostart.sync(store, True, "cmd-A") == "enabled"
    assert autostart.sync(store, True, "cmd-A") == "unchanged"
    assert store.writes == 1


def test_sync_enable_rewrites_when_command_changed():
    store = FakeRunStore("old-cmd")
    assert autostart.sync(store, True, "new-cmd") == "enabled"
    assert store.value == "new-cmd"


def test_sync_disable_removes_only_when_present():
    store = FakeRunStore("cmd")
    assert autostart.sync(store, False, "cmd") == "disabled"
    assert store.value is None
    assert autostart.sync(store, False, "cmd") == "unchanged"
    assert store.deletes == 1


def test_sync_without_store_is_unsupported():
    assert autostart.sync(None, True, "cmd") == "unsupported"
    assert autostart.sync(None, False, "cmd") == "unsupported"


def test_default_store_none_off_windows():
    if os.name == "nt":
        pytest.skip("本用例针对非 Windows")
    assert autostart.default_store() is None


# ════════════════════════════════════════════════════════════════════
# token ACL 加固
# ════════════════════════════════════════════════════════════════════

def test_icacls_argv_grants_only_user_full_control_without_inheritance(tmp_path):
    argv = security.icacls_argv(tmp_path / "k", "alice")
    assert argv[0] == "icacls"
    assert "/inheritance:r" in argv
    assert argv[argv.index("/grant:r") + 1] == "alice:F"


def test_harden_unsupported_off_windows(tmp_path):
    f = tmp_path / "k"
    f.write_text("x", encoding="utf-8")
    r = security.harden_file(f, is_windows=False)
    assert r.status == "unsupported"


def test_harden_missing_file(tmp_path):
    r = security.harden_file(tmp_path / "nope", is_windows=True, runner=lambda a: None)
    assert r.status == "missing"


def test_harden_success_calls_icacls_with_expected_args(tmp_path):
    f = tmp_path / "agent_api.key"
    f.write_text("tok", encoding="utf-8")
    seen = []

    def runner(argv):
        seen.append(argv)
        return subprocess.CompletedProcess(argv, 0, "", "")

    r = security.harden_file(f, username="bob", is_windows=True, runner=runner)
    assert r.status == "hardened"
    assert "bob" in r.message
    assert seen[0] == security.icacls_argv(f, "bob")


def test_harden_reports_icacls_failure_with_stderr(tmp_path):
    f = tmp_path / "k"
    f.write_text("x", encoding="utf-8")
    runner = lambda argv: subprocess.CompletedProcess(argv, 5, "", "拒绝访问")
    r = security.harden_file(f, username="bob", is_windows=True, runner=runner)
    assert r.status == "failed"
    assert "拒绝访问" in r.message


def test_harden_handles_missing_icacls_binary(tmp_path):
    f = tmp_path / "k"
    f.write_text("x", encoding="utf-8")

    def runner(argv):
        raise FileNotFoundError("icacls")

    r = security.harden_file(f, username="bob", is_windows=True, runner=runner)
    assert r.status == "failed"


def test_harden_project_tokens_covers_both_token_locations(tmp_path):
    (tmp_path / ".agent").mkdir()
    (tmp_path / ".agent" / "agent_api.key").write_text("t", encoding="utf-8")
    calls = []

    def runner(argv):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, "", "")

    results = security.harden_project_tokens(tmp_path, username="bob", is_windows=True, runner=runner)
    assert [r.status for r in results] == ["hardened", "missing"]
    assert len(calls) == 1


def test_token_files_paths(tmp_path):
    paths = security.token_files(tmp_path)
    assert paths[0] == tmp_path / ".agent" / "agent_api.key"
    assert paths[1] == tmp_path / ".agent" / "users" / "tokens" / "owner.key"


# ════════════════════════════════════════════════════════════════════
# 日志
# ════════════════════════════════════════════════════════════════════

@pytest.fixture
def clean_logger():
    name = logsetup.LOGGER_NAME
    lg = logging.getLogger(name)
    for h in list(lg.handlers):
        lg.removeHandler(h)
    yield lg
    for h in list(lg.handlers):
        lg.removeHandler(h)
        try:
            h.close()
        except Exception:  # noqa: BLE001
            pass


def test_setup_logging_writes_to_file(tmp_path, clean_logger):
    path = tmp_path / "logs" / "desktop.log"
    logsetup.setup_logging(path)
    logging.getLogger("mini_agent_desktop.test").info("hello 日志")
    for h in clean_logger.handlers:
        h.flush()
    assert path.exists()
    assert "hello 日志" in path.read_text(encoding="utf-8")


def test_setup_logging_is_idempotent(tmp_path, clean_logger):
    path = tmp_path / "desktop.log"
    logsetup.setup_logging(path)
    logsetup.setup_logging(path)
    marked = [h for h in clean_logger.handlers if getattr(h, logsetup._MARK, False)]
    assert len(marked) == 1


def test_setup_logging_degrades_when_dir_unwritable(tmp_path, clean_logger):
    blocker = tmp_path / "blocker"
    blocker.write_text("i am a file", encoding="utf-8")
    logsetup.setup_logging(blocker / "sub" / "desktop.log")   # 父路径是文件，mkdir 失败
    assert clean_logger.handlers, "应退化为 NullHandler，而不是没有 handler"


def test_default_log_path_shape():
    p = logsetup.default_log_path()
    assert p.name == "desktop.log"
    assert p.parent.name == "logs"


# ════════════════════════════════════════════════════════════════════
# 接口契约 & 配置
# ════════════════════════════════════════════════════════════════════

def test_shutdown_route_exists_in_real_router():
    pytest.importorskip("fastapi")
    from mini_agent.api import routes

    registered = {(m, r.path) for r in routes.router.routes for m in getattr(r, "methods", set())}
    assert ("POST", "/v1/shutdown") in registered


def test_daemon_api_shutdown_request_shape():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["path"] = request.url.path
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={"ok": True, "message": "shutdown requested"})

    api = DaemonApi("http://127.0.0.1:8765", "tok", transport=httpx.MockTransport(handler))
    out = api.shutdown()
    assert out["ok"] is True
    assert seen == {"method": "POST", "path": "/v1/shutdown", "auth": "Bearer tok"}


def test_autostart_default_off_and_roundtrip(tmp_path):
    assert DesktopConfig().autostart is False
    p = tmp_path / "d.json"
    save_config(DesktopConfig(autostart=True), p)
    assert load_config(p).autostart is True


# ════════════════════════════════════════════════════════════════════
# Qt 离屏：托盘 S4 菜单
# ════════════════════════════════════════════════════════════════════

def test_tray_s4_menu_items_and_quit_stop_callback():
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    from mini_agent_desktop.state import TrayModel
    from mini_agent_desktop.tray import TrayUi

    QApplication.instance() or QApplication([])
    called = []
    ui = TrayUi(
        TrayModel(), "http://x",
        on_start_daemon=lambda: None,
        on_reconnect=lambda: None,
        on_quit=lambda: None,
        on_quit_stop=lambda: called.append("stop"),
        on_toggle_autostart=lambda v: called.append(("auto", v)),
        autostart_checked=True,
    )
    texts = [a.text() for a in ui.menu.actions()]
    assert "开机自动启动" in texts
    assert "退出并停止 Agent…" in texts
    assert "加固 token 文件权限（仅当前用户）" in texts
    assert "打开日志文件夹" in texts
    assert ui.autostart_action.isCheckable() and ui.autostart_action.isChecked()

    ui.quit_stop_action.trigger()
    assert called == ["stop"]
    ui.autostart_action.setChecked(False)
    assert ("auto", False) in called
