"""P4 界面与命令行：分镜页（C2）可加载、渲染入口可用。"""
import json

import pytest

from conftest import PROJECT_DIR
from helpers_p4 import scripted_project, voiced_project

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

from article_to_video.visual import storyboard as sbm  # noqa: E402


def test_storyboard_page_without_storyboard_shows_generate_button(monkeypatch, tmp_path):
    monkeypatch.setenv("ARTICLE_TO_VIDEO_HOME", str(tmp_path))
    scripted_project(tmp_path)
    at = AppTest.from_file(str(PROJECT_DIR / "app.py"), default_timeout=120).run()
    at.sidebar.radio[0].set_value("🎞️ 分镜").run()
    assert not at.exception, [e.value for e in at.exception]
    assert any(h.value == "🎞️ 分镜（C2）" for h in at.header)
    assert any(b.label == "生成分镜（规则版）" for b in at.button)


def test_storyboard_page_with_generated_storyboard_renders_scenes(monkeypatch, tmp_path):
    monkeypatch.setenv("ARTICLE_TO_VIDEO_HOME", str(tmp_path))
    pdir, _ = scripted_project(tmp_path)
    sbm.plan_storyboard(pdir, "P01")
    at = AppTest.from_file(str(PROJECT_DIR / "app.py"), default_timeout=120).run()
    at.sidebar.radio[0].set_value("🎞️ 分镜").run()
    assert not at.exception, [e.value for e in at.exception]
    assert any("分镜" in h.value for h in at.subheader)
    assert len(at.expander) >= 5  # 视觉设置 + 各段


def test_cli_plan_and_render_round_trip(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("ARTICLE_TO_VIDEO_HOME", str(tmp_path))
    pdir, _ = voiced_project(tmp_path)
    import sys
    sys.path.insert(0, str(PROJECT_DIR / "entrypoints"))
    import plan_storyboard as ps
    import render_visuals as rv

    assert ps.main(["--id", pdir.name, "--part", "P02"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] and out["parts"]["P02"]["scenes"] > 0

    assert rv.main(["--id", pdir.name, "--part", "P02"]) == 1   # 尚未确认 C2
    err = json.loads(capsys.readouterr().out)
    assert err["ok"] is False and "C2" in err["error"]

    sbm.confirm_storyboard(pdir, "P02")
    assert rv.main(["--id", pdir.name, "--part", "P02", "--provider", "none"]) == 0
    res = json.loads(capsys.readouterr().out)
    assert res["ok"] and res["parts"]["P02"]["rendered"] > 0
    assert res["parts"]["P02"]["illustration"] == 0 and res["parts"]["P02"]["fallback"] > 0
