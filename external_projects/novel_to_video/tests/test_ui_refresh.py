"""回归：后台任务完成后，审核区域必须显示最新数据（不能被旧的控件状态盖住）。"""

from pathlib import Path

from conftest import build_export
from novel_to_video import tool_api
from novel_to_video.providers.text import RuleTextLLM
from novel_to_video.steps import global_visual as gv
from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).resolve().parent.parent / "app.py")


def _project(tmp_path, monkeypatch):
    monkeypatch.setenv("NOVEL_TO_VIDEO_PROVIDERS", "stub")
    src = build_export(tmp_path / "src", chapters=(1,))
    out = tmp_path / "out"
    res = tool_api.create_project_from_source(str(out), str(src), title="t")
    return out, Path(res["data"]["project_dir"])


def _anchor_widgets(at):
    return [t for t in at.text_area if t.label.startswith("visual_anchor_en")]


def test_assets_detail_shows_text_written_after_page_was_rendered(tmp_path, monkeypatch):
    out, proj = _project(tmp_path, monkeypatch)
    at = AppTest.from_file(APP, default_timeout=120).run()
    at.sidebar.radio[0].set_value("🎭 全书素材").run()
    at.text_input(key="assets_root").input(str(out)).run()
    # 第一次渲染：设定为空
    assert all(t.value == "" for t in _anchor_widgets(at))

    # 后台 G-2 完成（这里直接调用同一个步骤），随后页面重新渲染
    gv.visual_text_pass(proj, RuleTextLLM())
    at.run()

    values = [t.value for t in _anchor_widgets(at)]
    assert values and all(v.strip() for v in values), values


def test_gender_radio_shows_confirmed_choice_after_save(tmp_path, monkeypatch):
    from novel_to_video import gender_review
    out, proj = _project(tmp_path, monkeypatch)
    at = AppTest.from_file(APP, default_timeout=120).run()
    at.sidebar.radio[0].set_value("⚧ 性别确认").run()
    at.text_input(key="gen_root").input(str(out)).run()
    gender_review.suggest_all(proj)
    at.run()
    gender_review.confirm(proj, "char_002", "female")
    at.run()
    radio = next(r for r in at.radio if r.key.startswith("gen_char_002_"))
    assert radio.value == "female"
