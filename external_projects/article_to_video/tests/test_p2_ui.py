import time

import pytest

from conftest import PROJECT_DIR
from helpers_p1 import make_project
from helpers_p2 import good_script, ready_project, ScriptLLM

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

from article_to_video.script import writer as w  # noqa: E402
from ui import jobs  # noqa: E402
from helpers_ui_nav import go_page, go_step


def test_script_page_renders_with_script(monkeypatch, tmp_path):
    monkeypatch.setenv("ARTICLE_TO_VIDEO_HOME", str(tmp_path))
    pdir = ready_project(tmp_path)
    w.write_part(pdir, "P01", ScriptLLM(good_script))
    at = AppTest.from_file(str(PROJECT_DIR / "app.py"), default_timeout=90).run()
    go_step(at, "📝 口播稿")
    assert not at.exception, [e.value for e in at.exception]
    assert any(h.value == "📝 口播稿（C1）" for h in at.header)
    assert any(b.label.startswith("写全部章") for b in at.button)


def test_script_page_without_confirmed_outline_shows_hint(monkeypatch, tmp_path):
    monkeypatch.setenv("ARTICLE_TO_VIDEO_HOME", str(tmp_path))
    from article_to_video.planning.runner import plan_outline
    pdir = make_project(tmp_path)
    plan_outline(pdir, engine="rules")
    at = AppTest.from_file(str(PROJECT_DIR / "app.py"), default_timeout=90).run()
    go_step(at, "📝 口播稿")
    assert not at.exception
    assert any("确认大纲" in i.value for i in at.info)


def test_job_can_be_stopped_and_reports_stopped(tmp_path):
    import time as _t
    from article_to_video import cancel

    def slow():
        cancel.call(_t.sleep, 30)

    assert jobs.start(tmp_path, "测试任务", slow)
    _t.sleep(0.3)
    assert jobs.status(tmp_path)["state"] == "running"
    jobs.stop(tmp_path)
    for _ in range(50):
        if jobs.status(tmp_path)["state"] != "running":
            break
        _t.sleep(0.1)
    st = jobs.status(tmp_path)
    assert st["state"] == "stopped"


def test_second_job_refused_while_running(tmp_path):
    import time as _t
    from article_to_video import cancel
    assert jobs.start(tmp_path / "x", "a", lambda: cancel.call(_t.sleep, 5))
    assert not jobs.start(tmp_path / "x", "b", lambda: None)
    jobs.stop(tmp_path / "x")
