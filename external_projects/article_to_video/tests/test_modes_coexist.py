"""两种生成方案在同一篇文章上并存：各自的产物互不覆盖、互不删除。"""
import json

from article_to_video.audio.synth import synthesize_script
from article_to_video.deck.compose import compose_deck_part
from article_to_video.pipeline.reset import reset_project
from article_to_video.providers.tts import SilenceTTS
from test_deck_phase4 import small_project  # noqa: F401  （新方案已完整跑通到配音的项目）


def _classic_outputs(pdir):
    """模拟现有方案在同一项目里的产物（路径与 pipeline 写入的位置一致）。"""
    part = pdir / "parts" / "P01"
    (part / "audio").mkdir(parents=True, exist_ok=True)
    (part / "audio" / "P01.b01.s01.wav").write_bytes(b"classic")
    (part / "audio" / "manifest.json").write_text("{}", encoding="utf-8")
    for name in ("script.json", "timeline.json", "storyboard.json"):
        (part / name).write_text("{}", encoding="utf-8")
    (part / "visuals").mkdir(exist_ok=True)
    (part / "visuals" / "manifest.json").write_text("{}", encoding="utf-8")
    (part / "video").mkdir(exist_ok=True)
    (part / "video" / "part.mp4").write_bytes(b"classic")
    (pdir / "exports").mkdir(exist_ok=True)
    (pdir / "exports" / "测试文章.mp4").write_bytes(b"classic")


def test_both_schemes_keep_their_outputs_side_by_side(small_project):
    pdir = small_project
    _classic_outputs(pdir)
    deck_files = {p: (pdir / p).read_bytes() for p in
                  ["parts/P01/deck_timeline.json", "parts/P01/deck/audio/manifest.json"]}
    (pdir / "exports" / "测试文章_新方案.mp4").write_bytes(b"deck")

    removed = reset_project(pdir, from_stage="script", confirm=True)

    # 现有方案的产物被清空
    assert not (pdir / "parts/P01/audio").exists()
    assert not (pdir / "parts/P01/script.json").exists()
    assert not (pdir / "parts/P01/video").exists()
    assert not (pdir / "exports/测试文章.mp4").exists()
    # 新方案的产物完好
    for p, data in deck_files.items():
        assert (pdir / p).read_bytes() == data
    assert (pdir / "parts/P01/deck/audio").is_dir()
    assert (pdir / "exports/测试文章_新方案.mp4").read_bytes() == b"deck"
    assert "exports/测试文章_新方案.mp4" not in removed          # 重置的删除清单里不含新方案成片
    assert "exports/测试文章.mp4" in removed


def test_deck_still_composes_after_the_classic_reset(small_project):
    pdir = small_project
    _classic_outputs(pdir)
    reset_project(pdir, from_stage="script", confirm=True)
    info = compose_deck_part(pdir, "P01")                 # 新方案的音频未被删除，仍可合成
    assert info["duration"] > 0


def test_classic_synthesis_does_not_touch_the_deck_manifest(small_project):
    pdir = small_project
    deck_manifest = pdir / "parts/P01/deck/audio/manifest.json"
    before = deck_manifest.read_bytes()
    classic_script = {"schema": 1, "part": "P01", "title": "现有", "role": "claim",
                      "beats": [{"id": "P01.b01", "role": "hook", "source_refs": ["s01"], "pace": "normal",
                                 "pause_after_ms": 300,
                                 "sentences": [{"text": "现有方案的一句话。", "tts": None, "emphasis": [],
                                                "quote": False}]}]}
    tl = synthesize_script(pdir, "P01", classic_script, SilenceTTS())
    assert (pdir / "parts/P01/audio/manifest.json").is_file()
    assert deck_manifest.read_bytes() == before
    assert all(s["id"].startswith("P01.b01") for s in tl["sentences"])
    entries = json.loads(deck_manifest.read_text(encoding="utf-8"))
    assert entries and all(k.startswith("P01.S") for k in entries)
