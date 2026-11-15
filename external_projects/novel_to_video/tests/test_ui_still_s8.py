"""动态漫模式 S8：界面（创建页选择渲染方式、挡位页设置与预览动效、章节页显示场景图）与对应接口。"""
import json
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from conftest import build_export
from novel_to_video import chapter_pipeline as cp
from novel_to_video import tool_api
from novel_to_video.providers.image import StubImageProvider
from novel_to_video.steps import chapter_stills as cs
from novel_to_video.steps import chapter_segments as cseg
from novel_to_video import shot_review as sr
from test_still_stills_s3 import _prepare, _project, _shots

APP = str(Path(__file__).resolve().parent.parent / "app.py")
PREVIEW_CFG = dict(motion="auto_alternate", motion_strength="slow", transition="fade", transition_sec=0.4)


def _meta(p: Path) -> dict:
    return json.loads((p / "project.json").read_text(encoding="utf-8"))


@pytest.fixture
def still_with_stills(tmp_path):
    p = _project(tmp_path)              # 动态漫项目，根目录为 tmp_path/out
    _prepare(p)
    cs.run_stills(p, 1, StubImageProvider())
    return p


@pytest.fixture
def video_project(tmp_path):
    src = build_export(tmp_path / "src", chapters=(1,))
    res = tool_api.create_project_from_source(str(tmp_path / "vout"), str(src), title="视频")
    return Path(res["data"]["project_dir"])


# ---------- 接口：设置

def test_set_still_settings_persists_and_validates(still_with_stills):
    r = tool_api.set_still_settings(str(still_with_stills), motion="pan_right", motion_strength="medium",
                                    transition="dissolve", transition_sec=0.3, silent_shot_sec=4)
    assert r["ok"], r
    s = _meta(still_with_stills)["still"]
    assert s["motion"] == "pan_right" and s["transition"] == "dissolve" and s["silent_shot_sec"] == 4


@pytest.mark.parametrize("kwargs", [
    dict(motion="spiral", motion_strength="slow", transition="fade", transition_sec=0.4, silent_shot_sec=3),
    dict(motion="none", motion_strength="wild", transition="fade", transition_sec=0.4, silent_shot_sec=3),
    dict(motion="none", motion_strength="slow", transition="dissolve", transition_sec=0.5, silent_shot_sec=3),
    dict(motion="none", motion_strength="slow", transition="wipe", transition_sec=0.4, silent_shot_sec=3),
    dict(motion="none", motion_strength="slow", transition="fade", transition_sec=0.4, silent_shot_sec=0),
])
def test_invalid_still_settings_rejected_and_not_saved(still_with_stills, kwargs):
    before = _meta(still_with_stills).get("still")
    r = tool_api.set_still_settings(str(still_with_stills), **kwargs)
    assert not r["ok"]
    assert _meta(still_with_stills).get("still") == before


def test_still_settings_refused_for_video_project(video_project):
    r = tool_api.set_still_settings(str(video_project), motion="none", motion_strength="slow",
                                    transition="cut", transition_sec=0.4, silent_shot_sec=3)
    assert not r["ok"] and "动态漫" in r["error"]["message"]


# ---------- 接口：预览

def test_preview_requires_a_scene_image_first(tmp_path):
    p = _project(tmp_path / "np")
    _prepare(p)                                    # 还没有场景图
    sid = _shots(p)[0]["id"]
    r = tool_api.preview_motion(str(p), 1, sid, **PREVIEW_CFG)
    assert not r["ok"] and "场景图" in r["error"]["message"]


def test_preview_fade_is_about_three_seconds(still_with_stills):
    """淡化是黑场过渡、不重叠：总时长约 3 秒。"""
    from novel_to_video.media import ffmpeg as mf
    sid = _shots(still_with_stills)[0]["id"]
    r = tool_api.preview_motion(str(still_with_stills), 1, sid, **PREVIEW_CFG)
    assert r["ok"], r
    out = still_with_stills / r["data"]["path"]
    assert out.is_file() and out.parent.name == "previews"
    assert abs(mf.probe_duration(out) - 3.0) < 0.15
    assert abs(r["data"]["seconds"] - mf.probe_duration(out)) < 0.1


def test_preview_dissolve_is_shorter_by_the_overlap(still_with_stills):
    """叠化让两段画面重叠：总时长 = 3 秒 - 叠化时长。"""
    from novel_to_video.media import ffmpeg as mf
    sid = _shots(still_with_stills)[0]["id"]
    r = tool_api.preview_motion(str(still_with_stills), 1, sid, motion="auto_alternate", motion_strength="slow",
                                transition="dissolve", transition_sec=0.4)
    assert r["ok"], r
    assert abs(mf.probe_duration(still_with_stills / r["data"]["path"]) - (3.0 - 0.4)) < 0.15


def test_preview_cut_is_full_three_seconds(still_with_stills):
    from novel_to_video.media import ffmpeg as mf
    sid = _shots(still_with_stills)[0]["id"]
    r = tool_api.preview_motion(str(still_with_stills), 1, sid, motion="push_in", motion_strength="slow",
                                transition="cut", transition_sec=0.4)
    assert r["ok"]
    assert abs(mf.probe_duration(still_with_stills / r["data"]["path"]) - 3.0) < 0.15


def test_preview_does_not_touch_review_state(still_with_stills):
    before = sr.shot_states(still_with_stills, 1)
    sid = _shots(still_with_stills)[0]["id"]
    assert tool_api.preview_motion(str(still_with_stills), 1, sid, **PREVIEW_CFG)["ok"]
    assert sr.shot_states(still_with_stills, 1) == before


def test_preview_refused_for_video_project(video_project):
    r = tool_api.preview_motion(str(video_project), 1, "s001", **PREVIEW_CFG)
    assert not r["ok"]


# ---------- 接口：章节视图给出场景图

def test_chapter_view_exposes_current_scene_image(still_with_stills):
    v = cp.chapter_view(still_with_stills, 1)
    shots = v["shots"]
    assert shots and all(sh.get("still") for sh in shots)
    assert all((still_with_stills / sh["still"]).is_file() for sh in shots)


# ---------- 界面（AppTest）

def test_import_page_creates_still_project_when_chosen(tmp_path):
    src = build_export(tmp_path / "src")
    out = tmp_path / "out"
    at = AppTest.from_file(APP, default_timeout=60).run()
    at.sidebar.radio[0].set_value("📥 导入导出目录").run()
    at.text_input(key="imp_src").input(str(src)).run()
    at.text_input(key="imp_root").input(str(out)).run()
    at.radio(key="imp_mode").set_value("动态漫（场景图 + 动效 + 配音字幕）").run()
    at.button(key="imp_check").click().run()
    at.button(key="imp_create").click().run()
    assert not at.exception, at.exception
    (proj,) = list(out.iterdir())
    assert _meta(proj)["render_mode"] == "still"


def test_import_page_default_is_video(tmp_path):
    src = build_export(tmp_path / "src")
    out = tmp_path / "out"
    at = AppTest.from_file(APP, default_timeout=60).run()
    at.sidebar.radio[0].set_value("📥 导入导出目录").run()
    at.text_input(key="imp_src").input(str(src)).run()
    at.text_input(key="imp_root").input(str(out)).run()
    at.button(key="imp_check").click().run()
    at.button(key="imp_create").click().run()
    (proj,) = list(out.iterdir())
    assert _meta(proj)["render_mode"] == "video"


def _gear(at, root):
    at.sidebar.radio[0].set_value("🛠️ 设置与队列").run()
    at.text_input(key="gear_root").input(str(root)).run()
    return at


def test_gear_page_saves_still_settings(still_with_stills):
    at = _gear(AppTest.from_file(APP, default_timeout=60).run(), still_with_stills.parent)
    assert not at.exception, at.exception
    assert any("渲染方式与动态漫设置" in m.value for m in at.markdown)
    at.selectbox(key="still_motion").set_value("none").run()
    at.button(key="still_save").click().run()
    assert not at.exception, at.exception
    assert _meta(still_with_stills)["still"]["motion"] == "none"


def test_gear_page_preview_shows_a_video(still_with_stills):
    at = _gear(AppTest.from_file(APP, default_timeout=60).run(), still_with_stills.parent)
    at.button(key="still_prev_go").click().run()
    assert not at.exception, at.exception
    assert len(at.get("video")) >= 1
    assert list((still_with_stills / "exports" / "previews").glob("motion_preview_*.mp4"))


def test_gear_page_hides_still_section_for_video_project(video_project):
    at = _gear(AppTest.from_file(APP, default_timeout=60).run(), video_project.parent)
    assert not at.exception, at.exception
    assert not any(w.key == "still_motion" for w in at.selectbox)
