import pytest

from article_to_video.audio.config import normalize_tts, rate_for
from article_to_video.audio.timeline import assign_voices, build_timeline
from article_to_video.providers.tts import ChainTTS, sanitize_tts_text, has_speakable
from article_to_video import cancel


def _script():
    return {"part": "P01", "beats": [
        {"id": "P01.b01", "role": "hook", "pace": "normal", "pause_after_ms": 400,
         "sentences": [{"text": "开场一句。"}, {"text": "开场第二句。"}]},
        {"id": "P01.b02", "role": "claim", "pace": "slow", "pause_after_ms": 250,
         "sentences": [{"text": "金句原文。", "quote": True}, {"text": "解释一句。"}]},
    ]}


CFG = {"narration_voice": "N", "quote_voice": "Q", "max_quotes_per_part": 2,
       "lead_in_sec": 0.5, "tail_sec": 0.5, "sentence_gap_sec": 0.15}


def test_timeline_invariants():
    sc = _script()
    ids = [f"{b['id']}.s{i+1:02d}" for b in sc["beats"] for i in range(len(b["sentences"]))]
    durs = {sid: 2.0 + i * 0.5 for i, sid in enumerate(ids)}
    voices = {sid: "N" for sid in ids}
    engines = {sid: "edge-tts" for sid in ids}
    tl = build_timeline(sc, durs, CFG, voices, engines)
    items = tl["sentences"]
    assert items[0]["start"] == CFG["lead_in_sec"]
    for it in items:
        assert it["end"] == pytest.approx(it["start"] + it["duration"], abs=0.002)
    for a, b in zip(items, items[1:]):
        assert b["start"] == pytest.approx(a["end"] + a["gap_after"], abs=0.002)
    last = items[-1]
    assert tl["total_sec"] == pytest.approx(last["end"] + last["gap_after"] + CFG["tail_sec"], abs=0.002)


def test_beat_pause_is_used_at_end_of_beat_only():
    sc = _script()
    ids = [f"{b['id']}.s{i+1:02d}" for b in sc["beats"] for i in range(len(b["sentences"]))]
    tl = build_timeline(sc, {s: 1.0 for s in ids}, CFG, {s: "N" for s in ids}, {s: "x" for s in ids})
    gaps = {x["id"]: x["gap_after"] for x in tl["sentences"]}
    assert gaps["P01.b01.s01"] == CFG["sentence_gap_sec"]
    assert gaps["P01.b01.s02"] == 0.4
    assert gaps["P01.b02.s02"] == 0.25


def test_quote_voice_limit_falls_back_with_warning():
    sc = {"part": "P01", "beats": [{"id": "P01.b01", "role": "claim", "pace": "normal",
                                    "pause_after_ms": 300,
                                    "sentences": [{"text": f"金{i}", "quote": True} for i in range(4)]}]}
    cfg = {**CFG, "max_quotes_per_part": 2}
    voices, warns = assign_voices(sc, cfg)
    assert [voices[f"P01.b01.s{i:02d}"] for i in range(1, 5)] == ["Q", "Q", "N", "N"]
    assert len(warns) == 2


def test_rate_mapping():
    assert rate_for("slow", "claim") == "-10%"
    assert rate_for("fast", "claim") == "+10%"
    assert rate_for("normal", "hook") == "+5%"
    assert rate_for("normal", "claim") == "+0%"


def test_normalize_tts_fixes_bad_values():
    cfg, warns = normalize_tts({"narration_voice": "bogus", "engine_chain": ["nope"],
                                "max_quotes_per_part": 99})
    assert cfg["narration_voice"] == "zh-CN-YunjianNeural"
    assert cfg["engine_chain"] == ["cosyvoice", "edge-tts"]
    assert cfg["max_quotes_per_part"] == 10
    assert len(warns) >= 2


def test_sanitize_and_speakable():
    assert sanitize_tts_text("a\u200bb  \n c") == "ab c"
    assert has_speakable("你好") and has_speakable("GPU")
    assert not has_speakable("……！？")


class _Eng:
    def __init__(self, name, ok=True, log=None):
        self.name, self.ok, self.log = name, ok, log if log is not None else []

    def synth(self, text, out, voice, rate="+0%"):
        self.log.append(self.name)
        if not self.ok:
            raise RuntimeError(f"{self.name} 挂了")
        out.write_bytes(b"x")
        return 1.0


def test_chain_falls_back_in_order_and_records_engine(tmp_path):
    log = []
    chain = ChainTTS([_Eng("a", ok=False, log=log), _Eng("b", log=log)])
    assert chain.synth("你好", tmp_path / "x.wav", "v") == 1.0
    assert log == ["a", "b"] and chain.last_engine == "b"


def test_chain_all_fail_raises_listing_every_reason(tmp_path):
    chain = ChainTTS([_Eng("a", ok=False), _Eng("b", ok=False)])
    with pytest.raises(RuntimeError) as exc:
        chain.synth("你好", tmp_path / "x.wav", "v")
    assert "a 失败" in str(exc.value) and "b 失败" in str(exc.value)
    assert not (tmp_path / "x.wav").exists()


def test_chain_does_not_swallow_cancel(tmp_path):
    ev = cancel.begin(tmp_path)
    cancel.bind(ev)

    class StopEng(_Eng):
        def synth(self, *a, **k):
            cancel.request(tmp_path)
            raise cancel.Cancelled("stop")

    with pytest.raises(cancel.Cancelled):
        ChainTTS([StopEng("a"), _Eng("b")]).synth("你好", tmp_path / "x.wav", "v")
