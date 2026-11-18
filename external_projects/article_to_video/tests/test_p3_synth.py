import json
import wave

import pytest

from article_to_video import cancel
from article_to_video.audio import synth as sy
from article_to_video.audio.synth import synthesize_part
from article_to_video.planning.errors import PlanError
from article_to_video.providers.tts import SilenceTTS
from article_to_video.script import writer as w
from helpers_p2 import good_script, ScriptLLM, ready_project


@pytest.fixture
def voiced(tmp_path):
    pdir = ready_project(tmp_path)
    w.write_part(pdir, "P01", ScriptLLM(good_script))
    w.confirm_script(pdir, "P01")
    return pdir


class CountingStub(SilenceTTS):
    def __init__(self):
        self.calls = []

    def synth(self, text, out, voice, rate="+0%"):
        self.calls.append(text)
        return super().synth(text, out, voice, rate)


class ChainLike(CountingStub):
    last_engine = "edge-tts"


def test_synth_creates_wavs_manifest_and_timeline(voiced):
    tts = CountingStub()
    tl = synthesize_part(voiced, "P01", tts)
    audio = voiced / "parts" / "P01" / "audio"
    assert (audio / "manifest.json").is_file()
    for it in tl["sentences"]:
        f = audio / f"{it['id']}.wav"
        assert f.is_file()
        with wave.open(str(f)) as wv:
            assert wv.getnframes() / wv.getframerate() == pytest.approx(it["duration"], abs=0.001)
    assert (voiced / "parts" / "P01" / "timeline.json").is_file()
    assert tl["sentences"][-1]["end"] <= tl["total_sec"]
    assert tl["warnings"] and "stub" in tl["warnings"][-1]


def test_second_run_reuses_cache(voiced):
    synthesize_part(voiced, "P01", CountingStub())
    tts = CountingStub()
    synthesize_part(voiced, "P01", tts)
    assert tts.calls == []


def test_editing_one_sentence_resynthesizes_only_that_sentence(voiced):
    synthesize_part(voiced, "P01", CountingStub())
    script = w.load_script(voiced, "P01")
    script["beats"][0]["sentences"][0]["text"] = "这是一句新的开场白。"
    w.save_script(voiced, "P01", script)
    w.confirm_script(voiced, "P01")
    tts = CountingStub()
    synthesize_part(voiced, "P01", tts)
    assert len(tts.calls) == 1 and "新的开场白" in tts.calls[0]


def test_changing_engine_chain_invalidates_cache(voiced):
    synthesize_part(voiced, "P01", CountingStub())

    class Other(CountingStub):
        name = "other-engine"

    tts = Other()
    synthesize_part(voiced, "P01", tts)
    assert len(tts.calls) > 0


def test_failing_sentence_raises_without_silence_fallback(voiced):
    class Boom(CountingStub):
        def synth(self, text, out, voice, rate="+0%"):
            if "金句" in text or len(self.calls) >= 2:
                raise RuntimeError("引擎全部失败")
            return super().synth(text, out, voice, rate)

    with pytest.raises(RuntimeError):
        synthesize_part(voiced, "P01", Boom())
    manifest = json.loads((voiced / "parts" / "P01" / "audio" / "manifest.json").read_text(encoding="utf-8"))
    assert len(manifest) == 2                              # 已完成的句子保留，下次续跑
    assert not (voiced / "parts" / "P01" / "timeline.json").exists()


def test_unconfirmed_script_cannot_be_voiced(tmp_path):
    pdir = ready_project(tmp_path)
    w.write_part(pdir, "P01", ScriptLLM(good_script))
    with pytest.raises(PlanError):
        synthesize_part(pdir, "P01", CountingStub())


def test_stop_during_synthesis_keeps_partial_manifest(voiced):
    cancel.bind(cancel.begin(voiced))

    class StopAfterOne(CountingStub):
        def synth(self, text, out, voice, rate="+0%"):
            if self.calls:
                cancel.request(voiced)
            return super().synth(text, out, voice, rate)

    with pytest.raises(cancel.Cancelled):
        synthesize_part(voiced, "P01", StopAfterOne())
    assert not (voiced / "parts" / "P01" / "timeline.json").exists()
    assert (voiced / "parts" / "P01" / "audio" / "manifest.json").is_file()


def test_preview_does_not_touch_project_audio(voiced):
    out = sy.preview(voiced, "试听一句话。", "zh-CN-YunjianNeural", "+0%", CountingStub())
    assert out.is_file() and out.parent.name == "preview"
    assert not (voiced / "parts" / "P01" / "timeline.json").exists()


def test_settings_roundtrip(voiced):
    cfg, warns = sy.update_tts_settings(voiced, {"narration_voice": "zh-CN-YunxiNeural",
                                                 "engine_chain": ["edge-tts"]})
    assert cfg["narration_voice"] == "zh-CN-YunxiNeural"
    assert sy.project_tts_config(voiced)["engine_chain"] == ["edge-tts"]
