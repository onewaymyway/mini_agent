"""edge-tts NoAudioReceived：错误带请求参数；配音失败时 LLM 改写文本后重试。"""
import json
from pathlib import Path

import pytest

from novel_to_video.providers import tts as tts_mod
from novel_to_video.steps import chapter_audio as ca


class NoAudioReceived(Exception):
    pass


def test_noaudio_error_contains_request_params(monkeypatch, tmp_path):
    import edge_tts

    class _Bad:
        def __init__(self, text, voice, proxy=None):
            pass

        async def save(self, path):
            raise NoAudioReceived("No audio was received. Please verify that your parameters are correct.")
    monkeypatch.setattr(edge_tts, "Communicate", _Bad)
    monkeypatch.setattr(tts_mod, "NO_AUDIO_LOCAL_RETRIES", 0)
    with pytest.raises(RuntimeError) as e:
        tts_mod.EdgeTTS().synth("他说：好的", tmp_path / "a.wav", "zh-CN-YunjianNeural")
    msg = str(e.value)
    assert "请求参数" in msg and "zh-CN-YunjianNeural" in msg and "他说：好的" in msg and "NoAudioReceived" in msg


def test_unspeakable_text_fails_fast_without_request(tmp_path):
    with pytest.raises(RuntimeError) as e:
        tts_mod.EdgeTTS().synth("……\u200b！？", tmp_path / "a.wav", "zh-CN-YunjianNeural")
    assert "没有可朗读" in str(e.value) and "non_speakable_codepoints" in str(e.value)


def test_sanitize_removes_invisible_chars():
    assert tts_mod.sanitize_tts_text("你\u200b好\x07 吗") == "你好 吗"


class _FakeTTS(tts_mod.TTSProvider):
    name = "fake"

    def __init__(self):
        self.seen = []

    def synth(self, text, out_wav, voice):
        self.seen.append(text)
        if "……" in text and not any(c.isalnum() for c in text):
            raise RuntimeError("edge-tts 没有返回音频。请求参数：" + json.dumps({"text": text, "voice": voice}, ensure_ascii=False))
        Path(out_wav).write_bytes(b"x")
        return 1.0


class _LLM:
    def __init__(self):
        self.prompts = []

    def ask(self, prompt, system=""):
        self.prompts.append(prompt)
        return "他沉默了片刻。"


def test_llm_receives_error_and_text_is_repaired(tmp_path):
    b = {"idx": 0, "text": "……"}
    tts, llm = _FakeTTS(), _LLM()
    dur = ca._synth_with_repair(tmp_path, 1, "s01", b, tmp_path / "x.wav", "v", tts, llm)
    assert dur == 1.0 and b["text"] == "他沉默了片刻。" and b["text_original"] == "……"
    assert "请求参数" in llm.prompts[0] and "……" in llm.prompts[0]


def test_failure_is_raised_when_repair_cannot_help(tmp_path):
    b = {"idx": 0, "text": "……"}

    class _Same:
        def ask(self, prompt, system=""):
            return "……"
    with pytest.raises(RuntimeError):
        ca._synth_with_repair(tmp_path, 1, "s01", b, tmp_path / "x.wav", "v", _FakeTTS(), _Same())
