"""P5 界面与命令行：合成导出页可加载；入口在前置条件不满足时给出可读错误。"""
import json
import sys

import pytest

from conftest import PROJECT_DIR
from helpers_p4 import voiced_project

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402
from helpers_ui_nav import go_page, go_step


def test_export_page_loads_and_lists_chapters(monkeypatch, tmp_path):
    monkeypatch.setenv("ARTICLE_TO_VIDEO_HOME", str(tmp_path))
    voiced_project(tmp_path)
    at = AppTest.from_file(str(PROJECT_DIR / "app.py"), default_timeout=120).run()
    go_step(at, "🎬 合成导出")
    assert not at.exception, [e.value for e in at.exception]
    assert any(h.value == "🎬 合成导出（P5）" for h in at.header)
    assert any(b.label == "🎞️ 导出成片（后台）" for b in at.button)


def test_cli_compose_reports_missing_render(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("ARTICLE_TO_VIDEO_HOME", str(tmp_path))
    pdir, _ = voiced_project(tmp_path)
    sys.path.insert(0, str(PROJECT_DIR / "entrypoints"))
    import compose_part as cp
    assert cp.main(["--id", pdir.name, "--part", "P02"]) == 1
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False and "渲染" in out["error"]


def test_cli_export_reports_missing_chapters(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("ARTICLE_TO_VIDEO_HOME", str(tmp_path))
    pdir, _ = voiced_project(tmp_path)
    sys.path.insert(0, str(PROJECT_DIR / "entrypoints"))
    import export_final as ef
    assert ef.main(["--id", pdir.name]) == 1
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False and "P01" in out["error"]
