"""app.py 装配测试。

覆盖两类问题：
1. 静态：pyflakes 报告的未定义名（曾经导致启动即 NameError）；
2. 运行：离屏下真正调用一次 main()，只把阻塞的 app.exec() 替换为立即返回，
   用来确认托盘、面板、热键、设置、健康、目标等所有窗口的装配段都能执行到结束。
"""

import json
import os
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[1] / "apps" / "mini_agent_desktop" / "mini_agent_desktop" / "app.py"


def test_app_module_has_no_undefined_names():
    pyflakes_api = pytest.importorskip("pyflakes.api")
    import io
    import pyflakes.reporter as rep

    out, err = io.StringIO(), io.StringIO()
    reporter = rep.Reporter(out, err)
    pyflakes_api.checkPath(str(APP), reporter)
    text = out.getvalue() + err.getvalue()
    assert "undefined name" not in text, text


def test_main_assembles_all_windows_offscreen(tmp_path, monkeypatch):
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication, QSystemTrayIcon

    from mini_agent_desktop import app as app_mod

    cfg_path = tmp_path / "desktop.json"
    cfg_path.write_text(json.dumps({"hotkey_enabled": True, "hotkey": "Ctrl+Alt+K",
                                    "toast_enabled": True, "context_awareness_enabled": True}),
                        encoding="utf-8")
    monkeypatch.setenv("MINI_AGENT_DESKTOP_CONFIG", str(cfg_path))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    project = tmp_path / "project"
    (project / ".agent").mkdir(parents=True)

    monkeypatch.setattr(QSystemTrayIcon, "isSystemTrayAvailable", staticmethod(lambda: True))
    seen = {}

    def fake_exec(self):
        seen["exec"] = True
        return 0

    monkeypatch.setattr(QApplication, "exec", fake_exec)

    code = app_mod.main(["--project-root", str(project)])
    assert seen.get("exec") is True
    assert code == 0
