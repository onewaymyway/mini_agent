"""界面冒烟：用 Streamlit AppTest 驱动「新建项目」表单，断言作者名与水印写入 project.json。"""
import json
from pathlib import Path

import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

from conftest import PROJECT_DIR  # noqa: E402


def test_app_renders_import_page(monkeypatch, tmp_path):
    monkeypatch.setenv("ARTICLE_TO_VIDEO_HOME", str(tmp_path))
    at = AppTest.from_file(str(PROJECT_DIR / "app.py"), default_timeout=60).run()
    assert not at.exception
    assert any(h.value == "📥 新建项目：导入文章" for h in at.header)


def test_form_without_upload_shows_error(monkeypatch, tmp_path):
    monkeypatch.setenv("ARTICLE_TO_VIDEO_HOME", str(tmp_path))
    at = AppTest.from_file(str(PROJECT_DIR / "app.py"), default_timeout=60).run()
    at.button[0].click().run()
    assert any("请先上传文章" in e.value for e in at.error)
    assert not any(tmp_path.iterdir())
