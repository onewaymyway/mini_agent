"""R18：清空产出并重新生成——项目级清空（大纲/全部章节/导出）、生图缓存开关、确认要求，以及各阶段页面的控件。"""
import pytest

from article_to_video.pipeline.gates import PipelineError
from article_to_video.pipeline.reset import PROJECT_ORDER, reset_project
from article_to_video.visual import storyboard as sbm
from conftest import PROJECT_DIR
from helpers_p4 import scripted_project
from helpers_ui_nav import go_step

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402


@pytest.fixture
def pdir(tmp_path, monkeypatch):
    monkeypatch.setenv("ARTICLE_TO_VIDEO_HOME", str(tmp_path))
    d, _ = scripted_project(tmp_path)
    for pid in ("P01", "P02"):
        (d / "parts" / pid / "audio").mkdir(parents=True, exist_ok=True)
        (d / "parts" / pid / "audio" / "s.wav").write_bytes(b"x")
        (d / "parts" / pid / "timeline.json").write_text("{}", encoding="utf-8")
        (d / "parts" / pid / "images").mkdir(parents=True, exist_ok=True)
        (d / "parts" / pid / "images" / "i.png").write_bytes(b"x")
        (d / "parts" / pid / "video").mkdir(parents=True, exist_ok=True)
        (d / "parts" / pid / "video" / "v.mp4").write_bytes(b"x")
    (d / "exports").mkdir(exist_ok=True)
    (d / "exports" / "final.mp4").write_bytes(b"x")
    return d


def test_order_covers_outline_through_export():
    assert PROJECT_ORDER == ("outline", "script", "voice", "storyboard", "render", "compose", "export")


def test_clearing_requires_confirmation(pdir):
    with pytest.raises(PipelineError):
        reset_project(pdir, from_stage="voice")


def test_voice_clear_removes_sentence_cache_for_every_chapter_and_downstream(pdir):
    reset_project(pdir, from_stage="voice", confirm=True)
    for pid in ("P01", "P02"):
        assert not (pdir / "parts" / pid / "audio").exists()       # 句子缓存也清掉，才会真正重新配音
        assert not (pdir / "parts" / pid / "timeline.json").exists()
        assert not (pdir / "parts" / pid / "video").exists()
    assert not (pdir / "exports").exists()
    assert (pdir / "parts" / "P01" / "script.json").is_file()      # 上游口播稿保留


def test_single_chapter_scope_leaves_other_chapters_alone(pdir):
    reset_project(pdir, from_stage="voice", part_ids=["P01"], confirm=True)
    assert not (pdir / "parts" / "P01" / "audio").exists()
    assert (pdir / "parts" / "P02" / "audio" / "s.wav").is_file()


def test_images_are_kept_unless_requested(pdir):
    reset_project(pdir, from_stage="voice", confirm=True)
    assert (pdir / "parts" / "P01" / "images" / "i.png").is_file()
    reset_project(pdir, from_stage="voice", include_images=True, confirm=True)
    assert not (pdir / "parts" / "P01" / "images").exists()


def test_outline_clear_resets_every_chapter_and_outline_file(pdir):
    reset_project(pdir, from_stage="outline", confirm=True)
    assert not (pdir / "plan" / "spine.json").exists()
    for pid in ("P01", "P02"):
        assert not (pdir / "parts" / pid / "script.json").exists()
        assert not (pdir / "parts" / pid / "audio").exists()


def test_clearing_and_regenerating_a_stage_rebuilds_it(pdir):
    from article_to_video.visual import storyboard as sb
    sb.plan_storyboard(pdir, "P01")
    reset_project(pdir, from_stage="storyboard", part_ids=["P01"], confirm=True)
    assert sb.load_storyboard(pdir, "P01") is None
    sb.plan_storyboard(pdir, "P01")
    assert sb.load_storyboard(pdir, "P01") is not None


def _page(at, label):
    return at.sidebar.radio[0].set_value(label).run()


@pytest.mark.parametrize("step", ["📝 口播稿", "🔊 配音", "🎞️ 分镜", "🗺️ 大纲"])
def test_every_stage_page_offers_the_clear_panel(tmp_path, monkeypatch, step):
    monkeypatch.setenv("ARTICLE_TO_VIDEO_HOME", str(tmp_path))
    scripted_project(tmp_path)
    at = AppTest.from_file(str(PROJECT_DIR / "app.py"), default_timeout=90).run()
    go_step(at, step)
    assert not at.exception, [e.value for e in at.exception]
    assert any(b.key and b.key.startswith("rst_") and b.key.endswith("_go") for b in at.button), step
