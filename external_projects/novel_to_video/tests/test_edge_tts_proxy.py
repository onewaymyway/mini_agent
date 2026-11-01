"""edge-tts 网络方式：强制直连时，调用期间不读取系统代理；结束后恢复。"""
import json

import aiohttp.client as aclient
import pytest

from novel_to_video import tool_api
from novel_to_video.providers import tts as tts_mod
from novel_to_video.providers.factory import make_tts_provider


def _fake_comm(record):
    class _Comm:
        def __init__(self, text, voice, proxy=None):
            record["proxy"] = proxy
            record["env_during_init"] = aclient.get_env_proxy_for_url

        async def save(self, path):
            record["proxy_lookup_during_save"] = aclient.get_env_proxy_for_url
            with open(path, "wb") as f:
                f.write(b"")
    return _Comm


def _stub_wav(monkeypatch):
    from novel_to_video.media import ffmpeg as mf
    monkeypatch.setattr(mf, "to_wav", lambda src, dst: dst.write_bytes(b"RIFF"))
    monkeypatch.setattr(mf, "probe_duration", lambda p: 1.0)


def test_direct_mode_disables_system_proxy_only_during_call(monkeypatch, tmp_path):
    import edge_tts
    rec = {}
    monkeypatch.setattr(edge_tts, "Communicate", _fake_comm(rec))
    _stub_wav(monkeypatch)
    orig = aclient.get_env_proxy_for_url
    tts_mod.EdgeTTS(proxy=tts_mod.PROXY_DIRECT).synth("你好", tmp_path / "a.wav", "zh-CN-XiaoxiaoNeural")
    assert rec["proxy"] is None                                   # 不指定代理
    assert rec["proxy_lookup_during_save"] is not orig            # 调用期间已替换
    with pytest.raises(LookupError):
        rec["proxy_lookup_during_save"]("https://speech.platform.bing.com")
    assert aclient.get_env_proxy_for_url is orig                  # 调用结束后已恢复


def test_default_mode_keeps_system_proxy(monkeypatch, tmp_path):
    import edge_tts
    rec = {}
    monkeypatch.setattr(edge_tts, "Communicate", _fake_comm(rec))
    _stub_wav(monkeypatch)
    orig = aclient.get_env_proxy_for_url
    tts_mod.EdgeTTS().synth("你好", tmp_path / "a.wav", "zh-CN-XiaoxiaoNeural")
    assert rec["proxy"] is None and rec["proxy_lookup_during_save"] is orig


def test_explicit_proxy_is_passed_through(monkeypatch, tmp_path):
    import edge_tts
    rec = {}
    monkeypatch.setattr(edge_tts, "Communicate", _fake_comm(rec))
    _stub_wav(monkeypatch)
    tts_mod.EdgeTTS(proxy="http://127.0.0.1:7890").synth("你好", tmp_path / "a.wav", "zh-CN-XiaoxiaoNeural")
    assert rec["proxy"] == "http://127.0.0.1:7890"


def test_restored_even_when_call_fails(monkeypatch, tmp_path):
    import edge_tts
    orig = aclient.get_env_proxy_for_url

    class _Boom:
        def __init__(self, *a, **k):
            pass

        async def save(self, path):
            raise OSError("boom")
    monkeypatch.setattr(edge_tts, "Communicate", _Boom)
    with pytest.raises(RuntimeError):
        tts_mod.EdgeTTS(proxy="direct").synth("你好", tmp_path / "a.wav", "x")
    assert aclient.get_env_proxy_for_url is orig


def test_factory_passes_proxy_from_project_config():
    p = make_tts_provider("edge-tts", {"engine": "edge-tts", "fallback": None, "edge_tts_proxy": "direct"})
    assert p.engines[0].proxy == "direct"


def test_set_tts_proxy_saves_and_validates(tmp_path):
    (tmp_path / "project.json").write_text(json.dumps({"tts": {"engine": "cosyvoice"}}), encoding="utf-8")
    assert tool_api.set_tts_proxy(str(tmp_path), "direct")["ok"]
    cfg = json.loads((tmp_path / "project.json").read_text(encoding="utf-8"))["tts"]
    assert cfg["edge_tts_proxy"] == "direct" and cfg["engine"] == "cosyvoice"
    assert tool_api.set_tts_proxy(str(tmp_path), None)["ok"]
    assert not tool_api.set_tts_proxy(str(tmp_path), "bogus")["ok"]
