"""R41：语音输入必须有可朗读的文字；空或只有标点时不调用引擎；报错时带上输入文本。"""
import pytest

from article_to_video.audio.synth import synthesize_script
from article_to_video.deck.script import slide_problems
from article_to_video.deck.voice import split_sentences
from article_to_video.planning.errors import PlanError
from article_to_video.providers.tts import describe_input, speakable_text
from test_deck_phase4 import small_project  # noqa: F401  (fixture)


@pytest.mark.parametrize("raw", ["", "   ", "。，！？", "——…", "\u200b\u200b", " 。 "])
def test_speakable_text_rejects_empty_and_punctuation_only(raw):
    assert speakable_text(raw) == ""


def test_speakable_text_keeps_real_content():
    assert speakable_text("你好。") == "你好。"
    assert speakable_text("  GPU 2024 ") == "GPU 2024"


def test_describe_input_shows_text_and_length():
    assert describe_input("") == "（空字符串）"
    assert describe_input("你好世界") == "「你好世界」（原文 4 字）"
    long = describe_input("字" * 300, limit=10)
    assert long.startswith("「" + "字" * 10 + "…」") and "300 字" in long


def test_split_drops_fragments_without_speakable_characters():
    parts = split_sentences("你好。——。 世界。。？")
    assert all(p.strip() for p in parts)
    assert not any("——" in p for p in parts)
    assert "你好" in "".join(parts) and "世界" in "".join(parts)


def test_script_check_flags_punctuation_only_narration():
    problems = slide_problems("。。。", {"id": "P01", "title": "标题", "elements": []})
    assert problems and "没有可朗读的文字" in problems[0]


class _Recorder:
    name = "rec"
    last_engine = "rec"

    def __init__(self, fail=None):
        self.calls, self.fail = [], fail

    def synth(self, text, out, voice, rate="+0%"):
        self.calls.append(text)
        if self.fail:
            raise RuntimeError(self.fail)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"")
        return 1.0


def _script(text):
    return {"schema": 1, "part": "P01", "title": "t", "confirmed": True,
            "beats": [{"id": "B01", "role": "claim", "pace": "normal", "source_refs": [],
                       "pause_after_ms": 0, "sentences": [{"text": text, "tts": None, "emphasis": [], "quote": False}]}]}


def test_empty_sentence_never_reaches_the_engine(small_project):
    rec = _Recorder()
    with pytest.raises(PlanError) as ei:
        synthesize_script(small_project, "P01", _script("。。。"), rec, force=True)
    assert rec.calls == []                                   # 没有调用引擎
    assert "没有可朗读的文字" in str(ei.value) and "。。。" in str(ei.value)


def test_engine_failure_message_includes_the_input(small_project):
    rec = _Recorder(fail="boom")
    with pytest.raises(RuntimeError) as ei:
        synthesize_script(small_project, "P01", _script("你好世界，这是一句话。"), rec, force=True)
    msg = str(ei.value)
    assert "B01.s01" in msg and "boom" in msg and "你好世界，这是一句话。" in msg
