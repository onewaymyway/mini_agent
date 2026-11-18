"""分镜页默认引擎为模型版；尚未生成分镜时指标不显示误导性的数字与引擎。"""
from conftest import PROJECT_DIR
from helpers_ui_nav import go_step
from helpers_p4 import scripted_project


def _metric(at, label):
    return next(m.value for m in at.metric if m.label == label)


def test_storyboard_defaults_to_model_engine_and_shows_dash_before_generation(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest
    monkeypatch.setenv("ARTICLE_TO_VIDEO_HOME", str(tmp_path))
    scripted_project(tmp_path)
    at = AppTest.from_file(str(PROJECT_DIR / "app.py"), default_timeout=90).run()
    go_step(at, "🎞️ 分镜")
    assert not at.exception, [e.value for e in at.exception]
    radio = next(r for r in at.radio if r.label == "分镜引擎")
    assert radio.value.startswith("模型版")
    assert _metric(at, "需生成图片") == "—"
    assert _metric(at, "引擎") == "—"
    assert _metric(at, "状态") == "未生成"
