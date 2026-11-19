"""R19：改时间设置只重建时间轴——不重新配音、不重写分镜、不重新生图；缺句配音时拒绝。"""
import hashlib
import json

import pytest

from article_to_video.audio import synth as sy
from article_to_video.planning.errors import PlanError
from article_to_video.visual import storyboard as sbm
from helpers_p4 import scripted_project


@pytest.fixture
def voiced(tmp_path, monkeypatch):
    """假装已配音：每句有一个音频文件与配音清单（时长固定），章 P01 有分镜与图片。"""
    monkeypatch.setenv("ARTICLE_TO_VIDEO_HOME", str(tmp_path))
    pdir, _ = scripted_project(tmp_path)
    script = sy.load_script(pdir, "P01")
    audio = pdir / "parts" / "P01" / "audio"
    audio.mkdir(parents=True, exist_ok=True)
    manifest = {}
    for b in script["beats"]:
        for i, s in enumerate(b["sentences"]):
            sid = f"{b['id']}.s{i + 1:02d}"
            fname = f"{sid}.wav"
            (audio / fname).write_bytes(b"RIFF" + sid.encode())
            manifest[sid] = {"key": "k", "file": fname, "duration": 2.0, "voice": "v1",
                             "rate": "+0%", "engine": "stub", "text": s["text"]}
    (audio / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    img = pdir / "parts" / "P01" / "images"
    img.mkdir(parents=True, exist_ok=True)
    (img / "scene_v001.png").write_bytes(b"png")
    sbm.plan_storyboard(pdir, "P01")
    return pdir


def _h(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_rebuild_applies_new_card_hold_without_touching_audio_or_storyboard(voiced):
    audio_before = {p.name: _h(p) for p in (voiced / "parts" / "P01" / "audio").glob("*.wav")}
    sb_before = sbm.load_storyboard(voiced, "P01")["input_sha"]
    img_before = _h(voiced / "parts" / "P01" / "images" / "scene_v001.png")

    sy.update_tts_settings(voiced, {"chapter_card_sec": 5.0})
    tl = sy.rebuild_timeline(voiced, "P01")

    assert tl["lead_in_sec"] == pytest.approx(5.0)
    assert tl["sentences"][0]["start"] == pytest.approx(5.0)
    assert {p.name: _h(p) for p in (voiced / "parts" / "P01" / "audio").glob("*.wav")} == audio_before
    assert sbm.load_storyboard(voiced, "P01")["input_sha"] == sb_before
    assert _h(voiced / "parts" / "P01" / "images" / "scene_v001.png") == img_before
    assert sbm.storyboard_status(voiced, "P01")["stale"] is False     # 时间设置不让分镜过期


def test_gap_and_tail_changes_are_applied(voiced):
    sy.update_tts_settings(voiced, {"sentence_gap_sec": 0.5, "tail_sec": 2.0})
    tl = sy.rebuild_timeline(voiced, "P01")
    first, second = tl["sentences"][0], tl["sentences"][1]
    assert second["start"] - first["end"] == pytest.approx(0.5, abs=1e-3)   # 句间停顿精确生效
    assert tl["tail_sec"] == pytest.approx(2.0)


def test_rebuild_refuses_when_a_sentence_has_no_audio(voiced):
    (voiced / "parts" / "P01" / "audio" / "P01.b01.s01.wav").unlink()
    with pytest.raises(PlanError, match="没有配音"):
        sy.rebuild_timeline(voiced, "P01")


def test_rebuild_refuses_unconfirmed_script(voiced):
    from article_to_video.project_store import write_json_atomic
    s = sy.load_script(voiced, "P01")
    s["confirmed"] = False
    write_json_atomic(voiced / "parts" / "P01" / "script.json", s)
    with pytest.raises(PlanError, match="没有确认"):
        sy.rebuild_timeline(voiced, "P01")


def test_stale_old_timeline_is_replaced(voiced):
    old = sy.rebuild_timeline(voiced, "P01")
    assert old["lead_in_sec"] == pytest.approx(3.5)
    sy.update_tts_settings(voiced, {"chapter_card_sec": 3.0, "lead_in_sec": 0.0})
    new = sy.rebuild_timeline(voiced, "P01")
    assert new["lead_in_sec"] == pytest.approx(3.0)
    assert sy.load_timeline(voiced, "P01")["lead_in_sec"] == pytest.approx(3.0)
