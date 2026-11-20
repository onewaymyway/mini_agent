"""R23 补充：批量队列自动导出成片。开启时队列运行到导出阶段自动通过终审（approve=True）；关闭则停在终审。"""
import pytest

from conftest import PROJECT_DIR
from article_to_video import app_settings
from article_to_video.batch import runner, store

ART = "# 文章标题\n\n作者：某作者\n\n## 第一节\n\n这是第一节的正文内容。\n"


def test_auto_export_defaults_to_on_and_is_normalized(tmp_path):
    assert store.load(tmp_path)["params"]["auto_export"] is True
    p, _ = store.normalize_params({"auto_export": False})
    assert p["auto_export"] is False
    p, _ = store.normalize_params({})
    assert p["auto_export"] is True


def _capture_pipeline_call(monkeypatch, root, params):
    seen = {}

    def fake_run_pipeline(pdir, **kw):
        seen.update(kw)
        return {"state": "done"}

    monkeypatch.setattr(runner, "run_pipeline", fake_run_pipeline)
    monkeypatch.setattr(runner, "default_providers", lambda pdir: {"llm": None, "tts": None, "image": None})
    monkeypatch.setattr(app_settings, "effective_group", lambda pdir, name: {"outline_engine": "x"})
    runner.default_pipeline_runner(root, params)
    return seen


def test_auto_export_on_passes_approve_true(tmp_path, monkeypatch):
    store.set_params(tmp_path, {"auto_export": True, "gear": "G2", "max_images": 5})
    params = store.load(tmp_path)["params"]
    seen = _capture_pipeline_call(monkeypatch, tmp_path / "p", params)
    assert seen["approve"] is True


def test_auto_export_off_keeps_manual_final_review(tmp_path, monkeypatch):
    store.set_params(tmp_path, {"auto_export": False, "gear": "G2", "max_images": 5})
    params = store.load(tmp_path)["params"]
    seen = _capture_pipeline_call(monkeypatch, tmp_path / "p", params)
    assert seen["approve"] is False


pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402
from helpers_ui_nav import go_page  # noqa: E402

PAGE = "📦 批量生成"


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("ARTICLE_TO_VIDEO_HOME", str(tmp_path))
    return tmp_path


def _app():
    at = AppTest.from_file(str(PROJECT_DIR / "app.py"), default_timeout=120).run()
    return go_page(at, PAGE)


def test_page_checkbox_defaults_on_and_saves_off(home):
    at = _app()
    cb = next(c for c in at.checkbox if c.key == "bq_autoexport")
    assert cb.value is True
    cb.uncheck().run()
    next(b for b in at.button if b.key == "bq_save").click().run()
    assert not at.exception, [e.value for e in at.exception]
    assert store.load(home)["params"]["auto_export"] is False
