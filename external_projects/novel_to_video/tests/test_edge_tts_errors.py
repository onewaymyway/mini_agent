"""edge-tts 的 403 / 网络错误要给出可操作的说明，并带上阶段名。"""
from pathlib import Path

import pytest

from novel_to_video.providers import tts as tts_mod

ERR_403 = ("403, message='Invalid response status', url='wss://speech.platform.bing.com/consumer/speech/"
           "synthesize/readaloud/edge/v1?TrustedClientToken=abc'")


class _Fail:
    def __init__(self, text, voice, proxy=None):   # 与真实 edge_tts.Communicate 的签名一致
        pass

    async def save(self, path):
        raise RuntimeError(ERR_403)


def test_403_message_tells_user_to_upgrade(monkeypatch, tmp_path):
    import edge_tts
    monkeypatch.setattr(edge_tts, "Communicate", _Fail)
    with pytest.raises(RuntimeError) as e:
        tts_mod.EdgeTTS().synth("你好", tmp_path / "a.wav", "zh-CN-XiaoxiaoNeural")
    msg = str(e.value)
    assert "403" in msg and "pip install -U edge-tts" in msg
    assert "原始错误" in msg and "Invalid response status" in msg


def test_dns_failure_is_a_network_message(monkeypatch, tmp_path):
    import edge_tts

    class _Dns(_Fail):
        async def save(self, path):
            raise OSError("Cannot connect to host speech.platform.bing.com:443 [Name or service not known]")
    monkeypatch.setattr(edge_tts, "Communicate", _Dns)
    with pytest.raises(RuntimeError) as e:
        tts_mod.EdgeTTS().synth("你好", tmp_path / "a.wav", "zh-CN-XiaoxiaoNeural")
    assert "网络" in str(e.value) and "Name or service not known" in str(e.value)


def test_unknown_error_keeps_raw_text(monkeypatch, tmp_path):
    import edge_tts

    class _Other(_Fail):
        async def save(self, path):
            raise ValueError("weird")
    monkeypatch.setattr(edge_tts, "Communicate", _Other)
    with pytest.raises(RuntimeError) as e:
        tts_mod.EdgeTTS().synth("你好", tmp_path / "a.wav", "zh-CN-XiaoxiaoNeural")
    assert "ValueError: weird" in str(e.value)
