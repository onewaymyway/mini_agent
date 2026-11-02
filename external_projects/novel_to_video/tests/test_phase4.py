"""Phase 4 测试：F-1 门禁、人工确认、封面、转场、F-4 成片校验、G4/G5 有限恢复。"""

import json
from pathlib import Path

import pytest

from conftest import build_export
from novel_to_video import batch, gates
from novel_to_video import chapter_io as cio
from novel_to_video import project_store as ps
from novel_to_video import tool_api
from novel_to_video.media import concat_final as cf
from novel_to_video.media import ffmpeg as mf
from novel_to_video.media.cover import LAYOUTS, CoverError, render_cover
from novel_to_video.providers.image import ImageResult, StubImageProvider
from novel_to_video.providers.text import RuleTextLLM
from novel_to_video.steps import final_compose as fc
from novel_to_video.steps import global_visual as gv
from PIL import Image

from test_phase3 import FakeLLM, _fake_run_one  # 复用 Phase 3 的假 LLM 与运行器

pytestmark = pytest.mark.skipif(not (Path("/usr/bin/ffmpeg").exists() or __import__("shutil").which("ffmpeg")),
                                reason="需要 ffmpeg")


# ---------------------------------------------------------------- 纯函数

def test_g4_g5_in_policy_but_final_always_manual():
    assert "G4" in gates.GEARS and "G5" in gates.GEARS
    assert gates.is_auto("G4", "chapter_script") and gates.is_auto("G5", "shot_review")
    for g in gates.GEARS:
        assert gates.is_auto(g, "final_compose") is False


def test_retry_limits_bounded():
    assert gates.RETRY_LIMITS["G3"] == 0
    assert gates.RETRY_LIMITS["G4"] == 1
    assert gates.RETRY_LIMITS["G5"] == 3


def test_expected_duration_fade_subtracts_overlap():
    assert cf.expected_duration([10, 10, 10], 0.0, "cut") == 30
    assert cf.expected_duration([10, 10, 10], 0.0, "fade") == pytest.approx(30 - 2 * cf.FADE_SEC)
    assert cf.expected_duration([10], 3.0, "fade") == 13  # 只有一章不叠化


def test_cover_layouts_render_and_reject_unknown(tmp_path):
    bg = tmp_path / "bg.png"
    Image.new("RGB", (720, 1280), (30, 50, 80)).save(bg)
    for layout in LAYOUTS:
        out = render_cover(bg, tmp_path / f"c_{layout}.png", "铁城·第一次背叛", layout)
        im = Image.open(out)
        assert im.size == (1280, 720)          # 默认横屏 720P
    with pytest.raises(CoverError):
        render_cover(bg, tmp_path / "x.png", "标题", "diagonal")
    with pytest.raises(CoverError):
        render_cover(bg, tmp_path / "y.png", "   ", "center")


# ---------------------------------------------------------------- 3 章完整项目

@pytest.fixture
def done_project(tmp_path, monkeypatch):
    monkeypatch.setenv("NOVEL_TO_VIDEO_PROVIDERS", "stub")
    from test_phase3 import CH, _set_gear  # noqa: F401
    src = build_export(tmp_path / "src", chapters=(1, 2))
    for n in (1, 2):
        (src / "正文" / f"第{n:03d}章 测试章{n}.md").write_text(CH.format(n=n), encoding="utf-8")
    res = tool_api.create_project_from_source(str(tmp_path / "out"), str(src), title="测试")
    assert res["ok"], res
    p = Path(res["data"]["project_dir"])
    gv.visual_text_pass(p, RuleTextLLM())
    gv.portrait_pass(p, StubImageProvider())
    tool_api.set_gear(str(p), "G0")
    for n in (1, 2):
        out = batch.run_queue(p, [n], gear="G0", run_one=_fake_run_one(p))
        assert out["results"][n]["status"] == "waiting_review"
        for sh in cio.load_shots(p, n)["shots"]:
            tool_api.review_shot(str(p), n, sh["id"], "approve")
        r = tool_api.run_chapter(str(p), n)
        assert r["ok"] and r["data"]["status"] == "done", r
    return p


def test_f1_refuses_when_chapter_stale(done_project):
    assert tool_api.final_check(str(done_project))["data"]["ready"] is True
    cio.set_chapter_status(done_project, 2, "stale", "测试")
    chk = tool_api.final_check(str(done_project))["data"]
    assert chk["ready"] is False and any("第 2 章" in p for p in chk["problems"])


def test_compose_requires_manual_confirmation(done_project):
    r = tool_api.compose_book(str(done_project))
    assert not r["ok"] and "人工确认" in r["error"]["message"]


def test_compose_blocked_for_every_gear(done_project):
    for g in gates.GEARS:
        tool_api.set_gear(str(done_project), g, {"max_video_calls": 99, "max_image_calls": 99,
                                                  "max_minutes": 999} if g in ("G3", "G4", "G5") else None)
        r = tool_api.compose_book(str(done_project))
        assert not r["ok"], g  # 没有确认，任何挡位都不能合成


def test_confirm_then_compose_cut(done_project):
    assert tool_api.confirm_final(str(done_project), "看过成片")["ok"]
    r = tool_api.compose_book(str(done_project), transition="cut")
    assert r["ok"], r
    d = r["data"]
    assert d["verify"]["ok"], d["verify"]
    assert d["duration"] == pytest.approx(d["expected"], abs=0.3)
    assert (done_project / "exports" / "video.mp4").is_file()
    # 成片产生后确认失效，必须重新确认才能再次合成
    assert not fc.confirm_state(done_project)["confirmed"]


def test_confirmation_invalidated_when_chapter_changes(done_project):
    tool_api.confirm_final(str(done_project))
    # 章节成片被改动（模拟重新合成）→ 确认快照失效
    mp4 = cio.chapter_dir(done_project, 1) / "chapter_001.mp4"
    mp4.write_bytes(mp4.read_bytes() + b"\x00")
    r = tool_api.compose_book(str(done_project))
    assert not r["ok"] and "重新确认" in r["error"]["message"]


def test_cover_and_fade_compose(done_project):
    assert tool_api.set_cover(str(done_project), enabled=True, title="铁城", layout="bottom_bar",
                              duration_sec=2.0)["ok"]
    r = tool_api.render_cover(str(done_project))
    assert r["ok"], r
    assert (done_project / "global" / "assets" / "cover.png").is_file()
    tool_api.confirm_final(str(done_project))
    r = tool_api.compose_book(str(done_project), transition="fade")
    assert r["ok"], r
    d = r["data"]
    assert d["intro_sec"] == pytest.approx(2.0, abs=0.1)
    assert d["verify"]["ok"], d["verify"]
    assert d["verify"]["width"] == mf.W and d["verify"]["height"] == mf.H
    assert d["verify"]["has_audio"] is True


def test_cover_enabled_without_png_is_refused(done_project):
    tool_api.set_cover(str(done_project), enabled=True, title="铁城")
    tool_api.confirm_final(str(done_project))
    r = tool_api.compose_book(str(done_project))
    assert not r["ok"] and "cover.png" in r["error"]["message"]


def test_cover_bg_generation_failure_surfaces(tmp_path, done_project):
    tool_api.set_cover(str(done_project), enabled=True, title="铁城")

    class Failing(StubImageProvider):
        def generate(self, prompt, save_path, **kw):
            return ImageResult(ok=False, error="quota", non_retryable=True)

    with pytest.raises(fc.FinalError, match="封面背景生成失败"):
        fc.render_cover_assets(done_project, bg_provider=Failing())


def test_fade_only_between_chapters(tmp_path, done_project):
    """封面与第一章之间必须硬切：封面段时长不参与叠化计算。"""
    exp = cf.expected_duration([5.0, 5.0], 2.0, "fade")
    assert exp == pytest.approx(12.0 - cf.FADE_SEC)


def test_verify_detects_duration_mismatch(done_project, tmp_path):
    tool_api.confirm_final(str(done_project))
    r = tool_api.compose_book(str(done_project))
    assert r["ok"]
    bad = fc.verify_final(Path(r["data"]["output"]), expected=r["data"]["expected"] + 5.0)
    assert not bad["ok"] and any("时长" in p for p in bad["problems"])


# ---------------------------------------------------------------- G4 / G5 有限恢复

def _failing_then_ok(fail_times):
    calls = {"n": 0}

    def run_one(no):
        calls["n"] += 1
        if calls["n"] <= fail_times:
            return {"status": "failed", "message": "模拟失败"}
        return {"status": "waiting_review"}
    return run_one, calls


def test_g5_retries_up_to_limit(done_project):
    tool_api.set_gear(str(done_project), "G5", {"max_video_calls": 99, "max_image_calls": 99, "max_minutes": 999})
    run_one, calls = _failing_then_ok(10)
    res = batch.run_queue(done_project, [1], gear="G5", run_one=run_one)
    r = res["results"][1]
    assert r["status"] == "failed"
    assert r["recovery"]["attempts"] == gates.RETRY_LIMITS["G5"]
    assert calls["n"] == 1 + gates.RETRY_LIMITS["G5"]


def test_g5_recovers_after_transient_failure(done_project):
    tool_api.set_gear(str(done_project), "G5", {"max_video_calls": 99, "max_image_calls": 99, "max_minutes": 999})
    run_one, _ = _failing_then_ok(2)
    res = batch.run_queue(done_project, [1], gear="G5", run_one=run_one)
    assert res["results"][1]["status"] == "waiting_review"


def test_g4_tries_strategy_switch_once(done_project):
    tool_api.set_gear(str(done_project), "G4", {"max_video_calls": 99, "max_image_calls": 99, "max_minutes": 999})
    run_one, calls = _failing_then_ok(10)
    res = batch.run_queue(done_project, [1], gear="G4", run_one=run_one)
    assert res["results"][1]["recovery"]["attempts"] == gates.RETRY_LIMITS["G4"]
    assert calls["n"] == 1 + gates.RETRY_LIMITS["G4"]
    assert (cio.chapter_dir(done_project, 1) / "strategy.json").is_file()


def test_g3_does_not_retry(done_project):
    tool_api.set_gear(str(done_project), "G3", {"max_video_calls": 99, "max_image_calls": 99, "max_minutes": 999})
    run_one, calls = _failing_then_ok(10)
    res = batch.run_queue(done_project, [1], gear="G3", run_one=run_one)
    assert res["results"][1]["status"] == "failed"
    assert calls["n"] == 1


def test_strategy_limit_changes_shot_cache(done_project):
    from novel_to_video.steps.chapter_shots import strategy_block_limit
    from novel_to_video.chapter_io import MAX_BLOCK_CHARS
    assert strategy_block_limit(done_project, 1) == MAX_BLOCK_CHARS
    batch._switch_strategy(done_project, 1)
    assert strategy_block_limit(done_project, 1) == MAX_BLOCK_CHARS // 2


def test_compose_mechanical_check_on_stub_video(done_project):
    """单章 C-9 产物的时长与分辨率在 F-4 中可通过校验。"""
    v = fc.verify_final(cio.chapter_dir(done_project, 1) / "chapter_001.mp4",
                        expected=mf.probe_duration(cio.chapter_dir(done_project, 1) / "chapter_001.mp4"))
    assert v["ok"], v
