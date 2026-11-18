"""R4c：业务页面收敛——设置表单已删除，改为只读的生效设置；导入、驾驶舱、大纲的默认值来自设置。"""
import pytest

from article_to_video import app_settings as S
from conftest import PROJECT_DIR
from helpers_p1 import make_project
from helpers_p2 import ready_project

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("ARTICLE_TO_VIDEO_HOME", str(tmp_path))
    return tmp_path


def _app(page):
    at = AppTest.from_file(str(PROJECT_DIR / "app.py"), default_timeout=90).run()
    at.sidebar.radio[0].set_value(page).run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def _texts(at):
    return [c.value for c in at.caption] + [m.value for m in at.markdown]


def test_voice_page_shows_effective_settings_not_a_form(home):
    from article_to_video.script import writer as w
    from helpers_p2 import ScriptLLM, good_script
    pdir = ready_project(home)
    w.write_part(pdir, "P01", ScriptLLM(good_script))
    w.confirm_script(pdir, "P01")                       # 配音页需要已确认的口播稿才会显示设置行
    at = _app("🔊 配音")
    assert not any(b.label == "保存设置" for b in at.button)
    assert any("生效设置：主讲" in t and "在「⚙️ 设置」中修改" in t for t in _texts(at))


def test_storyboard_page_shows_visual_summary_not_a_form(home):
    ready_project(home)
    at = _app("🎞️ 分镜")
    assert not any(b.label == "保存视觉设置" for b in at.button)
    assert not any(t.label == "风格锚（所有插图提示词的固定前缀，必须保留禁文字约束）" for t in at.text_area)


def test_export_page_shows_compose_summary_not_forms(home):
    ready_project(home)
    at = _app("🎬 合成导出")
    labels = [b.label for b in at.button]
    assert not any(l.startswith("保存合成设置") or l.startswith("保存署名与水印") for l in labels)
    assert any("生效的合成设置" in t for t in _texts(at))


def test_projects_page_no_longer_edits_branding(home):
    make_project(home)
    at = _app("📚 项目")
    assert not any(b.label == "保存署名与水印" for b in at.button)
    assert any("统一到「⚙️ 设置」" in t for t in _texts(at))


def test_import_form_defaults_come_from_global_settings(home):
    S.save_global_group("settings", {"mode": "faithful", "persona": "author"})
    S.save_global_group("branding", {"watermark": {"position": "bottom_left"}})
    at = _app("📥 新建项目")
    mode = next(s for s in at.selectbox if s.label == "模式")
    persona = next(s for s in at.selectbox if s.label == "解说视角")
    pos = next(s for s in at.selectbox if s.label == "位置")
    assert mode.value == "faithful"
    assert persona.value == "author"
    assert pos.value == "bottom_left"


def test_cockpit_defaults_come_from_pipeline_settings(home):
    ready_project(home)
    S.save_global_group("pipeline", {"gear": "G1", "max_images": 7, "outline_engine": "rules"})
    at = _app("🧭 驾驶舱")
    gear = next(r for r in at.radio if r.label == "挡位")
    assert gear.value == "G1"
    budget = next(n for n in at.number_input if n.label.startswith("预算：最多生图次数"))
    assert budget.value == 7


def test_outline_engine_default_follows_settings(home):
    make_project(home)
    S.save_global_group("pipeline", {"outline_engine": "rules"})
    at = _app("🗺️ 大纲")
    eng = next(r for r in at.radio if r.label == "生成方式")
    assert eng.value == "rules"
