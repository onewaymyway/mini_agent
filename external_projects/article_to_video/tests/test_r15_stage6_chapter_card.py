"""章封面停留：至少 3 秒；旁白在封面之后开始；旧的时间轴/画面不再被静默合成。"""
import json

import pytest

from article_to_video.audio.config import CHAPTER_CARD_MIN_SEC, normalize_tts
from article_to_video.audio.timeline import build_timeline
from article_to_video.compose.ffrun import ComposeError
from article_to_video.compose.part import compose_part
from article_to_video.visual import storyboard as sbm
from helpers_p4 import scripted_project


def test_default_card_hold_is_above_three_seconds_and_floor_is_enforced():
    cfg, _ = normalize_tts(None)
    assert cfg["chapter_card_sec"] == 3.5 and cfg["chapter_card_sec"] > CHAPTER_CARD_MIN_SEC
    assert normalize_tts({"chapter_card_sec": 1.0})[0]["chapter_card_sec"] == CHAPTER_CARD_MIN_SEC
    assert normalize_tts({"chapter_card_sec": 99})[0]["chapter_card_sec"] == 10.0


def _script():
    return {"part": "P01", "beats": [{"id": "P01.b01", "role": "claim", "pace": "normal", "pause_after_ms": 300,
                                      "sentences": [{"text": "第一句"}, {"text": "第二句"}]}]}


def test_narration_starts_after_chapter_card_hold():
    cfg, _ = normalize_tts(None)
    dur = {"P01.b01.s01": 2.0, "P01.b01.s02": 2.0}
    voices = {k: "v" for k in dur}
    engines = {k: "stub" for k in dur}
    tl = build_timeline(_script(), dur, cfg, voices, engines)
    assert tl["lead_in_sec"] == pytest.approx(3.5)
    assert tl["sentences"][0]["start"] == pytest.approx(3.5)


def test_compose_refuses_timing_from_before_the_fix(tmp_path, monkeypatch):
    monkeypatch.setenv("ARTICLE_TO_VIDEO_HOME", str(tmp_path))
    pdir, _ = scripted_project(tmp_path)
    sb = sbm.plan_storyboard(pdir, "P01")
    vis = pdir / "parts" / "P01" / "visuals"
    vis.mkdir(parents=True, exist_ok=True)
    (vis / "manifest.json").write_text(json.dumps({"storyboard_input_sha": sb["input_sha"],
                                                   "chapter": {"end": 0.5}, "scenes": []}), encoding="utf-8")
    (pdir / "parts" / "P01" / "timeline.json").write_text(
        json.dumps({"schema": 1, "part": "P01", "lead_in_sec": 0.5, "total_sec": 10, "sentences": []}),
        encoding="utf-8")
    with pytest.raises(ComposeError, match="章封面停留只有 0.5 秒"):
        compose_part(pdir, "P01")
