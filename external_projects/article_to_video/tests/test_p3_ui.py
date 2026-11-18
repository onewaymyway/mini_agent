import pytest

from conftest import PROJECT_DIR
from helpers_p2 import good_script, ScriptLLM, ready_project

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

from article_to_video.audio import synth as sy  # noqa: E402
from article_to_video.providers.tts import SilenceTTS  # noqa: E402
from article_to_video.script import writer as w  # noqa: E402


def test_voice_page_renders_with_timeline(monkeypatch, tmp_path):
    monkeypatch.setenv("ARTICLE_TO_VIDEO_HOME", str(tmp_path))
    pdir = ready_project(tmp_path)
    w.write_part(pdir, "P01", ScriptLLM(good_script))
    w.confirm_script(pdir, "P01")
    sy.synthesize_part(pdir, "P01", SilenceTTS())
    at = AppTest.from_file(str(PROJECT_DIR / "app.py"), default_timeout=120).run()
    at.sidebar.radio[0].set_value("🔊 配音").run()
    assert not at.exception, [e.value for e in at.exception]
    assert any(h.value == "🔊 配音（S5）" for h in at.header)
    assert any("总长" in c.value for c in at.caption)


def test_voice_page_without_confirmed_script_shows_hint(monkeypatch, tmp_path):
    monkeypatch.setenv("ARTICLE_TO_VIDEO_HOME", str(tmp_path))
    ready_project(tmp_path)
    at = AppTest.from_file(str(PROJECT_DIR / "app.py"), default_timeout=120).run()
    at.sidebar.radio[0].set_value("🔊 配音").run()
    assert not at.exception
    assert any("C1" in i.value for i in at.info)
