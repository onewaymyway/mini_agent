"""R20：渲染所有章的画面——就绪判断、跳过原因、单章失败隔离、停止传播、界面按钮。"""
import json

import pytest

from article_to_video import cancel
from article_to_video.audio import synth as sy
from article_to_video.planning.errors import PlanError
from article_to_video.render import compose_part as cp
from article_to_video.visual import storyboard as sbm
from conftest import PROJECT_DIR
from helpers_p4 import scripted_project
from helpers_ui_nav import go_step

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402


@pytest.fixture
def proj(tmp_path, monkeypatch):
    monkeypatch.setenv("ARTICLE_TO_VIDEO_HOME", str(tmp_path))
    pdir, _ = scripted_project(tmp_path)
    for pid in ("P01", "P02"):
        script = sy.load_script(pdir, pid)
        audio = pdir / "parts" / pid / "audio"
        audio.mkdir(parents=True, exist_ok=True)
        manifest = {}
        for b in script["beats"]:
            for i, s in enumerate(b["sentences"]):
                sid = f"{b['id']}.s{i + 1:02d}"
                (audio / f"{sid}.wav").write_bytes(b"RIFF")
                manifest[sid] = {"key": "k", "file": f"{sid}.wav", "duration": 2.0, "voice": "v1",
                                 "rate": "+0%", "engine": "stub", "text": s["text"]}
        (audio / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
        sy.rebuild_timeline(pdir, pid)
        sbm.plan_storyboard(pdir, pid, engine="rules")
        sbm.confirm_storyboard(pdir, pid)
    return pdir


def test_readiness_reasons_explain_each_skip(tmp_path, monkeypatch):
    monkeypatch.setenv("ARTICLE_TO_VIDEO_HOME", str(tmp_path))
    pdir, _ = scripted_project(tmp_path)
    assert cp.render_ready_reason(pdir, "P02") == "还没有分镜"
    sbm.plan_storyboard(pdir, "P02", engine="rules")
    assert cp.render_ready_reason(pdir, "P02") == "分镜未确认"


def test_renders_ready_chapters_and_skips_the_rest(proj):
    out = cp.render_all_parts(proj, ["P01", "P03"], provider=None)   # P03 没有分镜：跳过
    rows = {r["part"]: r for r in out["results"]}
    assert rows["P01"]["action"] == "rendered"
    assert rows["P03"] == {"part": "P03", "action": "skipped", "reason": "还没有分镜"}
    assert (proj / "parts" / "P01" / "visuals" / "manifest.json").is_file()
    assert out["rendered"] == 1 and out["skipped"] == 1 and out["failed"] == 0


def test_one_failing_chapter_does_not_stop_the_rest(proj, monkeypatch):
    real = cp.render_part
    attempted = []

    def flaky(pdir, pid, **kw):
        attempted.append(pid)
        if pid == "P01":
            raise PlanError("P01 图片服务超时（测试）")
        return real(pdir, pid, **kw)

    monkeypatch.setattr(cp, "render_part", flaky)
    with pytest.raises(PlanError, match="P01"):
        cp.render_all_parts(proj, ["P01", "P02"], provider=None)
    assert attempted == ["P01", "P02"]                       # P02 仍然渲染了
    assert (proj / "parts" / "P02" / "visuals" / "manifest.json").is_file()


def test_stop_request_propagates(proj, monkeypatch):
    def stop(pdir, pid, **kw):
        raise cancel.Cancelled()
    monkeypatch.setattr(cp, "render_part", stop)
    with pytest.raises(cancel.Cancelled):
        cp.render_all_parts(proj, ["P01"], provider=None)


def test_storyboard_page_has_render_all_button(tmp_path, monkeypatch):
    monkeypatch.setenv("ARTICLE_TO_VIDEO_HOME", str(tmp_path))
    scripted_project(tmp_path)
    at = AppTest.from_file(str(PROJECT_DIR / "app.py"), default_timeout=90).run()
    go_step(at, "🎞️ 分镜")
    assert not at.exception, [e.value for e in at.exception]
    assert any(b.key == "batch_render_all" for b in at.button)
