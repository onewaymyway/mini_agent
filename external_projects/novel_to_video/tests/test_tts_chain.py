"""配音引擎链：与 novel-video-studio 一致（cosyvoice 先，失败降级 edge-tts，记录实际引擎）。"""
from pathlib import Path

import pytest

from novel_to_video.providers import tts as tts_mod
from novel_to_video.providers.factory import ProviderError, make_tts_provider


class Fake(tts_mod.TTSProvider):
    def __init__(self, name, ok=True, err="boom"):
        self.name, self.ok, self.err, self.calls = name, ok, err, 0

    def synth(self, text, out_wav, voice):
        self.calls += 1
        if not self.ok:
            raise RuntimeError(self.err)
        from novel_to_video.media import ffmpeg as mf
        return mf.write_silence_wav(out_wav, 1.5)   # 真实引擎也会落盘一个 wav


def _chain(a, b=None):
    return tts_mod.ChainTTS([a] + ([b] if b else []))


def test_default_config_is_cosyvoice_then_edge_like_the_skill():
    p = make_tts_provider("edge-tts", {"engine": "cosyvoice", "fallback": "edge-tts"})
    assert [e.name for e in p.engines] == ["cosyvoice", "edge-tts"]


def test_cosyvoice_success_does_not_touch_edge(tmp_path):
    cosy, edge = Fake("cosyvoice"), Fake("edge-tts")
    p = _chain(cosy, edge)
    assert round(p.synth("你好", tmp_path / "a.wav", "zh-CN-XiaoxiaoNeural"), 1) == 1.5
    assert p.last_engine == "cosyvoice" and edge.calls == 0 and p.last_errors == {}


def test_failure_falls_back_to_edge_and_records_reason(tmp_path):
    cosy = Fake("cosyvoice", ok=False, err="模型文件不存在")
    edge = Fake("edge-tts")
    p = _chain(cosy, edge)
    p.synth("你好", tmp_path / "a.wav", "zh-CN-XiaoxiaoNeural")
    assert p.last_engine == "edge-tts"
    assert p.last_errors == {"cosyvoice": "RuntimeError: 模型文件不存在"}


def test_all_engines_fail_message_lists_each_reason(tmp_path):
    p = _chain(Fake("cosyvoice", ok=False, err="模型缺失"), Fake("edge-tts", ok=False, err="403 forbidden"))
    with pytest.raises(RuntimeError) as e:
        p.synth("你好", tmp_path / "a.wav", "zh-CN-XiaoxiaoNeural")
    msg = str(e.value)
    assert "cosyvoice 失败（RuntimeError: 模型缺失）" in msg
    assert "edge-tts 失败（RuntimeError: 403 forbidden）" in msg


def test_engine_only_config_has_no_fallback():
    p = make_tts_provider("edge-tts", {"engine": "cosyvoice", "fallback": None})
    assert [e.name for e in p.engines] == ["cosyvoice"]


def test_edge_first_skips_cosyvoice_entirely():
    p = make_tts_provider("edge-tts", {"engine": "edge-tts", "fallback": "edge-tts"})
    assert [e.name for e in p.engines] == ["edge-tts"]


def test_unsupported_fallback_is_refused_at_config_time():
    with pytest.raises(ProviderError):
        make_tts_provider("edge-tts", {"engine": "cosyvoice", "fallback": "azure"})


def test_stub_provider_forces_silence():
    p = make_tts_provider("stub", {"engine": "cosyvoice", "fallback": "edge-tts"})
    assert isinstance(p, tts_mod.SilenceTTS)


def test_cosyvoice_maps_edge_voice_to_speaker(monkeypatch, tmp_path):
    import sys
    import types
    seen = {}
    # 不依赖真实 torch：只验证说话人映射与调用方式
    monkeypatch.setitem(sys.modules, "torch", types.ModuleType("torch"))
    monkeypatch.setitem(sys.modules, "torchaudio", types.ModuleType("torchaudio"))

    class _Model:
        sample_rate = 22050

        def inference_sft(self, text, spk):
            seen["spk"] = spk
            raise RuntimeError("stop after mapping")

    monkeypatch.setattr(tts_mod, "_cosy_model", lambda d: _Model())
    with pytest.raises(RuntimeError):
        tts_mod.CosyVoiceTTS().synth("你好", tmp_path / "a.wav", "zh-CN-YunjianNeural")
    assert seen["spk"] == "中文男"


def test_unconstructable_fallback_stays_in_chain_with_reason(monkeypatch, tmp_path):
    def _no_edge(*a, **k):
        raise RuntimeError("未安装 edge-tts：pip install edge-tts")
    monkeypatch.setattr("novel_to_video.providers.factory.EdgeTTS", _no_edge)
    p = make_tts_provider("edge-tts", {"engine": "cosyvoice", "fallback": "edge-tts"})
    assert [e.name for e in p.engines] == ["cosyvoice", "edge-tts"]
    with pytest.raises(RuntimeError) as e:
        p.engines[1].synth("你好", tmp_path / "a.wav", "x")
    assert "未安装 edge-tts" in str(e.value)


# ---- 整章流水线：降级原因与引擎使用情况要进入章节日志 ----
from test_chapter_pipeline import ChapterLLM, project  # noqa: E402,F401  (fixture)
from novel_to_video import chapter_pipeline as cp  # noqa: E402
from novel_to_video import joblog  # noqa: E402
from novel_to_video.providers.image import StubImageProvider  # noqa: E402
from novel_to_video.providers.video import StubVideoProvider  # noqa: E402


def test_chapter_log_shows_fallback_reason_and_engine_summary(project):
    from novel_to_video.steps.chapter_script import run_script
    from novel_to_video.steps.chapter_shots import run_shots
    from novel_to_video.steps.chapter_audio import run_audio
    run_script(project, 1, ChapterLLM())
    run_shots(project, 1, ChapterLLM())
    chain = tts_mod.ChainTTS([Fake("cosyvoice", ok=False, err="模型文件不存在"), Fake("edge-tts")])
    res = run_audio(project, 1, chain)
    assert res["engine_usage"] == {"edge-tts": sum(res["engine_usage"].values())}
    msgs = [x["message"] for x in joblog.tail(project, scopes=["chapter"])]
    assert any("C-6 配音降级" in m and "模型文件不存在" in m for m in msgs), msgs
    assert any("C-6 配音引擎使用" in m and "edge-tts" in m for m in msgs), msgs
