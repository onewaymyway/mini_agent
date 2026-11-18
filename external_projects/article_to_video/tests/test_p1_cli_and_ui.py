import json
import os
import subprocess
import sys

import pytest

from conftest import PROJECT_DIR
from helpers_p1 import make_project

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402


def test_plan_outline_cli_rules(tmp_path):
    pdir = make_project(tmp_path)
    out = subprocess.run([sys.executable, str(PROJECT_DIR / "entrypoints" / "plan_outline.py"),
                          "--project", str(pdir), "--engine", "rules"],
                         capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert out.returncode == 0, out.stderr
    payload = json.loads(out.stdout)
    assert payload["ok"] and 5 <= len(payload["parts"]) <= 7


def test_plan_outline_cli_refuses_confirmed_overwrite(tmp_path):
    pdir = make_project(tmp_path)
    run = lambda *extra: subprocess.run(  # noqa: E731
        [sys.executable, str(PROJECT_DIR / "entrypoints" / "plan_outline.py"),
         "--project", str(pdir), "--engine", "rules", *extra],
        capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert run().returncode == 0
    from article_to_video.planning.runner import confirm_spine
    confirm_spine(pdir)
    second = run()
    assert second.returncode == 1 and json.loads(second.stdout)["ok"] is False


def test_outline_page_renders_and_shows_parts(monkeypatch, tmp_path):
    monkeypatch.setenv("ARTICLE_TO_VIDEO_HOME", str(tmp_path))
    pdir = make_project(tmp_path)
    from article_to_video.planning.runner import plan_outline
    plan_outline(pdir, engine="rules")
    at = AppTest.from_file(str(PROJECT_DIR / "app.py"), default_timeout=90).run()
    at.sidebar.radio[0].set_value("🗺️ 大纲").run()
    assert not at.exception, [e.value for e in at.exception]
    assert any(h.value == "🗺️ 大纲（C0）" for h in at.header)
    assert any("章标题" in ti.label for ti in at.text_input)


def test_outline_page_without_outline_shows_hint(monkeypatch, tmp_path):
    monkeypatch.setenv("ARTICLE_TO_VIDEO_HOME", str(tmp_path))
    make_project(tmp_path)
    at = AppTest.from_file(str(PROJECT_DIR / "app.py"), default_timeout=90).run()
    at.sidebar.radio[0].set_value("🗺️ 大纲").run()
    assert not at.exception
    assert any("还没有大纲" in i.value for i in at.info)
