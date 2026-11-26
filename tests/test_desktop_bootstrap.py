"""tests/test_desktop_bootstrap.py

pythonw 下 stderr 不可见，启动期致命错误必须写日志并弹窗。覆盖：
- guarded_main：未捕获异常 → 完整 traceback 进日志 + 原生弹窗 + 返回码 1
- guarded_main：SystemExit 的退出码原样返回
- PySide6 未安装 → main 返回 1，原因写入日志（而不是只 print 到 stderr）
- --check 输出 Python 路径与 PySide6 安装状态

运行方式：PYTHONPATH=src python3 -m pytest tests/test_desktop_bootstrap.py -q
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "apps" / "mini_agent_desktop"))

from mini_agent_desktop import bootstrap, logsetup  # noqa: E402


@pytest.fixture
def clean_logger():
    lg = logging.getLogger(logsetup.LOGGER_NAME)
    for h in list(lg.handlers):
        lg.removeHandler(h)
    yield lg
    for h in list(lg.handlers):
        lg.removeHandler(h)
        h.close()


@pytest.fixture
def popups(monkeypatch):
    shown = []
    monkeypatch.setattr(bootstrap, "native_error", lambda t, x: shown.append((t, x)))
    return shown


def test_unhandled_exception_is_logged_and_shown(tmp_path, clean_logger, popups):
    log_path = tmp_path / "desktop.log"

    def boom():
        raise RuntimeError("tray exploded")

    rc = bootstrap.guarded_main(boom, log_path=log_path)
    assert rc == 1
    for h in clean_logger.handlers:
        h.flush()
    text = log_path.read_text(encoding="utf-8")
    assert "Traceback" in text and "tray exploded" in text
    assert popups and "tray exploded" in popups[0][1]
    assert str(log_path) in popups[0][1]


def test_system_exit_code_is_passed_through(tmp_path, clean_logger, popups):
    assert bootstrap.guarded_main(lambda: (_ for _ in ()).throw(SystemExit(3)),
                                  log_path=tmp_path / "x.log") == 3
    assert popups == []


def test_normal_return_maps_to_zero(tmp_path, clean_logger, popups):
    assert bootstrap.guarded_main(lambda: None, log_path=tmp_path / "x.log") == 0
    assert bootstrap.guarded_main(lambda: 2, log_path=tmp_path / "x.log") == 2


def test_missing_pyside6_returns_1_and_logs_reason(tmp_path, clean_logger, monkeypatch, popups):
    from mini_agent_desktop import app as app_mod

    # 屏蔽所有 PySide6 子模块（已被其它用例导入过的子模块会被缓存，只屏蔽顶层不够）
    for name in [m for m in list(sys.modules) if m == "PySide6" or m.startswith("PySide6.")]:
        monkeypatch.setitem(sys.modules, name, None)
    monkeypatch.setitem(sys.modules, "PySide6", None)
    monkeypatch.setitem(sys.modules, "PySide6.QtCore", None)
    monkeypatch.setitem(sys.modules, "PySide6.QtWidgets", None)
    monkeypatch.setitem(sys.modules, "PySide6.QtGui", None)
    monkeypatch.setattr(app_mod, "resolve_project_root_info",
                        lambda args, cfg: app_mod.locator.Resolved(tmp_path, "test"))
    monkeypatch.setattr(bootstrap, "native_error", lambda t, x: popups.append((t, x)))
    log_path = tmp_path / "desktop.log"
    logsetup.setup_logging(log_path)

    rc = app_mod.main(["--project-root", str(tmp_path)])
    assert rc == 1
    assert any("PySide6" in x for _, x in popups)
    for h in clean_logger.handlers:
        h.flush()
    assert "缺少 PySide6" in log_path.read_text(encoding="utf-8")


def test_check_reports_python_and_pyside_status(tmp_path, capsys):
    from mini_agent_desktop.app import run_check

    run_check(tmp_path, 1, "test")
    out = capsys.readouterr().out
    assert "Python" in out and sys.executable in out
    assert "PySide6" in out
