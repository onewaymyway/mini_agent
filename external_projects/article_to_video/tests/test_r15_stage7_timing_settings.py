"""设置页：章封面、章首、句间、章尾的停留参数出现在「讲解与配音」中，范围与校验一致，保存后生效，并列出受影响阶段。"""
import pytest

from article_to_video import app_settings as S
from article_to_video.audio.config import normalize_tts
from conftest import PROJECT_DIR
from helpers_p1 import make_project
from ui.settings_page import FIELDS

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

PATHS = {
    "tts.chapter_card_sec": (3.0, 10.0),
    "tts.lead_in_sec": (0.0, 3.0),
    "tts.sentence_gap_sec": (0.0, 2.0),
    "tts.tail_sec": (0.0, 3.0),
}


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("ARTICLE_TO_VIDEO_HOME", str(tmp_path))
    return tmp_path


def test_timing_fields_are_in_the_settings_schema_with_matching_ranges():
    by_path = {f["path"]: f for f in FIELDS["settings"]}
    for path, (lo, hi) in PATHS.items():
        spec = by_path[path]
        assert spec["kind"] == "number"
        assert (spec["min"], spec["max"]) == (lo, hi), path
        assert spec.get("label")


def test_ui_ranges_agree_with_normalization():
    """界面允许的范围与配置归一化一致：界面给出的值不会被静默改写。"""
    base, _ = normalize_tts(None)
    for path, (lo, hi) in PATHS.items():
        key = path.split(".", 1)[1]
        assert normalize_tts({key: lo})[0][key] == pytest.approx(lo), path
        assert normalize_tts({key: hi})[0][key] == pytest.approx(hi), path
        assert key in base


def test_saving_a_timing_value_reports_affected_stages():
    text = S.affected_stages("settings", "tts.chapter_card_sec")
    assert "重新配音" in text and "渲染画面" in text and "合成" in text


def test_settings_page_shows_timing_fields(home):
    make_project(home)
    at = AppTest.from_file(str(PROJECT_DIR / "app.py"), default_timeout=90).run()
    at.sidebar.radio[0].set_value("⚙️ 设置").run()
    assert not at.exception, [e.value for e in at.exception]
    labels = [n.label for n in at.number_input]
    for needle in ("章封面停留", "章首静音", "句间停顿", "章尾停顿"):
        assert any(needle in lbl for lbl in labels), needle
