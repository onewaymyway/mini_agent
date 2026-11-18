"""R4b：设置页——全局默认与当前项目两个标签、来源显示、覆盖保存、恢复默认、受影响阶段。"""
import pytest

from article_to_video import app_settings as S
from conftest import PROJECT_DIR
from helpers_p1 import make_project

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("ARTICLE_TO_VIDEO_HOME", str(tmp_path))
    return tmp_path


def _settings_app(home):
    at = AppTest.from_file(str(PROJECT_DIR / "app.py"), default_timeout=90).run()
    at.sidebar.radio[0].set_value("⚙️ 设置").run()
    return at


def test_settings_page_renders_both_tabs(home):
    make_project(home)
    at = _settings_app(home)
    assert not at.exception, [e.value for e in at.exception]
    assert any(h.value == "⚙️ 设置" for h in at.header)
    assert len(at.tabs) == 2


def test_project_tab_shows_source_labels(home):
    make_project(home)
    S.save_global_group("pipeline", {"gear": "G1"})
    at = _settings_app(home)
    assert not at.exception
    captions = [c.value for c in at.caption]
    assert any("来源：全局默认" in c for c in captions)
    assert any("来源：内置默认" in c for c in captions)


def test_global_save_stores_only_differences(home):
    make_project(home)
    at = _settings_app(home)
    # 表单控件的值在提交时一并生效：设置与点击之间不能再运行一次
    gear = next(s for s in at.selectbox if s.key == "g:pipeline:gear")
    gear.set_value("G2")
    save = next(b for b in at.button if b.label == "保存全局默认：流水线（挡位与预算）")
    save.click().run()
    assert not at.exception, [e.value for e in at.exception]
    assert S.load_global_raw()[0]["pipeline"] == {"gear": "G2"}


def test_project_override_then_group_reset_restores_global(home):
    pdir = make_project(home)
    S.save_global_group("settings", {"persona": "author"})
    S.set_field(pdir, "settings", "mode", "faithful")
    assert S.source_of(pdir, "settings", "mode") == "项目"
    S.reset_group(pdir, "settings")
    assert S.effective_group(pdir, "settings")["mode"] == "condensed"
    assert S.effective_group(pdir, "settings")["persona"] == "author"


def test_affected_stages_and_changed_paths_are_reported():
    before = {"video": {"resolution": "1080P", "orientation": "landscape"}}
    after = {"video": {"resolution": "720P", "orientation": "landscape"}}
    assert S.changed_paths(before, after) == ["video.resolution"]
    assert "画面渲染" in S.affected_stages("settings", "video.resolution")
    assert "不改动已生成" in S.affected_stages("pipeline", "gear")


def test_branding_identity_is_project_only_in_the_page(home):
    make_project(home)
    at = _settings_app(home)
    labels = [t.label for t in at.text_input]
    assert "作者名（留空则不署名）" in labels          # 项目标签页
    assert not any(t.label.startswith("作者名") for t in at.tabs[0].text_input)  # 全局标签页不出现
